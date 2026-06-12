from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from uuid import uuid4

from fastapi import HTTPException

from services.shared.tenant_context import (
    TenantContext,
    build_tenant_scoped_internal_headers,
    normalize_tenant_id,
)
from services.shared.telemetry_coverage import build_device_coverage_result
from src.config import settings
from src.database import AsyncSessionLocal
from src.repositories import WasteRepository
from src.services import compute_device_waste, summarize_insights
from src.services.influx_reader import influx_reader
from src.services.remote_clients import device_client, energy_client, tariff_cache, get_reporting_http_client
from src.storage.minio_client import minio_client
from src.utils.downloads import build_waste_download_path
from src.utils.localization import local_date_bounds_to_utc
from src.utils import clean_for_json

logger = logging.getLogger(__name__)
INTERNAL_WARNING_MARKERS = {"canonical_energy_projection_applied"}
SUPPRESSED_PUBLIC_WARNING_PREFIXES = {
    "POWER_UNIT_ASSUMED_WATTS:",
    "canonical_loss_overlay_rejected:",
}
SUPPRESSED_PUBLIC_WARNINGS = {
    "OVERCONSUMPTION: No overconsumption detected in this period",
}

TELEMETRY_FIELDS = [
    "energy_kwh",
    "power",
    "current",
    "current_l1",
    "current_l2",
    "current_l3",
    "phase_current",
    "i_l1",
    "voltage",
    "voltage_l1",
    "voltage_l2",
    "voltage_l3",
    "power_factor",
    "pf",
]
CANONICAL_SUSPICIOUS_FLAGS = {
    "counter_implausible_vs_power",
    "counter_implausible_hard_max",
    "counter_reverse_seen",
    "counter_reset_detected",
    "counter_gap_exceeded",
    "long_gap_fallback_blocked",
}
CANONICAL_SUSPICIOUS_REASONS = {
    "counter_implausible_vs_power",
    "counter_implausible_vs_hard_max",
    "counter_negative",
    "counter_reset_detected",
    "counter_gap_exceeded",
    "fallback_gap_exceeded",
}


def _background_tenant_context(tenant_id: str | None) -> TenantContext:
    normalized = normalize_tenant_id(tenant_id)
    if normalized is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "TENANT_SCOPE_REQUIRED",
                "message": "Tenant scope is required for waste-analysis background execution.",
            },
        )
    return TenantContext(
        tenant_id=normalized,
        user_id="svc:waste-analysis-service",
        role="internal_service",
        plant_ids=[],
        is_super_admin=False,
    )


def _effective_concurrency(configured: int) -> int:
    cpu = max(1, int(os.cpu_count() or 1))
    safe_upper = max(4, cpu * 4)
    return max(1, min(int(configured), safe_upper))


async def _resolve_devices(scope: str, requested_ids: list[str] | None, tenant_id: str | None) -> list[dict]:
    if scope == "selected":
        out = []
        for device_id in requested_ids or []:
            d = await device_client.get_device(device_id, tenant_id)
            if d:
                out.append(d)
        return out
    return await device_client.list_devices(tenant_id)


def _duration_label(seconds: int) -> str:
    minutes = max(0, round(seconds / 60))
    hours = minutes // 60
    rem = minutes % 60
    if hours <= 0:
        return f"{rem} min"
    return f"{hours} hr {rem} min"


def _is_low_or_insufficient(quality: str | None) -> bool:
    return (quality or "").lower() in {"low", "insufficient"}


def _warning_is_internal_or_noise(warning: str) -> bool:
    if not isinstance(warning, str):
        return False
    normalized = warning.strip()
    if not normalized:
        return False
    if normalized in INTERNAL_WARNING_MARKERS:
        return True
    if normalized in SUPPRESSED_PUBLIC_WARNINGS:
        return True
    return any(normalized.startswith(prefix) for prefix in SUPPRESSED_PUBLIC_WARNING_PREFIXES)


def _public_warnings(warnings: list[str]) -> list[str]:
    return [warning for warning in warnings if not _warning_is_internal_or_noise(warning)]


def _iter_utc_chunks(start_dt: datetime, end_dt: datetime, *, chunk_hours: int) -> list[tuple[datetime, datetime]]:
    safe_hours = max(1, int(chunk_hours))
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + timedelta(hours=safe_hours), end_dt)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


async def _query_accounting_rows(
    *,
    device_id: str,
    start_dt: datetime,
    end_dt: datetime,
    fields: list[str],
) -> list[dict]:
    rows: list[dict] = []
    for chunk_start, chunk_end in _iter_utc_chunks(
        start_dt,
        end_dt,
        chunk_hours=int(getattr(settings, "INFLUX_ACCOUNTING_CHUNK_HOURS", 24)),
    ):
        chunk_rows = await influx_reader.query_telemetry(
            device_id=device_id,
            start_dt=chunk_start,
            end_dt=chunk_end,
            fields=fields,
            aggregation_window=str(getattr(settings, "INFLUX_ACCOUNTING_WINDOW", "1m")),
        )
        rows.extend(chunk_rows)
    rows.sort(key=lambda row: str(row.get("timestamp") or row.get("_time") or ""))
    return rows


