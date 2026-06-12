from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EnergyDeviceDay, EnergyDeviceMonth, EnergyFleetDay, EnergyFleetMonth
from app.repositories.reconciliation_audit_repository import ReconciliationAuditRepository, ReconciliationRunRepository
from app.services.reconciliation_preview import ReconciliationPreviewService
from services.shared.tariff_client import resolve_tenant_tariff
from services.shared.tenant_context import build_internal_headers


class ReconciliationApplyService:
    """Apply reviewed corrections.

    Cost semantics:
    - `energy_kwh`/`loss_kwh` are the canonical aggregate truth.
    - Aggregate `*_cost_inr` fields are persisted, tariff-aware convenience values.
    - Device-day costs are recomputed from the historical tariff policy during apply.
    - Month/fleet costs are deterministic rollups that sum child day costs.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._audit_repo = ReconciliationAuditRepository(session)
        self._run_repo = ReconciliationRunRepository(session)

    async def approve_candidate(self, audit_id: int, *, actor: str) -> dict:
        audit = await self._require_audit(audit_id)
        if audit.status not in {"recomputed", "detected"}:
            raise ValueError("Only detected or recomputed candidates can be approved")
        await self._audit_repo.update_status(
            audit_id,
            status="approved",
            approved_by=actor,
            approved_at=datetime.now(timezone.utc),
        )
        await self._session.commit()
        return {"audit_id": audit_id, "status": "approved", "approved_by": actor}

    async def reject_candidate(self, audit_id: int, *, actor: str, reason: str) -> dict:
        if not str(reason).strip():
            raise ValueError("rejection reason is required")
        audit = await self._require_audit(audit_id)
        if audit.status not in {"recomputed", "detected", "approved"}:
            raise ValueError("Only detected, recomputed, or approved candidates can be rejected")
        await self._audit_repo.update_status(
            audit_id,
            status="rejected",
            rejected_by=actor,
            rejected_at=datetime.now(timezone.utc),
            rejection_reason=reason.strip(),
            repaired=False,
        )
        await self._session.commit()
        return {"audit_id": audit_id, "status": "rejected", "rejected_by": actor, "reason": reason.strip()}

    async def apply_candidate(self, audit_id: int, *, actor: str) -> dict:
        audit = await self._require_audit(audit_id)
        if audit.status != "approved":
            raise ValueError("candidate must be approved before apply")

        day = audit.day
        device_id = str(audit.device_id)
        tenant_id = str(getattr(audit, "tenant_id", "") or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required to apply a reconciliation candidate")
        new_metrics = dict(audit.new_metrics or {})
        row = (
            await self._session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == tenant_id,
                    EnergyDeviceDay.device_id == device_id,
                    EnergyDeviceDay.day == day,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = EnergyDeviceDay(tenant_id=tenant_id, device_id=device_id, day=day)
            self._session.add(row)
        resolved_tariff = await self._resolve_historical_tariff(audit)
        applied_rate = float(resolved_tariff.get("rate") or 0.0)

        row.energy_kwh = float(new_metrics.get("energy_kwh") or 0.0)
        row.idle_kwh = float(new_metrics.get("idle_kwh") or 0.0)
        row.offhours_kwh = float(new_metrics.get("offhours_kwh") or 0.0)
        row.overconsumption_kwh = float(new_metrics.get("overconsumption_kwh") or 0.0)
        row.loss_kwh = float(new_metrics.get("loss_kwh") or 0.0)
        row.energy_cost_inr = round(float(row.energy_kwh or 0.0) * applied_rate, 6)
        row.loss_cost_inr = round(float(row.loss_kwh or 0.0) * applied_rate, 6)
        row.quality_flags = json.dumps(self._applied_quality_flags(row.quality_flags, audit.run_id))
        row.version = int(row.version or 0) + 1

        month_bucket = day.replace(day=1)
        await self._rebuild_device_month(tenant_id=tenant_id, device_id=device_id, month_bucket=month_bucket)
        await self._rebuild_fleet_day(tenant_id=tenant_id, day=day)
        await self._rebuild_fleet_month(tenant_id=tenant_id, month_bucket=month_bucket)

        created_revisions = await self._request_report_revisions(audit)
        new_flags = dict(audit.new_quality_flags or {})
        new_flags["created_report_revisions"] = created_revisions
        new_flags["applied_tariff"] = {
            "rate": applied_rate,
            "source": resolved_tariff.get("source"),
            "version_id": resolved_tariff.get("version_id"),
            "effective_start_at": resolved_tariff.get("effective_start_at"),
            "effective_end_at": resolved_tariff.get("effective_end_at"),
        }
        await self._audit_repo.update_status(
            audit_id,
            status="applied",
            applied_by=actor,
            applied_at=datetime.now(timezone.utc),
            repaired=True,
            new_quality_flags=new_flags,
        )
        await self._session.commit()
        return {
            "audit_id": audit_id,
            "status": "applied",
            "applied_by": actor,
            "device_id": device_id,
            "day": day.isoformat(),
            "created_report_revisions": created_revisions,
        }

    async def sync_device_days_from_telemetry(
        self,
        *,
        tenant_id: str,
        device_ids: list[str],
        day: date,
        actor: str,
        live_metrics: list[dict] | None = None,
    ) -> dict:
        normalized_ids = sorted({str(device_id).strip() for device_id in device_ids if str(device_id).strip()})
        if not tenant_id:
            raise ValueError("tenant_id is required to sync device days")
        if not normalized_ids:
            return {"tenant_id": tenant_id, "day": day.isoformat(), "updated": 0, "skipped": []}

        preview = ReconciliationPreviewService(self._session)
        resolved_tariff = await self._resolve_tariff_for_day(tenant_id=tenant_id, day=day)
        applied_rate = float(resolved_tariff.get("rate") or 0.0)
        live_metrics_by_device = {
            str(item.get("device_id") or "").strip(): dict(item)
            for item in (live_metrics or [])
            if str(item.get("device_id") or "").strip()
        }

        updated: list[dict] = []
        skipped: list[dict] = []
        month_bucket = day.replace(day=1)

        for device_id in normalized_ids:
            if live_metrics_by_device:
                metrics = live_metrics_by_device.get(device_id)
                if metrics is None:
                    skipped.append({"device_id": device_id, "reason": "no_live_metrics"})
                    continue
            else:
                rows = await preview._fetch_telemetry_rows(tenant_id=tenant_id, device_id=device_id, day=day)
                if not rows:
                    skipped.append({"device_id": device_id, "reason": "no_telemetry_rows"})
                    continue
                recomputed = await preview._recompute_metrics(device_id=device_id, tenant_id=tenant_id, day=day, rows=rows)
                metrics = dict(recomputed.get("metrics") or {})
            row = (
                await self._session.execute(
                    select(EnergyDeviceDay).where(
                        EnergyDeviceDay.tenant_id == tenant_id,
                        EnergyDeviceDay.device_id == device_id,
                        EnergyDeviceDay.day == day,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = EnergyDeviceDay(tenant_id=tenant_id, device_id=device_id, day=day)
                self._session.add(row)

            row.energy_kwh = float(metrics.get("energy_kwh") or 0.0)
            row.idle_kwh = float(metrics.get("idle_kwh") or 0.0)
            row.offhours_kwh = float(metrics.get("offhours_kwh") or 0.0)
            row.overconsumption_kwh = float(metrics.get("overconsumption_kwh") or 0.0)
            row.loss_kwh = float(metrics.get("loss_kwh") or 0.0)
            row.energy_cost_inr = round(float(row.energy_kwh or 0.0) * applied_rate, 6)
            row.loss_cost_inr = round(float(row.loss_kwh or 0.0) * applied_rate, 6)
            merged_flags = set(self._aggregate_quality_flags([row.quality_flags]))
            merged_flags.add("current_day_sync_applied")
            merged_flags.add(f"current_day_sync_actor:{actor}")
            row.quality_flags = json.dumps(sorted(merged_flags))
            row.version = int(row.version or 0) + 1

            updated.append(
                {
                    "device_id": device_id,
                    "energy_kwh": round(float(row.energy_kwh or 0.0), 6),
                    "loss_kwh": round(float(row.loss_kwh or 0.0), 6),
                }
            )

        if updated:
            for device_id in normalized_ids:
                if any(item["device_id"] == device_id for item in updated):
                    await self._rebuild_device_month(tenant_id=tenant_id, device_id=device_id, month_bucket=month_bucket)
            await self._rebuild_fleet_day(tenant_id=tenant_id, day=day)
            await self._rebuild_fleet_month(tenant_id=tenant_id, month_bucket=month_bucket)

        await self._session.commit()
        return {
            "tenant_id": tenant_id,
            "day": day.isoformat(),
            "updated": len(updated),
            "devices": updated,
            "skipped": skipped,
        }

    async def _rebuild_device_month(self, *, tenant_id: str, device_id: str, month_bucket: date) -> None:
        month_end = (month_bucket.replace(day=28) + timedelta(days=4)).replace(day=1)
        rows = (
            await self._session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == tenant_id,
                    EnergyDeviceDay.device_id == device_id,
                    EnergyDeviceDay.day >= month_bucket,
                    EnergyDeviceDay.day < month_end,
                )
            )
        ).scalars().all()
        month = (
            await self._session.execute(
                select(EnergyDeviceMonth).where(
                    EnergyDeviceMonth.tenant_id == tenant_id,
                    EnergyDeviceMonth.device_id == device_id,
                    EnergyDeviceMonth.month == month_bucket,
                )
            )
        ).scalar_one_or_none()
        if month is None:
            month = EnergyDeviceMonth(tenant_id=tenant_id, device_id=device_id, month=month_bucket)
            self._session.add(month)

        month.energy_kwh = round(sum(float(r.energy_kwh or 0.0) for r in rows), 6)
        month.energy_cost_inr = round(sum(float(r.energy_cost_inr or 0.0) for r in rows), 6)
        month.idle_kwh = round(sum(float(r.idle_kwh or 0.0) for r in rows), 6)
        month.offhours_kwh = round(sum(float(r.offhours_kwh or 0.0) for r in rows), 6)
        month.overconsumption_kwh = round(sum(float(r.overconsumption_kwh or 0.0) for r in rows), 6)
        month.loss_kwh = round(sum(float(r.loss_kwh or 0.0) for r in rows), 6)
        month.loss_cost_inr = round(sum(float(r.loss_cost_inr or 0.0) for r in rows), 6)
        month.quality_flags = json.dumps(self._aggregate_quality_flags(r.quality_flags for r in rows))
        month.version = int(month.version or 0) + 1

    async def _rebuild_fleet_day(self, *, tenant_id: str, day: date) -> None:
        rows = (
            await self._session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == tenant_id,
                    EnergyDeviceDay.day == day,
                )
            )
        ).scalars().all()
        fleet = (
            await self._session.execute(
                select(EnergyFleetDay).where(
                    EnergyFleetDay.tenant_id == tenant_id,
                    EnergyFleetDay.day == day,
                )
            )
        ).scalar_one_or_none()
        if fleet is None:
            fleet = EnergyFleetDay(tenant_id=tenant_id, day=day)
            self._session.add(fleet)

        fleet.energy_kwh = round(sum(float(r.energy_kwh or 0.0) for r in rows), 6)
        fleet.energy_cost_inr = round(sum(float(r.energy_cost_inr or 0.0) for r in rows), 6)
        fleet.idle_kwh = round(sum(float(r.idle_kwh or 0.0) for r in rows), 6)
        fleet.offhours_kwh = round(sum(float(r.offhours_kwh or 0.0) for r in rows), 6)
        fleet.overconsumption_kwh = round(sum(float(r.overconsumption_kwh or 0.0) for r in rows), 6)
        fleet.loss_kwh = round(sum(float(r.loss_kwh or 0.0) for r in rows), 6)
        fleet.loss_cost_inr = round(sum(float(r.loss_cost_inr or 0.0) for r in rows), 6)
        fleet.version = int(fleet.version or 0) + 1

    async def _rebuild_fleet_month(self, *, tenant_id: str, month_bucket: date) -> None:
        month_end = (month_bucket.replace(day=28) + timedelta(days=4)).replace(day=1)
        rows = (
            await self._session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == tenant_id,
                    EnergyDeviceDay.day >= month_bucket,
                    EnergyDeviceDay.day < month_end,
                )
            )
        ).scalars().all()
        fleet = (
            await self._session.execute(
                select(EnergyFleetMonth).where(
                    EnergyFleetMonth.tenant_id == tenant_id,
                    EnergyFleetMonth.month == month_bucket,
                )
            )
        ).scalar_one_or_none()
        if fleet is None:
            fleet = EnergyFleetMonth(tenant_id=tenant_id, month=month_bucket)
            self._session.add(fleet)

        fleet.energy_kwh = round(sum(float(r.energy_kwh or 0.0) for r in rows), 6)
        fleet.energy_cost_inr = round(sum(float(r.energy_cost_inr or 0.0) for r in rows), 6)
        fleet.idle_kwh = round(sum(float(r.idle_kwh or 0.0) for r in rows), 6)
        fleet.offhours_kwh = round(sum(float(r.offhours_kwh or 0.0) for r in rows), 6)
        fleet.overconsumption_kwh = round(sum(float(r.overconsumption_kwh or 0.0) for r in rows), 6)
        fleet.loss_kwh = round(sum(float(r.loss_kwh or 0.0) for r in rows), 6)
        fleet.loss_cost_inr = round(sum(float(r.loss_cost_inr or 0.0) for r in rows), 6)
        fleet.version = int(fleet.version or 0) + 1

    async def _request_report_revisions(self, audit) -> list[dict]:
        tenant_id = getattr(audit, "tenant_id", None)
        report_entries = []
        if isinstance(audit.new_quality_flags, dict):
            report_entries = list(audit.new_quality_flags.get("related_reports") or [])
        if not tenant_id or not report_entries:
            return []
        base = (settings.REPORTING_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            return []
        revisions: list[dict] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for entry in report_entries:
                report_id = str((entry or {}).get("report_id") or "").strip()
                if not report_id:
                    continue
                response = await client.post(
                    f"{base}/api/reports/internal/revisions/corrected",
                    headers=build_internal_headers("energy-service", tenant_id),
                    json={
                        "report_id": report_id,
                        "revision_reason": "corrected_after_reconciliation_apply",
                        "generated_from_reconciliation_run_id": audit.run_id,
                    },
                )
                if response.status_code != 200:
                    revisions.append({"source_report_id": report_id, "status": "failed"})
                    continue
                payload = response.json()
                revisions.append(
                    {
                        "source_report_id": report_id,
                        "new_report_id": payload.get("new_report_id"),
                        "status": payload.get("status", "queued"),
                        "tariff_version_id": payload.get("tariff_version_id"),
                    }
                )
        return revisions

    async def _require_audit(self, audit_id: int):
        audit = await self._audit_repo.get_item(audit_id)
        if audit is None:
            raise ValueError("reconciliation candidate not found")
        return audit

    async def _resolve_historical_tariff(self, audit) -> dict:
        tenant_id = str(getattr(audit, "tenant_id", "") or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required to apply a reconciliation candidate")
        base = (settings.REPORTING_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            raise ValueError("REPORTING_SERVICE_BASE_URL is required to resolve historical tariff")
        effective_at = datetime.combine(audit.day, time.max)
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = await resolve_tenant_tariff(
                client,
                base,
                tenant_id,
                service_name="energy-service",
                effective_at=effective_at,
            )
        return payload

    async def _resolve_tariff_for_day(self, *, tenant_id: str, day: date) -> dict:
        base = (settings.REPORTING_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            raise ValueError("REPORTING_SERVICE_BASE_URL is required to resolve historical tariff")
        effective_at = datetime.combine(day, time.max)
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = await resolve_tenant_tariff(
                client,
                base,
                tenant_id,
                service_name="energy-service",
                effective_at=effective_at,
            )
        return payload

    @staticmethod
    def _aggregate_quality_flags(flag_values) -> list[str]:
        merged: set[str] = set()
        for raw in flag_values:
            try:
                items = json.loads(raw or "[]")
            except Exception:
                items = []
            if isinstance(items, list):
                merged.update(str(item) for item in items if str(item).strip())
        return sorted(merged)

    @staticmethod
    def _applied_quality_flags(existing: str | None, run_id: str | None) -> list[str]:
        merged = set(ReconciliationApplyService._aggregate_quality_flags([existing]))
        merged.add("reconciled_applied")
        if run_id:
            merged.add(f"reconciliation_run:{run_id}")
        return sorted(merged)
