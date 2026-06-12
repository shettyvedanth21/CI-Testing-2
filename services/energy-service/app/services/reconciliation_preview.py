from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EnergyDeviceDay
from app.repositories.reconciliation_audit_repository import ReconciliationAuditRepository, ReconciliationRunRepository
from app.services.device_meta import meta_cache
from app.services.energy_engine import EnergyEngine, _get_platform_tz
from app.services.internal_http import internal_get
from services.shared.energy_accounting import aggregate_window
from services.shared.telemetry_normalization import (
    INTERVAL_ENERGY_ALGORITHM_VERSION,
    NORMALIZATION_VERSION,
    build_device_power_config,
    compute_interval_energy_delta,
    normalize_telemetry_sample,
)


TELEMETRY_FIELDS = [
    "energy_kwh",
    "power",
    "power_w",
    "active_power",
    "current",
    "voltage",
    "power_factor",
]

SUSPICIOUS_QUALITY_FLAGS = {
    "counter_implausible_vs_power",
    "counter_implausible_vs_hard_max",
    "counter_reset_detected",
    "counter_reverse_seen",
    "counter_gap_exceeded",
    "long_gap_fallback_blocked",
}


@dataclass(frozen=True)
class ReconciliationPreviewRequest:
    start_date: date
    end_date: date
    tenant_id: str | None = None
    device_ids: list[str] | None = None
    requested_by: str | None = None
    affected_window_start: date | None = None
    affected_window_end: date | None = None
    min_drift_kwh: float = 0.25
    min_drift_ratio: float = 0.25
    include_report_intersections: bool = True