def _canonical_quality_markers(canonical_range: dict[str, Any] | None) -> tuple[set[str], set[str], set[str]]:
    flags: set[str] = set()
    reasons: set[str] = set()
    quality_classes: set[str] = set()
    if not canonical_range or not isinstance(canonical_range, dict):
        return flags, reasons, quality_classes

    def _collect_from_row(row: dict[str, Any]) -> None:
        if not isinstance(row, dict):
            return
        reason = row.get("reason_code")
        if isinstance(reason, str) and reason.strip():
            reasons.add(reason.strip())
        quality_class = row.get("quality_class")
        if isinstance(quality_class, str) and quality_class.strip():
            quality_classes.add(quality_class.strip())
        row_flags = row.get("quality_flags")
        if isinstance(row_flags, list):
            for item in row_flags:
                if isinstance(item, str) and item.strip():
                    flags.add(item.strip())

    _collect_from_row(canonical_range.get("totals") or {})
    for day in canonical_range.get("days") or []:
        _collect_from_row(day if isinstance(day, dict) else {})
    return flags, reasons, quality_classes


def _canonical_loss_totals(canonical_range: dict[str, Any] | None) -> dict[str, float] | None:
    if not canonical_range or not isinstance(canonical_range, dict) or not canonical_range.get("success"):
        return None
    totals = canonical_range.get("totals") or {}
    values: dict[str, float] = {}
    for key in ("idle_kwh", "offhours_kwh", "overconsumption_kwh", "loss_kwh"):
        value = totals.get(key)
        if isinstance(value, (int, float)):
            values[key] = float(value)
    return values if values else None


def _canonical_financial_totals(canonical_range: dict[str, Any] | None) -> dict[str, float] | None:
    if not canonical_range or not isinstance(canonical_range, dict) or not canonical_range.get("success"):
        return None
    totals = canonical_range.get("totals") or {}
    energy_kwh = totals.get("energy_kwh")
    if not isinstance(energy_kwh, (int, float)):
        return None
    values = {"energy_kwh": float(energy_kwh)}
    energy_cost = totals.get("energy_cost_inr")
    if isinstance(energy_cost, (int, float)):
        values["energy_cost_inr"] = float(energy_cost)
    loss_kwh = totals.get("loss_kwh")
    if isinstance(loss_kwh, (int, float)):
        values["loss_kwh"] = float(loss_kwh)
    loss_cost = totals.get("loss_cost_inr")
    if isinstance(loss_cost, (int, float)):
        values["loss_cost_inr"] = float(loss_cost)
    return values


def _apply_canonical_financial_totals(result, canonical_range: dict[str, Any] | None, tariff_rate: float | None) -> tuple[bool, str]:
    financial_totals = _canonical_financial_totals(canonical_range)
    if financial_totals is None:
        return False, "canonical_missing_financial_total"

    canonical_energy = float(financial_totals["energy_kwh"])
    local_energy = float(getattr(result, "total_energy_kwh", 0.0) or 0.0)
    if canonical_energy <= 0.0 and local_energy > 0.0:
        return False, "canonical_placeholder_zero_energy"

    result.total_energy_kwh = round(canonical_energy, 6)
    canonical_loss = financial_totals.get("loss_kwh")
    if canonical_loss is not None:
        result.total_loss_kwh = round(float(canonical_loss), 6)
    if "energy_cost_inr" in financial_totals:
        result.total_cost = round(float(financial_totals["energy_cost_inr"]), 2)
    elif tariff_rate is not None:
        result.total_cost = round(result.total_energy_kwh * float(tariff_rate), 2)
    if "canonical_energy_projection_applied" not in result.warnings:
        result.warnings.append("canonical_energy_projection_applied")
    return True, "canonical_financial_total_accepted"


