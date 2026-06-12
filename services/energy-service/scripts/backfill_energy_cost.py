from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import (
    EnergyDeviceDay,
    EnergyDeviceMonth,
    EnergyFleetDay,
    EnergyFleetMonth,
    EnergyReconcileAudit,
    EnergyReconcileRun,
)
from app.services.reconciliation_apply import ReconciliationApplyService
from services.shared.tariff_client import resolve_tenant_tariff
from services.shared.tenant_context import build_internal_headers

logger = logging.getLogger("energy_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _local_today() -> date:
    return datetime.now(ZoneInfo(settings.PLATFORM_TIMEZONE)).date()


def _day_end_utc(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=timezone.utc)


class TariffRateCache:
    def __init__(self, reporting_base_url: str):
        self._reporting_base_url = reporting_base_url
        self._tenant_versions: dict[str, list[dict]] = {}

    async def load_tenant_history(self, client: httpx.AsyncClient, tenant_id: str) -> list[dict]:
        if tenant_id in self._tenant_versions:
            return self._tenant_versions[tenant_id]
        base = self._reporting_base_url.rstrip("/")
        headers = build_internal_headers("energy-backfill", tenant_id)
        for path in ("/api/v1/settings/tariff/history", "/api/reports/tariff/history"):
            try:
                resp = await client.get(f"{base}{path}", headers=headers, timeout=10.0)
                if resp.status_code == 200:
                    payload = resp.json()
                    versions = payload.get("versions", []) if isinstance(payload, dict) else []
                    self._tenant_versions[tenant_id] = versions
                    return versions
            except Exception:
                continue
        self._tenant_versions[tenant_id] = []
        return []

    def resolve_local(self, tenant_id: str, day: date) -> dict | None:
        versions = self._tenant_versions.get(tenant_id, [])
        if not versions:
            return None
        effective_at = _day_end_utc(day)
        best = None
        for v in versions:
            try:
                start_str = v.get("effective_from") or v.get("effective_start_at")
                end_str = v.get("effective_end_at")
                if not start_str:
                    continue
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if start_dt > effective_at:
                    continue
                if end_str:
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    if end_dt <= effective_at:
                        continue
                if best is None or start_dt > datetime.fromisoformat(
                    (best.get("effective_from") or best.get("effective_start_at") or "").replace("Z", "+00:00")
                ):
                    best = v
            except Exception:
                continue
        if best is not None:
            rate = best.get("rate")
            if rate is not None:
                return {
                    "rate": float(rate),
                    "source": "tenant_tariff_versions_local",
                    "version_id": best.get("id"),
                }
        return None

    async def resolve_http(
        self, client: httpx.AsyncClient, tenant_id: str, day: date
    ) -> dict | None:
        base = self._reporting_base_url.rstrip("/")
        if not base:
            return None
        effective_at = _day_end_utc(day)
        try:
            payload = await resolve_tenant_tariff(
                client, base, tenant_id, service_name="energy-backfill", effective_at=effective_at
            )
            rate = payload.get("rate")
            if rate is not None and float(rate) > 0:
                return {
                    "rate": float(rate),
                    "source": payload.get("source", "http_resolve"),
                    "version_id": payload.get("version_id"),
                }
        except Exception:
            pass
        return None


async def _detect_drifted_rows(
    session: AsyncSession,
    cutoff_date: date,
    tenant_id: str | None,
) -> list[EnergyDeviceDay]:
    conditions_primary = and_(
        EnergyDeviceDay.energy_kwh > 0,
        EnergyDeviceDay.energy_cost_inr == 0.0,
        EnergyDeviceDay.day < cutoff_date,
    )
    conditions_secondary = and_(
        EnergyDeviceDay.loss_kwh > 0,
        EnergyDeviceDay.loss_cost_inr == 0.0,
        EnergyDeviceDay.energy_cost_inr > 0,
        EnergyDeviceDay.day < cutoff_date,
    )
    stmt = select(EnergyDeviceDay).where(
        conditions_primary | conditions_secondary
    )
    if tenant_id:
        stmt = stmt.where(EnergyDeviceDay.tenant_id == tenant_id)
    stmt = stmt.order_by(EnergyDeviceDay.tenant_id, EnergyDeviceDay.day, EnergyDeviceDay.device_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _resolve_rate(
    tariff_cache: TariffRateCache,
    client: httpx.AsyncClient,
    tenant_id: str,
    day: date,
) -> dict | None:
    local_result = tariff_cache.resolve_local(tenant_id, day)
    if local_result is not None:
        return local_result
    http_result = await tariff_cache.resolve_http(client, tenant_id, day)
    if http_result is not None:
        return http_result
    return None


async def _guarded_update_row(
    session: AsyncSession,
    row: EnergyDeviceDay,
    energy_rate: float,
    loss_rate: float,
    dry_run: bool,
) -> dict:
    old_energy_cost = float(row.energy_cost_inr or 0.0)
    old_loss_cost = float(row.loss_cost_inr or 0.0)
    new_energy_cost = old_energy_cost
    new_loss_cost = old_loss_cost
    needs_energy_repair = old_energy_cost == 0.0 and float(row.energy_kwh or 0.0) > 0
    needs_loss_repair = old_loss_cost == 0.0 and float(row.loss_kwh or 0.0) > 0
    if not needs_energy_repair and not needs_loss_repair:
        return {
            "row_id": row.id,
            "tenant_id": row.tenant_id,
            "device_id": row.device_id,
            "day": row.day.isoformat(),
            "needs_repair": False,
            "reason": "guard_no_match",
        }
    if needs_energy_repair:
        new_energy_cost = round(float(row.energy_kwh or 0.0) * energy_rate, 6)
    if needs_loss_repair:
        new_loss_cost = round(float(row.loss_kwh or 0.0) * loss_rate, 6)
    try:
        old_flags = json.loads(row.quality_flags or "[]")
    except Exception:
        old_flags = []
    new_flags = sorted(set(old_flags) | {"cost_backfill_applied"})
    repair_info = {
        "row_id": row.id,
        "tenant_id": row.tenant_id,
        "device_id": row.device_id,
        "day": row.day.isoformat(),
        "needs_repair": True,
        "old_energy_cost_inr": old_energy_cost,
        "new_energy_cost_inr": new_energy_cost,
        "old_loss_cost_inr": old_loss_cost,
        "new_loss_cost_inr": new_loss_cost,
        "applied_energy_rate": energy_rate,
        "applied_loss_rate": loss_rate,
        "quality_flags_before": old_flags,
        "quality_flags_after": new_flags,
        "dry_run": dry_run,
    }
    if dry_run:
        return repair_info
    stmt = (
        update(EnergyDeviceDay)
        .where(EnergyDeviceDay.id == row.id)
        .where(
            (EnergyDeviceDay.energy_cost_inr == 0.0) | (EnergyDeviceDay.loss_cost_inr == 0.0)
        )
        .values(
            energy_cost_inr=func.round(
                EnergyDeviceDay.energy_cost_inr
                + (new_energy_cost - old_energy_cost),
                6,
            ),
            loss_cost_inr=func.round(
                EnergyDeviceDay.loss_cost_inr
                + (new_loss_cost - old_loss_cost),
                6,
            ),
            quality_flags=json.dumps(new_flags),
            version=EnergyDeviceDay.version + 1,
        )
    )
    await session.execute(stmt)
    repair_info["guard_matched"] = True
    return repair_info


async def _write_audit_entry(
    session: AsyncSession,
    run_id: str,
    row: EnergyDeviceDay,
    repair_info: dict,
    tariff_info: dict,
) -> None:
    old_metrics = {
        "energy_kwh": float(row.energy_kwh or 0.0),
        "energy_cost_inr": repair_info.get("old_energy_cost_inr", 0.0),
        "loss_kwh": float(row.loss_kwh or 0.0),
        "loss_cost_inr": repair_info.get("old_loss_cost_inr", 0.0),
    }
    new_metrics = {
        "energy_kwh": float(row.energy_kwh or 0.0),
        "energy_cost_inr": repair_info.get("new_energy_cost_inr", 0.0),
        "loss_kwh": float(row.loss_kwh or 0.0),
        "loss_cost_inr": repair_info.get("new_loss_cost_inr", 0.0),
    }
    audit = EnergyReconcileAudit(
        run_id=run_id,
        tenant_id=row.tenant_id,
        device_id=row.device_id,
        day=row.day,
        period_type="device_day",
        period_start=datetime.combine(row.day, time.min, tzinfo=timezone.utc),
        period_end=datetime.combine(row.day, time.max, tzinfo=timezone.utc),
        expected_energy_kwh=float(row.energy_kwh or 0.0),
        projected_energy_kwh=float(row.energy_kwh or 0.0),
        drift_kwh=0.0,
        repaired=True,
        old_metrics=old_metrics,
        new_metrics=new_metrics,
        old_quality_flags={"quality_flags": repair_info.get("quality_flags_before", [])},
        new_quality_flags={
            "applied_rate": tariff_info.get("rate"),
            "tariff_source": tariff_info.get("source"),
            "tariff_version_id": tariff_info.get("version_id"),
            "backfill_run_id": run_id,
        },
        algorithm_version="cost_backfill_v1",
        status="applied",
        applied_by="svc:energy-backfill",
        applied_at=datetime.now(timezone.utc),
    )
    session.add(audit)
    await session.flush()


async def _rebuild_aggregates(
    session: AsyncSession,
    tenant_id: str,
    affected_device_months: set[tuple[str, str, date]],
    affected_fleet_days: set[tuple[str, date]],
    affected_fleet_months: set[tuple[str, date]],
    dry_run: bool,
) -> None:
    if dry_run:
        return
    service = ReconciliationApplyService(session)
    for tenant, device_id, month_bucket in sorted(affected_device_months):
        await service._rebuild_device_month(
            tenant_id=tenant, device_id=device_id, month_bucket=month_bucket
        )
    for tenant, day in sorted(affected_fleet_days):
        await service._rebuild_fleet_day(tenant_id=tenant, day=day)
    for tenant, month_bucket in sorted(affected_fleet_months):
        await service._rebuild_fleet_month(tenant_id=tenant, month_bucket=month_bucket)


async def _write_run_record(
    session: AsyncSession,
    run_id: str,
    tenant_id: str | None,
    start_date: date | None,
    end_date: date | None,
    candidate_count: int,
    suspicious_count: int,
) -> None:
    run = EnergyReconcileRun(
        run_id=run_id,
        tenant_id=tenant_id,
        status="completed",
        requested_start=start_date or date.today(),
        requested_end=end_date or date.today(),
        requested_by="svc:energy-backfill",
        candidate_count=candidate_count,
        suspicious_count=suspicious_count,
        completed_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()


async def run_backfill(
    session: AsyncSession,
    *,
    tenant_id: str | None = None,
    exclude_recent_days: int = 1,
    dry_run: bool = False,
    output_path: str | None = None,
) -> dict:
    cutoff_date = _local_today() - timedelta(days=exclude_recent_days)
    reporting_base_url = settings.REPORTING_SERVICE_BASE_URL

    run_id = f"backfill-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"

    drifted_rows = await _detect_drifted_rows(session, cutoff_date, tenant_id)
    if not drifted_rows:
        logger.info("No drifted rows found. Exiting.")
        return {"run_id": run_id, "status": "no_drift", "repaired": 0, "skipped": 0}

    logger.info("Detected %d drifted rows (cutoff=%s)", len(drifted_rows), cutoff_date.isoformat())

    tariff_cache = TariffRateCache(reporting_base_url)
    tenant_ids = sorted({r.tenant_id for r in drifted_rows})

    async with httpx.AsyncClient(timeout=15.0) as client:
        for tid in tenant_ids:
            await tariff_cache.load_tenant_history(client, tid)

        jsonl_lines: list[str] = []
        repaired_count = 0
        skipped_count = 0
        no_rate_count = 0
        affected_device_months: set[tuple[str, str, date]] = set()
        affected_fleet_days: set[tuple[str, date]] = set()
        affected_fleet_months: set[tuple[str, date]] = set()

        grouped: dict[str, list[EnergyDeviceDay]] = defaultdict(list)
        for r in drifted_rows:
            grouped[r.tenant_id].append(r)

        for tid in tenant_ids:
            tenant_rows = grouped[tid]
            for row in tenant_rows:
                rate_info = await _resolve_rate(tariff_cache, client, tid, row.day)
                if rate_info is None or rate_info.get("rate", 0) <= 0:
                    repair_info = {
                        "row_id": row.id,
                        "tenant_id": row.tenant_id,
                        "device_id": row.device_id,
                        "day": row.day.isoformat(),
                        "needs_repair": False,
                        "reason": "no_rate_available",
                    }
                    jsonl_lines.append(json.dumps(repair_info))
                    no_rate_count += 1
                    continue

                rate = rate_info["rate"]
                repair_info = await _guarded_update_row(session, row, rate, rate, dry_run)
                jsonl_lines.append(json.dumps(repair_info))

                if repair_info.get("needs_repair"):
                    repaired_count += 1
                    if not dry_run:
                        await _write_audit_entry(session, run_id, row, repair_info, rate_info)
                        month_bucket = row.day.replace(day=1)
                        affected_device_months.add((tid, row.device_id, month_bucket))
                        affected_fleet_days.add((tid, row.day))
                        affected_fleet_months.add((tid, month_bucket))
                else:
                    skipped_count += 1

            if not dry_run and affected_device_months:
                tenant_dev_months = {m for t, _, m in affected_device_months if t == tid}
                tenant_fleet_days = {d for t, d in affected_fleet_days if t == tid}
                tenant_fleet_months = {m for t, m in affected_fleet_months if t == tid}
                await _rebuild_aggregates(
                    session,
                    tid,
                    {(t, d, m) for t, d, m in affected_device_months if t == tid},
                    {(t, d) for t, d in affected_fleet_days if t == tid},
                    {(t, m) for t, m in affected_fleet_months if t == tid},
                    dry_run,
                )
                await session.commit()
                logger.info(
                    "Committed tenant %s: %d repaired, %d skipped, %d no-rate",
                    tid,
                    sum(1 for l in jsonl_lines if json.loads(l).get("needs_repair") and json.loads(l).get("tenant_id") == tid),
                    sum(1 for l in jsonl_lines if not json.loads(l).get("needs_repair") and json.loads(l).get("tenant_id") == tid and json.loads(l).get("reason") != "no_rate_available"),
                    sum(1 for l in jsonl_lines if json.loads(l).get("reason") == "no_rate_available" and json.loads(l).get("tenant_id") == tid),
                )

        if output_path:
            with open(output_path, "w") as f:
                for line in jsonl_lines:
                    f.write(line + "\n")
            logger.info("Wrote audit to %s", output_path)

    if not dry_run:
        async with AsyncSessionLocal() as run_session:
            await _write_run_record(
                run_session,
                run_id,
                tenant_id,
                min(r.day for r in drifted_rows) if drifted_rows else None,
                max(r.day for r in drifted_rows) if drifted_rows else None,
                repaired_count + skipped_count + no_rate_count,
                repaired_count,
            )
            await run_session.commit()

    return {
        "run_id": run_id,
        "status": "dry_run_complete" if dry_run else "complete",
        "repaired": repaired_count,
        "skipped": skipped_count,
        "no_rate": no_rate_count,
        "cutoff_date": cutoff_date.isoformat(),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-time backfill for energy_device_day rows with zero cost.")
    parser.add_argument("--tenant-id", default=None, help="Scope to a single tenant")
    parser.add_argument("--exclude-recent-days", type=int, default=1, help="Exclude most recent N days (default 1 = exclude today)")
    parser.add_argument("--dry-run", action="store_true", help="Detect and report only, no writes")
    parser.add_argument("--output", default=None, help="Path for JSONL audit output")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    async with AsyncSessionLocal() as session:
        result = await run_backfill(
            session,
            tenant_id=args.tenant_id,
            exclude_recent_days=args.exclude_recent_days,
            dry_run=args.dry_run,
            output_path=args.output,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