class ReconciliationPreviewService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._audit_repo = ReconciliationAuditRepository(session)
        self._run_repo = ReconciliationRunRepository(session)
        self._engine = EnergyEngine(session)

    async def preview(self, request: ReconciliationPreviewRequest) -> dict:
        run_id = uuid4().hex
        scope_filters = {
            "device_ids": list(request.device_ids or []),
            "affected_window_start": request.affected_window_start.isoformat()
            if request.affected_window_start
            else None,
            "affected_window_end": request.affected_window_end.isoformat() if request.affected_window_end else None,
            "min_drift_kwh": request.min_drift_kwh,
            "min_drift_ratio": request.min_drift_ratio,
            "include_report_intersections": request.include_report_intersections,
        }
        await self._run_repo.create_run(
            run_id=run_id,
            tenant_id=request.tenant_id,
            status="created",
            requested_start=request.start_date,
            requested_end=request.end_date,
            requested_by=request.requested_by,
            scope_filters=scope_filters,
        )

        device_ids = await self._resolve_device_scope(request)
        report_cache = (
            await self._load_completed_report_summaries(request.tenant_id)
            if request.include_report_intersections and request.tenant_id
            else []
        )

        scanned = 0
        suspicious = 0
        candidate_rows: list[dict] = []
        cur = request.start_date
        while cur <= request.end_date:
            if request.affected_window_start and cur < request.affected_window_start:
                cur += timedelta(days=1)
                continue
            if request.affected_window_end and cur > request.affected_window_end:
                cur += timedelta(days=1)
                continue

            for device_id in device_ids:
                scanned += 1
                preview = await self._preview_device_day(
                    run_id=run_id,
                    tenant_id=request.tenant_id,
                    device_id=device_id,
                    day=cur,
                    min_drift_kwh=request.min_drift_kwh,
                    min_drift_ratio=request.min_drift_ratio,
                    report_cache=report_cache,
                )
                if preview is not None:
                    suspicious += 1
                    candidate_rows.append(preview)
            cur += timedelta(days=1)

        await self._run_repo.update_run(
            run_id,
            status="completed",
            candidate_count=len(candidate_rows),
            suspicious_count=suspicious,
            completed_at=datetime.now(timezone.utc),
        )
        await self._session.commit()

        return {
            "run_id": run_id,
            "status": "completed",
            "tenant_id": request.tenant_id,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "scanned_device_days": scanned,
            "candidate_count": len(candidate_rows),
            "candidates": candidate_rows,
        }

    async def _resolve_device_scope(self, request: ReconciliationPreviewRequest) -> list[str]:
        if request.device_ids:
            return sorted({str(device_id) for device_id in request.device_ids if str(device_id).strip()})

        allowed = await self._engine._get_allowed_device_ids(request.tenant_id)
        if allowed:
            return sorted(allowed)

        rows = (
            await self._session.execute(
                select(EnergyDeviceDay.device_id)
                .where(EnergyDeviceDay.day >= request.start_date, EnergyDeviceDay.day <= request.end_date)
                .where(EnergyDeviceDay.tenant_id == request.tenant_id if request.tenant_id else true())
                .distinct()
            )
        ).scalars().all()
        return sorted({str(row) for row in rows if row})

    async def _preview_device_day(
        self,
        *,
        run_id: str,
        tenant_id: str | None,
        device_id: str,
        day: date,
        min_drift_kwh: float,
        min_drift_ratio: float,
        report_cache: list[dict],
    ) -> dict | None:
        canonical = (
            await self._session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.device_id == device_id,
                    EnergyDeviceDay.day == day,
                    EnergyDeviceDay.tenant_id == tenant_id if tenant_id else true(),
                )
            )
        ).scalar_one_or_none()

        rows = await self._fetch_telemetry_rows(tenant_id=tenant_id, device_id=device_id, day=day)
        recomputed = await self._recompute_metrics(device_id=device_id, tenant_id=tenant_id, day=day, rows=rows)
        candidate_reasons = self._detect_candidate_reasons(canonical=canonical, recomputed=recomputed)
        if not self._is_material_candidate(
            candidate_reasons=candidate_reasons,
            canonical=canonical,
            recomputed=recomputed,
            min_drift_kwh=min_drift_kwh,
            min_drift_ratio=min_drift_ratio,
        ):
            return None

        related_reports = self._intersect_reports(report_cache, device_id=device_id, day=day)
        old_metrics = self._canonical_metrics(canonical)
        new_metrics = recomputed["metrics"]
        old_energy = float(old_metrics.get("energy_kwh") or 0.0)
        new_energy = float(new_metrics.get("energy_kwh") or 0.0)

        audit = await self._audit_repo.create_item(
            run_id=run_id,
            tenant_id=tenant_id,
            device_id=device_id,
            day=day,
            period_type="device_day",
            period_start=datetime.combine(day, time.min, tzinfo=timezone.utc),
            period_end=datetime.combine(day, time.max, tzinfo=timezone.utc),
            expected_energy_kwh=new_energy,
            projected_energy_kwh=old_energy,
            drift_kwh=round(new_energy - old_energy, 6),
            old_metrics=old_metrics,
            new_metrics=new_metrics,
            old_quality_flags={"quality_flags": old_metrics.get("quality_flags", [])},
            new_quality_flags={
                "candidate_reasons": candidate_reasons,
                "interval_reason_counts": recomputed["interval_reason_counts"],
                "interval_flag_counts": recomputed["interval_flag_counts"],
                "related_reports": related_reports,
            },
            algorithm_version=INTERVAL_ENERGY_ALGORITHM_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            source_window_start=datetime.combine(day, time.min, tzinfo=timezone.utc),
            source_window_end=datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc),
            status="recomputed",
        )

        return {
            "audit_id": audit.id,
            "device_id": device_id,
            "day": day.isoformat(),
            "candidate_reasons": candidate_reasons,
            "old_energy_kwh": round(old_energy, 6),
            "new_energy_kwh": round(new_energy, 6),
            "drift_kwh": round(new_energy - old_energy, 6),
            "related_reports": related_reports,
        }

    async def _fetch_telemetry_rows(self, *, tenant_id: str | None, device_id: str, day: date) -> list[dict]:
        base = (settings.DATA_SERVICE_BASE_URL or "").rstrip("/")
        if not base or not tenant_id:
            return []
        start_dt = datetime.combine(day, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await internal_get(
                client,
                f"{base}/api/v1/data/telemetry/{device_id}",
                service_name="energy-service",
                tenant_id=tenant_id,
                params={
                    "start_time": start_dt.isoformat(),
                    "end_time": end_dt.isoformat(),
                    "fields": ",".join(TELEMETRY_FIELDS),
                    "limit": 10000,
                },
            )
            if response.status_code != 200:
                return []
            payload = response.json()
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            items = data.get("items", []) if isinstance(data, dict) else []
            return list(items) if isinstance(items, list) else []

    async def _recompute_metrics(self, *, device_id: str, tenant_id: str | None, day: date, rows: list[dict]) -> dict:
        meta = await meta_cache.get(device_id, tenant_id)
        accounting = aggregate_window(
            rows,
            platform_tz=_get_platform_tz(),
            shifts=meta.get("shifts") or [],
            idle_threshold=meta.get("idle_threshold"),
            over_threshold=meta.get("over_threshold"),
            config_source=meta,
        )
        power_config = build_device_power_config(meta)
        reason_counts: Counter[str] = Counter()
        flag_counts: Counter[str] = Counter()
        normalized_rows = [normalize_telemetry_sample(row, power_config) for row in rows]
        for prev, curr in zip(normalized_rows, normalized_rows[1:]):
            interval = compute_interval_energy_delta(prev, curr)
            reason_counts[str(interval.reason_code or "unknown")] += 1
            for flag in interval.quality_flags:
                flag_counts[str(flag)] += 1

        return {
            "metrics": {
                "energy_kwh": round(accounting.total.energy_kwh, 6),
                "idle_kwh": round(accounting.total.idle_kwh, 6),
                "offhours_kwh": round(accounting.total.offhours_kwh, 6),
                "overconsumption_kwh": round(accounting.total.overconsumption_kwh, 6),
                "loss_kwh": round(accounting.total.total_loss_kwh, 6),
                "pf_estimated": bool(accounting.total.pf_estimated),
                "samples": int(accounting.samples),
            },
            "interval_reason_counts": dict(reason_counts),
            "interval_flag_counts": dict(flag_counts),
        }

    def _canonical_metrics(self, canonical: EnergyDeviceDay | None) -> dict:
        if canonical is None:
            return {
                "energy_kwh": 0.0,
                "idle_kwh": 0.0,
                "offhours_kwh": 0.0,
                "overconsumption_kwh": 0.0,
                "loss_kwh": 0.0,
                "quality_flags": [],
                "version": 0,
            }
        try:
            quality_flags = json.loads(canonical.quality_flags or "[]")
        except Exception:
            quality_flags = []
        return {
            "energy_kwh": float(canonical.energy_kwh or 0.0),
            "idle_kwh": float(canonical.idle_kwh or 0.0),
            "offhours_kwh": float(canonical.offhours_kwh or 0.0),
            "overconsumption_kwh": float(canonical.overconsumption_kwh or 0.0),
            "loss_kwh": float(canonical.loss_kwh or 0.0),
            "quality_flags": list(quality_flags if isinstance(quality_flags, list) else []),
            "version": int(canonical.version or 0),
        }

    def _detect_candidate_reasons(self, *, canonical: EnergyDeviceDay | None, recomputed: dict) -> list[str]:
        reasons: list[str] = []
        old = self._canonical_metrics(canonical)
        new = recomputed["metrics"]
        old_energy = float(old.get("energy_kwh") or 0.0)
        new_energy = float(new.get("energy_kwh") or 0.0)
        drift = abs(new_energy - old_energy)
        if drift > 1e-6:
            reasons.append("material_energy_drift")

        flags = {str(flag) for flag in old.get("quality_flags", [])}
        if flags & SUSPICIOUS_QUALITY_FLAGS:
            reasons.append("canonical_quality_flagged")

        reason_counts = recomputed.get("interval_reason_counts", {})
        flag_counts = recomputed.get("interval_flag_counts", {})
        implausible_count = sum(
            int(count)
            for reason, count in reason_counts.items()
            if reason in {"counter_implausible_vs_power", "counter_implausible_vs_hard_max"}
        )
        implausible_count += sum(
            int(count)
            for flag, count in flag_counts.items()
            if flag in {"counter_implausible_vs_power", "counter_implausible_vs_hard_max"}
        )
        if implausible_count > 0:
            reasons.append("abnormal_counter_jumps_detected")

        if bool(new.get("pf_estimated")):
            reasons.append("recompute_uses_estimated_pf")

        return reasons

    def _is_material_candidate(
        self,
        *,
        candidate_reasons: list[str],
        canonical: EnergyDeviceDay | None,
        recomputed: dict,
        min_drift_kwh: float,
        min_drift_ratio: float,
    ) -> bool:
        if not candidate_reasons:
            return False
        old = self._canonical_metrics(canonical)
        new = recomputed["metrics"]
        old_energy = float(old.get("energy_kwh") or 0.0)
        new_energy = float(new.get("energy_kwh") or 0.0)
        drift = abs(new_energy - old_energy)
        baseline = max(abs(old_energy), 0.0001)
        drift_ratio = drift / baseline
        if "canonical_quality_flagged" in candidate_reasons or "abnormal_counter_jumps_detected" in candidate_reasons:
            return True
        return drift >= min_drift_kwh and drift_ratio >= min_drift_ratio

    async def _load_completed_report_summaries(self, tenant_id: str | None) -> list[dict]:
        if not tenant_id:
            return []
        base = (settings.REPORTING_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            return []
        reports: list[dict] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            history_resp = await internal_get(
                client,
                f"{base}/api/reports/history",
                service_name="energy-service",
                tenant_id=tenant_id,
                params={"tenant_id": tenant_id, "limit": 100},
            )
            if history_resp.status_code != 200:
                return []
            history_payload = history_resp.json()
            report_rows = history_payload.get("reports", []) if isinstance(history_payload, dict) else []
            for row in report_rows:
                if not isinstance(row, dict) or row.get("status") != "completed":
                    continue
                report_id = row.get("report_id")
                if not report_id:
                    continue
                result_resp = await internal_get(
                    client,
                    f"{base}/api/reports/{report_id}/result",
                    service_name="energy-service",
                    tenant_id=tenant_id,
                    params={"tenant_id": tenant_id},
                )
                if result_resp.status_code != 200:
                    continue
                result_payload = result_resp.json()
                if not isinstance(result_payload, dict):
                    continue
                result = result_payload.get("result") or result_payload
                if not isinstance(result, dict):
                    continue
                reports.append(
                    {
                        "report_id": report_id,
                        "report_type": row.get("report_type"),
                        "start_date": result.get("start_date"),
                        "end_date": result.get("end_date"),
                        "device_scope": result.get("device_scope"),
                        "devices": result.get("devices") if isinstance(result.get("devices"), list) else [],
                    }
                )
        return reports

    def _intersect_reports(self, reports: list[dict], *, device_id: str, day: date) -> list[dict]:
        matches: list[dict] = []
        for report in reports:
            start_raw = report.get("start_date")
            end_raw = report.get("end_date")
            if not isinstance(start_raw, str) or not isinstance(end_raw, str):
                continue
            try:
                start_date = date.fromisoformat(start_raw)
                end_date = date.fromisoformat(end_raw)
            except Exception:
                continue
            if not (start_date <= day <= end_date):
                continue

            device_scope = str(report.get("device_scope") or "")
            device_match = device_scope == "ALL" or device_scope == device_id
            if not device_match:
                devices = report.get("devices") or []
                for item in devices:
                    if isinstance(item, dict) and str(item.get("device_id") or "") == device_id:
                        device_match = True
                        break
            if not device_match:
                continue
            matches.append(
                {
                    "report_id": str(report.get("report_id")),
                    "report_type": str(report.get("report_type") or ""),
                }
            )
        return matches