def _should_apply_canonical_loss_overlay(result, canonical_range: dict[str, Any] | None) -> tuple[bool, str]:
    canonical = _canonical_loss_totals(canonical_range)
    if canonical is None:
        return False, "canonical_unavailable_or_untrusted"

    flags, reasons, quality_classes = _canonical_quality_markers(canonical_range)
    if flags & CANONICAL_SUSPICIOUS_FLAGS:
        return False, "canonical_suspicious_quality_flags"
    if reasons & CANONICAL_SUSPICIOUS_REASONS:
        return False, "canonical_suspicious_reason_code"
    if quality_classes & {"gap_exceeded", "invalid", "unbillable"}:
        return False, "canonical_suspicious_quality_class"

    local_quality = str(getattr(result, "overall_quality", "") or "").lower()
    local_idle = float(getattr(result, "idle_energy_kwh", 0.0) or 0.0)
    local_off = float(getattr(result, "offhours_energy_kwh", 0.0) or 0.0)
    local_over = float(getattr(result, "overconsumption_energy_kwh", 0.0) or 0.0)
    local_loss = local_idle + local_off + local_over

    canonical_idle = float(canonical.get("idle_kwh") or 0.0)
    canonical_off = float(canonical.get("offhours_kwh") or 0.0)
    canonical_over = float(canonical.get("overconsumption_kwh") or 0.0)
    canonical_loss = float(canonical.get("loss_kwh") or (canonical_idle + canonical_off + canonical_over))

    if local_loss > 0.0 and canonical_loss <= 0.0:
        return False, "canonical_placeholder_zero_loss"

    if local_quality in {"high", "medium"} and local_loss > 0.0:
        loss_diff = abs(canonical_loss - local_loss)
        if loss_diff > max(0.1, local_loss * 0.2):
            return False, "canonical_loss_materially_conflicts_with_local"

    if local_quality in {"high", "medium"} and local_idle > 0.1:
        idle_diff = abs(canonical_idle - local_idle)
        if canonical_idle < (local_idle * 0.5) and idle_diff > 0.1:
            return False, "canonical_idle_materially_conflicts_with_local"

    if local_quality in {"high", "medium"} and local_off > 0.25:
        off_diff = abs(canonical_off - local_off)
        if off_diff > max(0.25, local_off * 0.2):
            return False, "canonical_offhours_materially_conflicts_with_local"

    if local_quality in {"high", "medium"} and local_over > 0.1:
        over_diff = abs(canonical_over - local_over)
        if over_diff > max(0.1, local_over * 0.2):
            return False, "canonical_overconsumption_materially_conflicts_with_local"

    return True, "canonical_loss_accepted"


def _build_device_summary(result, tariff_rate: float | None) -> dict:
    device_total_waste_cost = round(
        (result.idle_cost or 0.0)
        + (result.offhours_cost or 0.0)
        + (result.overconsumption_cost or 0.0),
        2,
    ) if tariff_rate is not None else None
    over_config = result.overconsumption_config_used or {}
    return {
        "device_id": result.device_id,
        "device_name": result.device_name,
        "data_source_type": result.data_source_type,
        "idle_duration_sec": result.idle_duration_sec,
        "idle_duration_label": _duration_label(result.idle_duration_sec),
        "idle_energy_kwh": result.idle_energy_kwh,
        "idle_cost": result.idle_cost,
        "total_energy_kwh": result.total_energy_kwh,
        "total_cost": result.total_cost,
        "total_energy_cost": result.total_cost,
        "total_energy_cost_inr": result.total_cost,
        "total_waste_cost": device_total_waste_cost,
        "total_waste_cost_inr": device_total_waste_cost,
        "full_load_current_a": over_config.get("full_load_current_a"),
        "idle_threshold_pct_of_fla": over_config.get("idle_threshold_pct_of_fla"),
        "derived_idle_threshold_a": over_config.get("derived_idle_threshold_a"),
        "derived_overconsumption_threshold_a": over_config.get("derived_overconsumption_threshold_a"),
        "offhours_energy_kwh": result.offhours_energy_kwh,
        "offhours_cost": result.offhours_cost,
        "offhours_duration_sec": result.offhours_duration_sec,
        "offhours_skipped_reason": result.offhours_skipped_reason,
        "offhours_pf_estimated": result.offhours_pf_estimated,
        "overconsumption_duration_sec": result.overconsumption_duration_sec,
        "overconsumption_kwh": result.overconsumption_energy_kwh,
        "overconsumption_energy_kwh": result.overconsumption_energy_kwh,
        "overconsumption_cost": result.overconsumption_cost,
        "overconsumption_skipped_reason": result.overconsumption_skipped_reason,
        "overconsumption_pf_estimated": result.overconsumption_pf_estimated,
        "unoccupied_duration_sec": result.unoccupied_duration_sec,
        "unoccupied_energy_kwh": result.unoccupied_energy_kwh,
        "unoccupied_cost": result.unoccupied_cost,
        "unoccupied_skipped_reason": result.unoccupied_skipped_reason,
        "unoccupied_pf_estimated": result.unoccupied_pf_estimated,
        "off_hours": {
            "duration_sec": result.offhours_duration_sec,
            "energy_kwh": result.offhours_energy_kwh,
            "cost": result.offhours_cost,
            "skipped_reason": result.offhours_skipped_reason,
            "pf_estimated": result.offhours_pf_estimated,
            "config_source": "shift_config",
        },
        "overconsumption": {
            "duration_sec": result.overconsumption_duration_sec,
            "energy_kwh": result.overconsumption_energy_kwh,
            "cost": result.overconsumption_cost,
            "skipped_reason": result.overconsumption_skipped_reason,
            "pf_estimated": result.overconsumption_pf_estimated,
            "config_source": result.overconsumption_config_source,
            "config_used": result.overconsumption_config_used,
        },
        "unoccupied_running": {
            "duration_sec": result.unoccupied_duration_sec,
            "energy_kwh": result.unoccupied_energy_kwh,
            "cost": result.unoccupied_cost,
            "skipped_reason": result.unoccupied_skipped_reason,
            "pf_estimated": result.unoccupied_pf_estimated,
            "config_source": result.unoccupied_config_source,
            "config_used": result.unoccupied_config_used,
        },
        "idle_status": result.idle_status,
        "power_unit_input": result.power_unit_input,
        "power_unit_normalized_to": result.power_unit_normalized_to,
        "normalization_applied": result.normalization_applied,
        "pf_estimated": result.pf_estimated,
        "warnings": _public_warnings(list(result.warnings or [])),
        "calculation_method": result.calculation_method,
    }


def _sync_canonical_overlay_warnings(result) -> None:
    if (result.offhours_energy_kwh or 0.0) > 0:
        result.warnings = [w for w in result.warnings if w != "OFF_HOURS: No off-hours consumption detected"]
    if (result.overconsumption_energy_kwh or 0.0) > 0:
        result.warnings = [w for w in result.warnings if w != "OVERCONSUMPTION: No overconsumption detected in this period"]


def _to_db_summary_from_result(result) -> dict:
    overall_quality = getattr(result, "overall_quality", None)
    return {
        "device_id": result.device_id,
        "device_name": result.device_name,
        "data_source_type": result.data_source_type,
        "idle_duration_sec": result.idle_duration_sec,
        "idle_energy_kwh": result.idle_energy_kwh,
        "idle_cost": result.idle_cost,
        "standby_power_kw": result.standby_power_kw,
        "standby_energy_kwh": result.standby_energy_kwh,
        "standby_cost": result.standby_cost,
        "total_energy_kwh": result.total_energy_kwh,
        "total_cost": result.total_cost,
        "offhours_energy_kwh": result.offhours_energy_kwh,
        "offhours_cost": result.offhours_cost,
        "offhours_duration_sec": result.offhours_duration_sec,
        "offhours_skipped_reason": result.offhours_skipped_reason,
        "offhours_pf_estimated": result.offhours_pf_estimated,
        "overconsumption_duration_sec": result.overconsumption_duration_sec,
        "overconsumption_kwh": result.overconsumption_energy_kwh,
        "overconsumption_cost": result.overconsumption_cost,
        "overconsumption_skipped_reason": result.overconsumption_skipped_reason,
        "overconsumption_pf_estimated": result.overconsumption_pf_estimated,
        "unoccupied_duration_sec": result.unoccupied_duration_sec,
        "unoccupied_energy_kwh": result.unoccupied_energy_kwh,
        "unoccupied_cost": result.unoccupied_cost,
        "unoccupied_skipped_reason": result.unoccupied_skipped_reason,
        "unoccupied_pf_estimated": result.unoccupied_pf_estimated,
        "data_quality": getattr(result, "data_quality", overall_quality),
        "energy_quality": getattr(result, "energy_quality", overall_quality),
        "idle_quality": getattr(result, "idle_quality", overall_quality),
        "standby_quality": getattr(result, "standby_quality", overall_quality),
        "overall_quality": overall_quality,
        "idle_status": result.idle_status,
        "pf_estimated": result.pf_estimated,
        "warnings": _public_warnings(list(result.warnings or [])),
        "calculation_method": result.calculation_method,
    }


async def _complete_job_with_result(
    repo: WasteRepository,
    *,
    job_id: str,
    result_payload: dict,
    db_summaries: list[dict],
    tariff_rate: float | None,
    currency: str | None,
    s3_key: str | None = None,
    download_url: str | None = None,
    stage: str = "Complete ✓",
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    await repo.replace_device_summaries_chunked(
        job_id,
        summaries=db_summaries,
        batch_size=settings.WASTE_DB_BATCH_SIZE,
    )
    await repo.update_job(
        job_id,
        status="completed",
        progress_pct=100,
        stage=stage,
        result_json=clean_for_json(result_payload),
        s3_key=s3_key,
        download_url=download_url,
        tariff_rate_used=tariff_rate,
        currency=currency,
        error_code=error_code,
        error_message=error_message,
        completed_at=datetime.utcnow(),
    )


def _build_waste_coverage_result(
    *,
    devices: list[dict],
    results: list,
    quality_failures: list[dict],
    warnings: list[str],
    artifact_generation_allowed: bool,
) -> dict:
    selected_ids = [str(device.get("device_id")) for device in devices if device.get("device_id")]
    usable_ids = [
        str(result.device_id)
        for result in results
        if getattr(result, "device_id", None)
        and str(getattr(result, "overall_quality", "")).lower() not in {"low", "insufficient"}
    ]
    skipped = [
        {
            "device_id": str(item.get("device_id") or ""),
            "reason": str(item.get("code") or "LOW_QUALITY_DATA"),
            "message": str(item.get("message") or "Device telemetry quality is insufficient."),
        }
        for item in quality_failures
        if item.get("device_id")
    ]
    has_any_data = bool(results or quality_failures)
    return build_device_coverage_result(
        selected_device_ids=selected_ids,
        usable_device_ids=usable_ids,
        has_any_data=has_any_data,
        skipped_devices=skipped,
        warnings=warnings,
        artifact_generation_allowed=artifact_generation_allowed,
    ).to_dict()


async def _find_reporting_reference_kwh(
    scope: str,
    selected_ids: list[str],
    start_date,
    end_date,
    tenant_id: str | None,
) -> float | None:
    if not tenant_id:
        return None
    target_scope = "ALL" if scope == "all" else (selected_ids[0] if len(selected_ids) == 1 else None)
    if target_scope is None:
        return None
    headers = build_tenant_scoped_internal_headers("waste-analysis-service", tenant_id)
    client = get_reporting_http_client()
    hist = await client.get(
        f"{settings.REPORTING_SERVICE_URL}/api/reports/history",
        params={"tenant_id": tenant_id, "limit": 50, "report_type": "consumption"},
        headers=headers,
    )
    if hist.status_code != 200:
        return None
    reports = (hist.json() or {}).get("reports") or []
    for item in reports:
        if item.get("status") != "completed":
            continue
        rid = item.get("report_id")
        if not rid:
            continue
        res = await client.get(
            f"{settings.REPORTING_SERVICE_URL}/api/reports/{rid}/result",
            params={"tenant_id": tenant_id},
            headers=headers,
        )
        if res.status_code != 200:
            continue
        payload = res.json() or {}
        if payload.get("start_date") != start_date.isoformat():
            continue
        if payload.get("end_date") != end_date.isoformat():
            continue
        if str(payload.get("device_scope")) != target_scope:
            continue
        summary = payload.get("summary") or {}
        total_kwh = summary.get("total_kwh")
        if isinstance(total_kwh, (int, float)):
            return float(total_kwh)
    return None


async def run_waste_analysis(job_id: str, params: dict) -> None:
    async with AsyncSessionLocal() as db:
        tenant_id = normalize_tenant_id(params.get("tenant_id"))
        repo = WasteRepository(db, _background_tenant_context(tenant_id))

        try:
            await repo.update_job(
                job_id,
                status="running",
                progress_pct=5,
                stage="Fetching device list...",
                started_at=datetime.utcnow(),
            )

            scope = params.get("scope", "all")
            start_date = datetime.strptime(params["start_date"], "%Y-%m-%d").date()
            end_date = datetime.strptime(params["end_date"], "%Y-%m-%d").date()
            granularity = params.get("granularity", "daily")
            selected = params.get("device_ids") or []

            devices = await _resolve_devices(scope, selected, tenant_id)
            if not devices:
                await repo.update_job(
                    job_id,
                    status="failed",
                    error_code="NO_DEVICES_FOUND",
                    progress_pct=100,
                    stage="Failed",
                    error_message="No devices available for analysis",
                    completed_at=datetime.utcnow(),
                )
                return

            await repo.update_job(job_id, progress_pct=8, stage="Validating configuration...")

            quality_failures: list[dict] = []
            threshold_by_device: dict[str, float | None] = {}
            threshold_config_by_device: dict[str, dict] = {}
            shifts_by_device: dict[str, list[dict]] = {}
            overconsumption_threshold_by_device: dict[str, float | None] = {}
            config_warnings: list[str] = []
            skipped_devices: list[dict] = []

            eff_conc = _effective_concurrency(settings.WASTE_DEVICE_CONCURRENCY)
            logger.info(
                "waste_device_concurrency_resolved configured=%s effective=%s cpu_count=%s",
                int(settings.WASTE_DEVICE_CONCURRENCY),
                eff_conc,
                max(1, int(os.cpu_count() or 1)),
            )
            cfg_sem = asyncio.Semaphore(eff_conc)

            async def _load_device_config(
                d: dict,
            ) -> tuple[
                str,
                dict,
                list[dict],
                float | None,
            ]:
                device_id = d.get("device_id")
                if not device_id:
                    return "", {}, [], None
                async with cfg_sem:
                    idle_cfg, shifts, waste_cfg = await asyncio.gather(
                        device_client.get_idle_config(device_id, tenant_id),
                        device_client.get_shift_config(device_id, tenant_id),
                        device_client.get_waste_config(device_id, tenant_id),
                    )

                overconsumption_threshold = waste_cfg.get("derived_overconsumption_threshold_a")

                return (
                    str(device_id),
                    idle_cfg,
                    shifts,
                    overconsumption_threshold,
                )

            cfg_tasks = [asyncio.create_task(_load_device_config(d)) for d in devices]
            for fut in asyncio.as_completed(cfg_tasks):
                (
                    device_id,
                    idle_cfg,
                    shifts,
                    overconsumption_threshold,
                ) = await fut
                if not device_id:
                    continue
                threshold_config_by_device[device_id] = idle_cfg
                threshold = idle_cfg.get("derived_idle_threshold_a")
                threshold_by_device[device_id] = threshold
                shifts_by_device[device_id] = shifts
                overconsumption_threshold_by_device[device_id] = overconsumption_threshold
                if idle_cfg.get("full_load_current_a") is None:
                    config_warnings.append(
                        f"{device_id}: full load current (FLA) not configured (idle and overconsumption categories reduced)"
                    )
                if threshold is None:
                    config_warnings.append(f"{device_id}: derived idle threshold unavailable (idle category reduced)")
                if overconsumption_threshold is None:
                    config_warnings.append(
                        f"{device_id}: derived overconsumption threshold unavailable (category skipped)"
                    )

            tariff = await tariff_cache.get(tenant_id)
            await repo.update_job(job_id, progress_pct=10, stage="Fetching tariff configuration...")

            start_dt, end_dt = local_date_bounds_to_utc(start_date, end_date)

            results = []
            warnings: list[str] = list(config_warnings)
            n_devices = max(1, len(devices))
            dev_sem = asyncio.Semaphore(eff_conc)

            async def _process_device(d: dict):
                device_id = d.get("device_id")
                if not device_id:
                    return None
                device_name = d.get("device_name") or device_id
                data_source_type = d.get("data_source_type") or "metered"
                async with dev_sem:
                    rows = await _query_accounting_rows(
                        device_id=device_id,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        fields=TELEMETRY_FIELDS,
                    )
                threshold = threshold_by_device.get(device_id)
                threshold_config = threshold_config_by_device.get(device_id, {})
                shifts = shifts_by_device.get(device_id, [])
                overconsumption_threshold = overconsumption_threshold_by_device.get(device_id)
                res = compute_device_waste(
                    device_id=device_id,
                    device_name=device_name,
                    data_source_type=str(data_source_type),
                    rows=rows,
                    threshold=threshold,
                    overconsumption_threshold=overconsumption_threshold,
                    tariff_rate=tariff.rate,
                    shifts=shifts,
                    threshold_config=threshold_config,
                )
                canonical = await energy_client.get_device_range(
                    device_id=device_id,
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                    tenant_id=tenant_id,
                )
                overlay_applied = False
                if canonical:
                    _apply_canonical_financial_totals(res, canonical, tariff.rate)
                    apply_overlay, overlay_reason = _should_apply_canonical_loss_overlay(res, canonical)
                    if apply_overlay:
                        overlay_applied = True
                    else:
                        res.warnings.append(f"canonical_loss_overlay_rejected:{overlay_reason}")
                if canonical and overlay_applied:
                    totals = canonical.get("totals") or {}
                    if isinstance(totals.get("energy_kwh"), (int, float)):
                        res.total_energy_kwh = round(float(totals.get("energy_kwh") or 0.0), 6)
                    if isinstance(totals.get("idle_kwh"), (int, float)):
                        res.idle_energy_kwh = round(float(totals.get("idle_kwh") or 0.0), 6)
                    if isinstance(totals.get("offhours_kwh"), (int, float)):
                        res.offhours_energy_kwh = round(float(totals.get("offhours_kwh") or 0.0), 6)
                    if isinstance(totals.get("overconsumption_kwh"), (int, float)):
                        res.overconsumption_energy_kwh = round(float(totals.get("overconsumption_kwh") or 0.0), 6)
                    if tariff.rate is not None:
                        rate = float(tariff.rate)
                        canonical_total_cost = getattr(res, "total_cost", None)
                        if canonical_total_cost is not None and res.total_energy_kwh and res.total_energy_kwh > 0:
                            idle_share = (res.idle_energy_kwh or 0.0) / res.total_energy_kwh
                            off_share = (res.offhours_energy_kwh or 0.0) / res.total_energy_kwh
                            over_share = (res.overconsumption_energy_kwh or 0.0) / res.total_energy_kwh
                            allocated_idle = round(canonical_total_cost * idle_share, 2)
                            allocated_off = round(canonical_total_cost * off_share, 2) if res.offhours_energy_kwh is not None else None
                            allocated_over = round(canonical_total_cost * over_share, 2) if res.overconsumption_energy_kwh is not None else None
                            bucket_sum = allocated_idle + (allocated_off or 0.0) + (allocated_over or 0.0)
                            remainder = round(canonical_total_cost - bucket_sum, 2)
                            if abs(remainder) >= 0.01:
                                buckets = [("idle", allocated_idle, res.idle_energy_kwh), ("over", allocated_over or 0.0, res.overconsumption_energy_kwh), ("off", allocated_off or 0.0, res.offhours_energy_kwh)]
                                largest = max(buckets, key=lambda b: b[2] or 0.0)
                                if largest[0] == "idle":
                                    allocated_idle = round(allocated_idle + remainder, 2)
                                elif largest[0] == "off" and allocated_off is not None:
                                    allocated_off = round(allocated_off + remainder, 2)
                                elif allocated_over is not None:
                                    allocated_over = round(allocated_over + remainder, 2)
                            res.idle_cost = allocated_idle
                            res.offhours_cost = allocated_off
                            res.overconsumption_cost = allocated_over
                        else:
                            res.total_cost = round(res.total_energy_kwh * rate, 2)
                            res.idle_cost = round((res.idle_energy_kwh or 0.0) * rate, 2)
                            res.offhours_cost = (
                                round((res.offhours_energy_kwh or 0.0) * rate, 2) if res.offhours_energy_kwh is not None else None
                            )
                            res.overconsumption_cost = (
                                round((res.overconsumption_energy_kwh or 0.0) * rate, 2)
                                if res.overconsumption_energy_kwh is not None
                                else None
                            )
                    if "canonical_energy_projection_applied" not in res.warnings:
                        res.warnings.append("canonical_energy_projection_applied")
                    _sync_canonical_overlay_warnings(res)
                return device_name, device_id, res

            proc_tasks = [asyncio.create_task(_process_device(d)) for d in devices]
            processed = 0
            for fut in asyncio.as_completed(proc_tasks):
                out = await fut
                if out is None:
                    continue
                device_name, device_id, res = out
                results.append(res)
                warnings.extend([f"{device_name}: {w}" for w in _public_warnings(list(res.warnings or []))])
                if _is_low_or_insufficient(res.overall_quality):
                    quality_failures.append(
                        {
                            "device_id": device_id,
                            "metric": "overall",
                            "code": "LOW_QUALITY_DATA" if res.overall_quality == "low" else "INSUFFICIENT_DATA",
                            "message": f"Device quality is {res.overall_quality}",
                        }
                    )
                processed += 1
                if processed == 1 or processed == n_devices or (processed % max(1, n_devices // 20) == 0):
                    await repo.update_job(
                        job_id,
                        progress_pct=min(80, 10 + int((processed / n_devices) * 65)),
                        stage=f"Loading telemetry and computing... ({processed} of {n_devices})",
                    )

            total_idle_kwh = round(sum(r.idle_energy_kwh for r in results), 6)
            total_idle_seconds = sum(r.idle_duration_sec for r in results)
            total_energy_kwh = round(sum(r.total_energy_kwh for r in results), 6)
            canonical_total_loss_kwh = sum(getattr(r, "total_loss_kwh", None) or 0.0 for r in results)
            any_canonical_loss = any(getattr(r, "total_loss_kwh", None) is not None for r in results)
            total_loss_kwh = round(canonical_total_loss_kwh, 6) if any_canonical_loss else round(
                sum(r.idle_energy_kwh for r in results)
                + sum((r.offhours_energy_kwh or 0.0) for r in results)
                + sum((r.overconsumption_energy_kwh or 0.0) for r in results),
                6,
            )
            total_energy_cost = None if tariff.rate is None else round(sum((r.total_cost or 0.0) for r in results), 2)
            total_waste_cost = None if tariff.rate is None else round(
                sum(
                    (r.idle_cost or 0.0)
                    + (r.offhours_cost or 0.0)
                    + (r.overconsumption_cost or 0.0)
                    for r in results
                ),
                2,
            )
            worst_device = "N/A"
            if results:
                worst = max(
                    results,
                    key=lambda x: (x.idle_cost or 0.0)
                    + (x.offhours_cost or 0.0)
                    + (x.overconsumption_cost or 0.0),
                )
                worst_device = worst.device_name

            insights = summarize_insights(results, tariff.currency)

            device_summaries = []
            for r in results:
                device_summaries.append(_build_device_summary(r, tariff.rate))

            quality_gate_passed = len(quality_failures) == 0
            public_warnings = sorted(set(_public_warnings(warnings)))
            coverage_result = _build_waste_coverage_result(
                devices=devices,
                results=results,
                quality_failures=quality_failures,
                warnings=public_warnings,
                artifact_generation_allowed=quality_gate_passed,
            )

            result_payload = {
                "job_id": job_id,
                "scope": scope,
                "scope_label": "All Devices" if scope == "all" else f"Selected Devices ({len(device_summaries)})",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "granularity": granularity,
                "tariff_rate_used": tariff.rate,
                "currency": tariff.currency,
                "tariff_stale": tariff.stale,
                "total_idle_kwh": total_idle_kwh,
                "total_idle_duration_sec": total_idle_seconds,
                "total_idle_label": _duration_label(total_idle_seconds),
                "total_energy_kwh": total_energy_kwh,
                "total_loss_kwh": total_loss_kwh,
                "total_energy_cost": total_energy_cost,
                "total_energy_cost_inr": total_energy_cost,
                "total_waste_cost": total_waste_cost,
                "total_waste_cost_inr": total_waste_cost,
                "total_idle_cost_inr": None if tariff.rate is None else round(sum((r.idle_cost or 0.0) for r in results), 2),
                "offhours_total_cost_inr": None if tariff.rate is None else round(sum((r.offhours_cost or 0.0) for r in results), 2),
                "overconsumption_total_cost_inr": None if tariff.rate is None else round(sum((r.overconsumption_cost or 0.0) for r in results), 2),
                "worst_device": worst_device,
                "device_summaries": device_summaries,
                "warnings": public_warnings,
                "insights": insights,
                "coverage_result": coverage_result,
                "quality_gate_passed": quality_gate_passed,
                "quality_failures": quality_failures,
                "skipped_devices": skipped_devices,
                "estimation_used": False,
                "calculation_version": "waste_v2_exclusive",
                "aggregation_policy": "mutually_exclusive",
            }

            invariant_checks = {"waste_le_total_energy": True}
            if total_waste_cost is not None and total_energy_cost is not None:
                invariant_checks["waste_le_total_energy"] = total_waste_cost <= (total_energy_cost + 0.01)
            result_payload["invariant_checks"] = invariant_checks

            try:
                ref_kwh = await _find_reporting_reference_kwh(
                    scope, selected, start_date, end_date, tenant_id
                )
                if ref_kwh is not None:
                    tolerance = max(0.5, 0.01 * ref_kwh)
                    delta = abs(total_energy_kwh - ref_kwh)
                    passed = delta <= tolerance
                    result_payload["parity_check"] = {
                        "checked": True,
                        "reference_source": "reporting-service:consumption",
                        "reference_total_kwh": round(ref_kwh, 6),
                        "waste_total_kwh": round(total_energy_kwh, 6),
                        "abs_delta_kwh": round(delta, 6),
                        "tolerance_kwh": round(tolerance, 6),
                        "passed": passed,
                    }
                    if not passed:
                        result_payload["warnings"].append(
                            "PARITY_CHECK_WARNING: waste vs consumption totals differ beyond tolerance"
                        )
                else:
                    result_payload["parity_check"] = {"checked": False}
            except Exception:
                result_payload["parity_check"] = {"checked": False}

            db_summaries = [_to_db_summary_from_result(r) for r in results]

            if settings.WASTE_STRICT_QUALITY_GATE and not quality_gate_passed:
                await _complete_job_with_result(
                    repo,
                    job_id=job_id,
                    result_payload=result_payload,
                    db_summaries=db_summaries,
                    tariff_rate=tariff.rate,
                    currency=tariff.currency,
                    stage="Insufficient coverage",
                    error_code="INSUFFICIENT_TELEMETRY_COVERAGE",
                    error_message="Waste analysis completed with insufficient telemetry coverage; result is not usable for business decisions.",
                )
                return

            await repo.update_job(job_id, progress_pct=88, stage="Generating PDF...")
            from src.pdf.builder import async_generate_waste_pdf

            try:
                pdf_bytes = await async_generate_waste_pdf(result_payload)
            except Exception as artifact_exc:
                logger.exception("waste_analysis_artifact_generation_failed job_id=%s", job_id)
                await _complete_job_with_result(
                    repo,
                    job_id=job_id,
                    result_payload=result_payload,
                    db_summaries=db_summaries,
                    tariff_rate=tariff.rate,
                    currency=tariff.currency,
                    stage="Result ready · PDF unavailable",
                    error_code="ARTIFACT_GENERATION_FAILED",
                    error_message=f"Result generated successfully, but PDF generation failed: {artifact_exc}",
                )
                return

            s3_key = f"waste-reports/{job_id}/waste_report_{uuid4().hex[:8]}.pdf"
            try:
                await minio_client.async_upload_pdf(pdf_bytes, s3_key)
            except Exception as artifact_exc:
                logger.exception("waste_analysis_artifact_upload_failed job_id=%s", job_id)
                await _complete_job_with_result(
                    repo,
                    job_id=job_id,
                    result_payload=result_payload,
                    db_summaries=db_summaries,
                    tariff_rate=tariff.rate,
                    currency=tariff.currency,
                    stage="Result ready · PDF unavailable",
                    error_code="ARTIFACT_UPLOAD_FAILED",
                    error_message=f"Result generated successfully, but PDF upload failed: {artifact_exc}",
                )
                return

            download_url = build_waste_download_path(job_id)
            await _complete_job_with_result(
                repo,
                job_id=job_id,
                result_payload=result_payload,
                db_summaries=db_summaries,
                tariff_rate=tariff.rate,
                currency=tariff.currency,
                s3_key=s3_key,
                download_url=download_url,
            )
        except Exception as exc:
            logger.exception("waste_analysis_failed job_id=%s", job_id)
            await repo.update_job(
                job_id,
                status="failed",
                error_code="INTERNAL_ERROR",
                progress_pct=100,
                stage="Failed",
                error_message=str(exc),
                completed_at=datetime.utcnow(),
            )
