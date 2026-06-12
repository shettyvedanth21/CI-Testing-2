from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select, update, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    EnergyDeviceDay,
    EnergyDeviceMonth,
    EnergyDeviceState,
    EnergyFleetDay,
    EnergyFleetMonth,
)
from app.services.device_meta import meta_cache
from app.services.internal_http import internal_get
from app.services.tariff_cache import tariff_cache
from services.shared.energy_accounting import split_loss_components
from services.shared.telemetry_normalization import (
    DevicePowerConfig,
    NormalizedTelemetrySample,
    compute_interval_energy_delta,
    normalize_telemetry_sample,
)

logger = logging.getLogger(__name__)


def _get_platform_tz() -> ZoneInfo:
    return ZoneInfo(settings.PLATFORM_TIMEZONE)


async def update_live_state_with_lock(
    session: AsyncSession,
    device_id: str,
    updates: dict,
    max_retries: int = 3,
    retry_delay_ms: int = 50
) -> bool:
    """
    Returns True if update succeeded, False if all retries exhausted.
    Uses savepoints so partial failures do not roll back the entire session.
    """

    for attempt in range(1, max_retries + 1):
        async with session.begin_nested():
            version_result = await session.execute(
                select(EnergyDeviceState.version).where(EnergyDeviceState.device_id == device_id)
            )
            expected_version = version_result.scalar_one_or_none()
            if expected_version is None:
                logger.error(
                    "Energy device state row missing for optimistic lock update",
                    extra={"device_id": device_id},
                )
                return False

            update_result = await session.execute(
                update(EnergyDeviceState)
                .where(
                    EnergyDeviceState.device_id == device_id,
                    EnergyDeviceState.version == expected_version,
                )
                .values(
                    **updates,
                    version=EnergyDeviceState.version + 1,
                    updated_at=datetime.utcnow(),
                )
            )
            if (update_result.rowcount or 0) > 0:
                return True

        if attempt < max_retries:
            logger.warning(
                "Optimistic lock conflict on energy live state update; retrying",
                extra={
                    "device_id": device_id,
                    "attempt": attempt,
                    "max_retries": max_retries,
                },
            )
            await asyncio.sleep(retry_delay_ms / 1000.0)

    logger.error(
        "Optimistic lock retries exhausted for energy live state update",
        extra={"device_id": device_id, "max_retries": max_retries},
    )
    return False


@dataclass
class DeltaMetrics:
    energy_kwh: float
    idle_kwh: float
    offhours_kwh: float
    overconsumption_kwh: float
    loss_kwh: float
    flags: set[str]
    energy_method: str
    quality_class: str
    reason_code: str
    algorithm_version: str


class EnergyEngine:
    """Energy aggregate engine.

    Aggregate cost semantics:
    - `energy_kwh` / `loss_kwh` remain the canonical aggregate truth.
    - Aggregate `*_cost_inr` fields are persisted tariff-aware convenience values.
    - Live ingestion adds interval cost incrementally so prior energy is never repriced.
    - Historical reads prefer persisted aggregate costs and only derive overlay costs for
      synthetic live overlays that are not yet persisted.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _safe_rate(tariff: dict[str, Any]) -> float:
        try:
            return float(tariff.get("rate") or 0.0)
        except Exception:
            return 0.0

    @staticmethod
    def _currency(tariff: dict[str, Any]) -> str:
        return str(tariff.get("currency") or "INR")

    @staticmethod
    def _stored_energy_cost(row: Any, fallback_rate: float) -> float:
        stored = getattr(row, "energy_cost_inr", None)
        if stored is None:
            return float(getattr(row, "energy_kwh", 0.0) or 0.0) * fallback_rate
        return float(stored or 0.0)

    @staticmethod
    def _stored_loss_cost(row: Any, fallback_rate: float) -> float:
        stored = getattr(row, "loss_cost_inr", None)
        if stored is None:
            return float(getattr(row, "loss_kwh", 0.0) or 0.0) * fallback_rate
        return float(stored or 0.0)

    async def _get_allowed_device_ids(self, tenant_id: Optional[str]) -> Optional[set[str]]:
        if not tenant_id:
            return None
        base = (settings.DEVICE_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            return set()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await internal_get(
                    client,
                    f"{base}/api/v1/devices",
                    service_name="energy-service",
                    tenant_id=tenant_id,
                    params={"tenant_id": tenant_id},
                )
                payload = response.json()
                rows = payload if isinstance(payload, list) else payload.get("data", [])
                if not isinstance(rows, list):
                    return set()
                return {
                    str(item.get("device_id") or item.get("id"))
                    for item in rows
                    if isinstance(item, dict) and (item.get("device_id") or item.get("id"))
                }
        except Exception:
            return set()

    async def _fetch_live_current_day_totals(self, device_id: str, tenant_id: Optional[str]) -> dict[str, Any] | None:
        if not tenant_id:
            return None
        base = (settings.DEVICE_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            return None
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await internal_get(
                    client,
                    f"{base}/api/v1/devices/{device_id}/loss-stats",
                    service_name="energy-service",
                    tenant_id=tenant_id,
                    params={"tenant_id": tenant_id},
                )
                if response.status_code != 200:
                    return None
                payload = response.json()
                if not isinstance(payload, dict) or not payload.get("success"):
                    return None
                today = payload.get("today")
                if not isinstance(today, dict):
                    return None
                return {
                    "date": payload.get("day_bucket"),
                    "energy_kwh": float(today.get("today_energy_kwh") or 0.0),
                    "loss_kwh": float(today.get("total_loss_kwh") or 0.0),
                    "idle_kwh": float(today.get("idle_kwh") or 0.0),
                    "offhours_kwh": float(today.get("off_hours_kwh") or 0.0),
                    "overconsumption_kwh": float(today.get("overconsumption_kwh") or 0.0),
                }
        except Exception:
            return None

    async def _fetch_live_dashboard_today_totals(self, tenant_id: Optional[str], plant_id: Optional[str] = None) -> dict[str, Any] | None:
        if not tenant_id:
            return None
        base = (settings.DEVICE_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            return None
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await internal_get(
                    client,
                    f"{base}/api/v1/devices/dashboard/today-loss-breakdown",
                    service_name="energy-service",
                    tenant_id=tenant_id,
                    params={"tenant_id": tenant_id, **({"plant_id": plant_id} if plant_id else {})},
                )
                if response.status_code != 200:
                    return None
                payload = response.json()
                totals = payload.get("totals") if isinstance(payload, dict) else None
                if not isinstance(totals, dict):
                    return None
                return {
                    "today_energy_kwh": float(totals.get("today_energy_kwh") or 0.0),
                    "today_loss_kwh": float(totals.get("total_loss_kwh") or 0.0),
                }
        except Exception:
            return None

    async def _fetch_live_dashboard_energy_widgets(self, tenant_id: Optional[str]) -> dict[str, Any] | None:
        if not tenant_id:
            return None
        base = (settings.DEVICE_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            return None
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await internal_get(
                    client,
                    f"{base}/api/v1/devices/dashboard/summary",
                    service_name="energy-service",
                    tenant_id=tenant_id,
                    params={"tenant_id": tenant_id},
                )
                if response.status_code != 200:
                    return None
                payload = response.json()
                widgets = payload.get("energy_widgets") if isinstance(payload, dict) else None
                if not isinstance(widgets, dict):
                    return None
                return {
                    "month_energy_kwh": float(widgets.get("month_energy_kwh") or 0.0),
                    "today_energy_kwh": float(widgets.get("today_energy_kwh") or 0.0),
                    "today_loss_kwh": float(widgets.get("today_loss_kwh") or 0.0),
                }
        except Exception:
            return None

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _parse_ts(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _extract_counter(payload: dict[str, Any]) -> Optional[float]:
        for key in ("energy_kwh", "kwh", "energy"):
            val = EnergyEngine._to_float(payload.get(key))
            if val is not None:
                return val
        return None

    @staticmethod
    def _extract_power_kw(payload: dict[str, Any]) -> Optional[float]:
        kw = EnergyEngine._to_float(payload.get("kw"))
        if kw is not None:
            return max(0.0, kw)
        for watts_key in ("power", "active_power"):
            watts = EnergyEngine._to_float(payload.get(watts_key))
            if watts is not None:
                return max(0.0, watts / 1000.0)
        return None

    @staticmethod
    def _extract_current(payload: dict[str, Any]) -> Optional[float]:
        for key in ("current", "phase_current"):
            val = EnergyEngine._to_float(payload.get(key))
            if val is not None:
                return val
        return None

    @staticmethod
    def _extract_voltage(payload: dict[str, Any]) -> Optional[float]:
        for key in ("voltage",):
            val = EnergyEngine._to_float(payload.get(key))
            if val is not None:
                return val
        return None

    @staticmethod
    def _inside_shift(local_ts: datetime, shifts: list[dict[str, Any]]) -> bool:
        if not shifts:
            return False
        weekday = local_ts.weekday()
        minutes = local_ts.hour * 60 + local_ts.minute
        for shift in shifts:
            if not shift.get("is_active", True):
                continue
            day = shift.get("day_of_week")
            start = str(shift.get("shift_start") or "00:00")
            end = str(shift.get("shift_end") or "00:00")
            try:
                sh, sm = [int(v) for v in start.split(":")[:2]]
                eh, em = [int(v) for v in end.split(":")[:2]]
            except Exception:
                continue
            start_m = sh * 60 + sm
            end_m = eh * 60 + em
            if end_m <= start_m:
                if minutes >= start_m and (day is None or day == weekday):
                    return True
                if minutes < end_m and (day is None or day == (weekday - 1) % 7):
                    return True
            else:
                if start_m <= minutes < end_m and (day is None or day == weekday):
                    return True
        return False

    async def _get_or_create_state(self, device_id: str) -> EnergyDeviceState:
        state = await self._session.get(EnergyDeviceState, device_id)
        if state is not None:
            return state
        state = EnergyDeviceState(device_id=device_id, session_state="running", version=0)
        self._session.add(state)
        return state

    @staticmethod
    def _require_tenant_scope(tenant_id: Optional[str], *, device_id: str) -> str:
        tenant_scope = str(tenant_id or "").strip()
        if not tenant_scope:
            raise ValueError(f"tenant_id is required to persist energy aggregates for device {device_id}")
        return tenant_scope

    async def _get_or_create_device_day(self, device_id: str, day_bucket: date, tenant_id: str) -> EnergyDeviceDay:
        row = (
            await self._session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == tenant_id,
                    EnergyDeviceDay.device_id == device_id,
                    EnergyDeviceDay.day == day_bucket,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
        row = EnergyDeviceDay(tenant_id=tenant_id, device_id=device_id, day=day_bucket)
        self._session.add(row)
        return row

    async def _get_or_create_device_month(self, device_id: str, month_bucket: date, tenant_id: str) -> EnergyDeviceMonth:
        row = (
            await self._session.execute(
                select(EnergyDeviceMonth).where(
                    EnergyDeviceMonth.tenant_id == tenant_id,
                    EnergyDeviceMonth.device_id == device_id,
                    EnergyDeviceMonth.month == month_bucket,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
        row = EnergyDeviceMonth(tenant_id=tenant_id, device_id=device_id, month=month_bucket)
        self._session.add(row)
        return row

    async def _get_or_create_fleet_day(self, day_bucket: date, tenant_id: str) -> EnergyFleetDay:
        row = await self._session.get(EnergyFleetDay, {"tenant_id": tenant_id, "day": day_bucket})
        if row is not None:
            return row
        row = EnergyFleetDay(tenant_id=tenant_id, day=day_bucket)
        self._session.add(row)
        return row

    async def _get_or_create_fleet_month(self, month_bucket: date, tenant_id: str) -> EnergyFleetMonth:
        row = await self._session.get(EnergyFleetMonth, {"tenant_id": tenant_id, "month": month_bucket})
        if row is not None:
            return row
        row = EnergyFleetMonth(tenant_id=tenant_id, month=month_bucket)
        self._session.add(row)
        return row

    def _compute_delta(
        self,
        *,
        state: EnergyDeviceState,
        ts: datetime,
        previous_sample: Optional[NormalizedTelemetrySample],
        current_sample: NormalizedTelemetrySample,
        idle_threshold: Optional[float],
        over_threshold: Optional[float],
        shifts: list[dict[str, Any]],
    ) -> DeltaMetrics:
        resolution = compute_interval_energy_delta(
            previous_sample,
            current_sample,
            max_fallback_gap_seconds=settings.MAX_FALLBACK_GAP_SECONDS,
            max_counter_gap_seconds=settings.MAX_FALLBACK_GAP_SECONDS,
        )
        flags: set[str] = set(resolution.quality_flags)
        if previous_sample is None:
            return DeltaMetrics(
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                flags,
                resolution.energy_delta_method,
                resolution.quality_class,
                resolution.reason_code,
                resolution.algorithm_version,
            )

        delta_energy = float(resolution.business_energy_delta_kwh)
        dt_sec = float(resolution.coverage_seconds or 0.0)
        power_kw = (
            float(resolution.comparison_power_kw)
            if resolution.comparison_power_kw is not None
            else current_sample.business_power_w / 1000.0
        )
        current = current_sample.current_a
        voltage = current_sample.voltage_v

        idle = 0.0
        off = 0.0
        over = 0.0
        if delta_energy > 0:
            local_ts = ts.astimezone(_get_platform_tz())
            inside_shift = self._inside_shift(local_ts, shifts)
            running = (power_kw is not None and power_kw > 0) or (current is not None and current > 0)
            if running:
                idle, off, over = split_loss_components(
                    duration_sec=dt_sec,
                    interval_energy_kwh=delta_energy,
                    current_a=current,
                    voltage_v=voltage,
                    pf=current_sample.pf_business,
                    idle_threshold=idle_threshold,
                    over_threshold=over_threshold,
                    inside_shift=inside_shift,
                    power_kw=power_kw if power_kw and power_kw > 0 else None,
                )
        return DeltaMetrics(
            energy_kwh=round(max(0.0, delta_energy), 6),
            idle_kwh=round(max(0.0, idle), 6),
            offhours_kwh=round(max(0.0, off), 6),
            overconsumption_kwh=round(max(0.0, over), 6),
            loss_kwh=round(max(0.0, idle + off + over), 6),
            flags=flags,
            energy_method=resolution.energy_delta_method,
            quality_class=resolution.quality_class,
            reason_code=resolution.reason_code,
            algorithm_version=resolution.algorithm_version,
        )

    async def apply_live_update(
        self,
        device_id: str,
        telemetry: dict[str, Any],
        dynamic_fields: Optional[dict[str, Any]] = None,
        normalized_fields: Optional[dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        meta = await meta_cache.get(device_id, tenant_id)
        tariff = await tariff_cache.get(tenant_id)
        state = await self._get_or_create_state(device_id)
        result = await self._apply_live_update_loaded(
            device_id=device_id,
            telemetry=telemetry,
            dynamic_fields=dynamic_fields,
            normalized_fields=normalized_fields,
            tenant_id=tenant_id,
            state=state,
            meta=meta,
            rate=float((tariff or {}).get("rate") or 0.0),
            persistence_mode="optimistic",
        )
        await self._session.commit()
        return result

    async def apply_live_updates_batch(
        self,
        *,
        tenant_id: Optional[str],
        updates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not updates:
            return []

        ordered_device_ids: list[str] = []
        seen: set[str] = set()
        for update in updates:
            device_id = str((update.get("telemetry") or {}).get("device_id") or "").strip()
            if not device_id or device_id in seen:
                continue
            seen.add(device_id)
            ordered_device_ids.append(device_id)

        state_rows = await self._session.execute(
            select(EnergyDeviceState).where(EnergyDeviceState.device_id.in_(ordered_device_ids)).with_for_update()
        )
        states_by_id = {str(state.device_id): state for state in state_rows.scalars().all()}
        for device_id in ordered_device_ids:
            if device_id not in states_by_id:
                state = EnergyDeviceState(device_id=device_id, session_state="running", version=0)
                self._session.add(state)
                states_by_id[device_id] = state
        await self._session.flush()

        meta_rows = await asyncio.gather(*[meta_cache.get(device_id, tenant_id) for device_id in ordered_device_ids])
        meta_by_id = {device_id: meta for device_id, meta in zip(ordered_device_ids, meta_rows, strict=False)}
        tariff = await tariff_cache.get(tenant_id)
        rate = float((tariff or {}).get("rate") or 0.0)

        local_tz = _get_platform_tz()
        tenant_scope = self._require_tenant_scope(tenant_id, device_id=ordered_device_ids[0]) if ordered_device_ids else None
        day_keys: set[tuple[str, str, date]] = set()
        month_keys: set[tuple[str, str, date]] = set()
        fleet_days: set[tuple[str, date]] = set()
        fleet_months: set[tuple[str, date]] = set()
        for update in updates:
            telemetry = update.get("telemetry") or {}
            device_id = str(telemetry.get("device_id") or "").strip()
            if not device_id:
                continue
            ts = self._parse_ts(telemetry.get("timestamp"))
            day_bucket = ts.astimezone(local_tz).date()
            month_bucket = day_bucket.replace(day=1)
            if tenant_scope is None:
                raise ValueError(f"tenant_id is required to persist energy aggregates for device {device_id}")
            day_keys.add((tenant_scope, device_id, day_bucket))
            month_keys.add((tenant_scope, device_id, month_bucket))
            fleet_days.add((tenant_scope, day_bucket))
            fleet_months.add((tenant_scope, month_bucket))

        device_day_rows = (
            await self._session.execute(
                select(EnergyDeviceDay).where(
                    tuple_(EnergyDeviceDay.tenant_id, EnergyDeviceDay.device_id, EnergyDeviceDay.day).in_(list(day_keys))
                )
            )
        ).scalars().all() if day_keys else []
        device_month_rows = (
            await self._session.execute(
                select(EnergyDeviceMonth).where(
                    tuple_(EnergyDeviceMonth.tenant_id, EnergyDeviceMonth.device_id, EnergyDeviceMonth.month).in_(list(month_keys))
                )
            )
        ).scalars().all() if month_keys else []
        fleet_day_rows = (
            await self._session.execute(
                select(EnergyFleetDay).where(tuple_(EnergyFleetDay.tenant_id, EnergyFleetDay.day).in_(list(fleet_days)))
            )
        ).scalars().all() if fleet_days else []
        fleet_month_rows = (
            await self._session.execute(
                select(EnergyFleetMonth).where(
                    tuple_(EnergyFleetMonth.tenant_id, EnergyFleetMonth.month).in_(list(fleet_months))
                )
            )
        ).scalars().all() if fleet_months else []

        device_days = {(row.tenant_id, row.device_id, row.day): row for row in device_day_rows}
        device_months = {(row.tenant_id, row.device_id, row.month): row for row in device_month_rows}
        fleet_days_by_day = {(row.tenant_id, row.day): row for row in fleet_day_rows}
        fleet_months_by_month = {(row.tenant_id, row.month): row for row in fleet_month_rows}

        results: list[dict[str, Any] | None] = [None] * len(updates)
        processable: list[tuple[int, str, dict[str, Any]]] = []
        for index, update in enumerate(updates):
            telemetry = update.get("telemetry") or {}
            device_id = str(telemetry.get("device_id") or "").strip()
            if not device_id:
                results[index] = {
                    "success": False,
                    "device_id": "",
                    "error": "device_id required",
                    "error_code": "DEVICE_ID_REQUIRED",
                    "retryable": False,
                }
                continue
            processable.append((index, device_id, update))

        async def _apply_one(device_id: str, update: dict[str, Any]) -> dict[str, Any]:
            return await self._apply_live_update_loaded(
                device_id=device_id,
                telemetry=update.get("telemetry") or {},
                dynamic_fields=update.get("dynamic_fields"),
                normalized_fields=update.get("normalized_fields"),
                tenant_id=tenant_id,
                state=states_by_id[device_id],
                meta=meta_by_id.get(device_id) or {},
                rate=rate,
                persistence_mode="locked",
                device_day_rows=device_days,
                device_month_rows=device_months,
                fleet_day_rows=fleet_days_by_day,
                fleet_month_rows=fleet_months_by_month,
            )

        chunk_size = max(1, int(settings.ENERGY_BATCH_CHUNK_SIZE))
        for start in range(0, len(processable), chunk_size):
            chunk = processable[start:start + chunk_size]
            chunk_failed = False

            try:
                async with self._session.begin_nested():
                    for result_index, device_id, update in chunk:
                        data = await _apply_one(device_id, update)
                        results[result_index] = {
                            "success": True,
                            "device_id": device_id,
                            "data": data,
                            "retryable": False,
                        }
            except Exception:
                chunk_failed = True

            if not chunk_failed:
                continue

            for result_index, device_id, update in chunk:
                try:
                    async with self._session.begin_nested():
                        data = await _apply_one(device_id, update)
                    results[result_index] = {
                        "success": True,
                        "device_id": device_id,
                        "data": data,
                        "retryable": False,
                    }
                except Exception as exc:
                    results[result_index] = {
                        "success": False,
                        "device_id": device_id,
                        "error": str(exc),
                        "error_code": "ENERGY_LIVE_UPDATE_ERROR",
                        "retryable": True,
                    }

        await self._session.commit()
        return [item for item in results if item is not None]

    async def _apply_live_update_loaded(
        self,
        *,
        device_id: str,
        telemetry: dict[str, Any],
        dynamic_fields: Optional[dict[str, Any]],
        normalized_fields: Optional[dict[str, Any]],
        tenant_id: Optional[str],
        state: EnergyDeviceState,
        meta: dict[str, Any],
        rate: float,
        persistence_mode: str,
        device_day_rows: dict[tuple[str, str, date], EnergyDeviceDay] | None = None,
        device_month_rows: dict[tuple[str, str, date], EnergyDeviceMonth] | None = None,
        fleet_day_rows: dict[tuple[str, date], EnergyFleetDay] | None = None,
        fleet_month_rows: dict[tuple[str, date], EnergyFleetMonth] | None = None,
    ) -> dict[str, Any]:
        merged = dict(telemetry or {})
        if dynamic_fields:
            merged.update(dynamic_fields)

        idle_threshold = self._to_float(meta.get("idle_threshold"))
        over_threshold = self._to_float(meta.get("over_threshold"))
        shifts = meta.get("shifts") or []
        config = DevicePowerConfig(
            energy_flow_mode=str(meta.get("energy_flow_mode") or "consumption_only"),
            polarity_mode=str(meta.get("polarity_mode") or "normal"),
        )
        current_sample = normalize_telemetry_sample(merged, config)
        ts = current_sample.timestamp

        previous_sample = None
        if state.last_ts is not None:
            previous_sample = normalize_telemetry_sample(
                {
                    "timestamp": state.last_ts.isoformat(),
                    "energy_kwh": state.last_energy_counter,
                    "power_kw": state.last_power_kw,
                },
                config,
            )
        delta = self._compute_delta(
            state=state,
            ts=ts,
            previous_sample=previous_sample,
            current_sample=current_sample,
            idle_threshold=idle_threshold,
            over_threshold=over_threshold,
            shifts=shifts,
        )
        if "duplicate_or_out_of_order" in delta.flags:
            return {
                "device_id": device_id,
                "device_name": meta.get("device_name") or device_id,
                "ts": ts.isoformat(),
                "delta_energy_kwh": 0.0,
                "delta_loss_kwh": 0.0,
                "quality_flags": sorted(delta.flags),
                "energy_debug": {
                    "energy_method": delta.energy_method,
                    "quality_class": delta.quality_class,
                    "reason_code": delta.reason_code,
                    "algorithm_version": delta.algorithm_version,
                },
                "version": int(state.version or 0),
                "freshness_ts": datetime.now(timezone.utc).isoformat(),
                "idempotent_drop": True,
            }

        local_tz = _get_platform_tz()
        day_bucket = ts.astimezone(local_tz).date()
        month_bucket = day_bucket.replace(day=1)

        current_version = int(state.version or 0)
        if persistence_mode == "locked":
            state.last_ts = ts
            if current_sample.energy_counter_kwh is not None:
                state.last_energy_counter = current_sample.energy_counter_kwh
            if current_sample.business_power_w > 0:
                state.last_power_kw = current_sample.business_power_w / 1000.0
            state.last_day_bucket = day_bucket
            state.last_month_bucket = month_bucket
            state.session_state = "running"
            state.version = current_version + 1
            state.updated_at = datetime.utcnow()
            await self._session.flush()
        else:
            success = await update_live_state_with_lock(
                self._session,
                device_id,
                {
                    "last_ts": ts,
                    "last_energy_counter": current_sample.energy_counter_kwh if current_sample.energy_counter_kwh is not None else state.last_energy_counter,
                    "last_power_kw": (current_sample.business_power_w / 1000.0) if current_sample.business_power_w > 0 else state.last_power_kw,
                    "last_day_bucket": day_bucket,
                    "last_month_bucket": month_bucket,
                    "session_state": "running",
                },
            )
            if not success:
                await self._session.rollback()
                return {
                    "device_id": device_id,
                    "device_name": meta.get("device_name") or device_id,
                    "ts": ts.isoformat(),
                    "delta_energy_kwh": 0.0,
                    "delta_loss_kwh": 0.0,
                    "quality_flags": sorted(delta.flags | {"optimistic_lock_skipped"}),
                    "energy_debug": {
                        "energy_method": delta.energy_method,
                        "quality_class": delta.quality_class,
                        "reason_code": delta.reason_code,
                        "algorithm_version": delta.algorithm_version,
                    },
                    "version": current_version,
                    "freshness_ts": datetime.now(timezone.utc).isoformat(),
                    "idempotent_drop": True,
                }

        if delta.energy_kwh > 0:
            tenant_scope = self._require_tenant_scope(tenant_id, device_id=device_id)
            dd = await self._get_or_create_device_day_cached(tenant_scope, device_id, day_bucket, device_day_rows)
            dm = await self._get_or_create_device_month_cached(tenant_scope, device_id, month_bucket, device_month_rows)
            fd = await self._get_or_create_fleet_day_cached(tenant_scope, day_bucket, fleet_day_rows)
            fm = await self._get_or_create_fleet_month_cached(tenant_scope, month_bucket, fleet_month_rows)

            await self._session.flush()

            cost_increment = delta.energy_kwh * rate
            loss_cost_increment = delta.loss_kwh * rate

            await self._session.execute(
                update(EnergyDeviceDay)
                .where(
                    EnergyDeviceDay.tenant_id == tenant_scope,
                    EnergyDeviceDay.device_id == device_id,
                    EnergyDeviceDay.day == day_bucket,
                )
                .values(
                    energy_kwh=EnergyDeviceDay.energy_kwh + delta.energy_kwh,
                    idle_kwh=EnergyDeviceDay.idle_kwh + delta.idle_kwh,
                    offhours_kwh=EnergyDeviceDay.offhours_kwh + delta.offhours_kwh,
                    overconsumption_kwh=EnergyDeviceDay.overconsumption_kwh + delta.overconsumption_kwh,
                    loss_kwh=EnergyDeviceDay.loss_kwh + delta.loss_kwh,
                    energy_cost_inr=func.round(EnergyDeviceDay.energy_cost_inr + cost_increment, 6),
                    loss_cost_inr=func.round(EnergyDeviceDay.loss_cost_inr + loss_cost_increment, 6),
                    version=EnergyDeviceDay.version + 1,
                )
            )
            await self._session.execute(
                update(EnergyDeviceMonth)
                .where(
                    EnergyDeviceMonth.tenant_id == tenant_scope,
                    EnergyDeviceMonth.device_id == device_id,
                    EnergyDeviceMonth.month == month_bucket,
                )
                .values(
                    energy_kwh=EnergyDeviceMonth.energy_kwh + delta.energy_kwh,
                    idle_kwh=EnergyDeviceMonth.idle_kwh + delta.idle_kwh,
                    offhours_kwh=EnergyDeviceMonth.offhours_kwh + delta.offhours_kwh,
                    overconsumption_kwh=EnergyDeviceMonth.overconsumption_kwh + delta.overconsumption_kwh,
                    loss_kwh=EnergyDeviceMonth.loss_kwh + delta.loss_kwh,
                    energy_cost_inr=func.round(EnergyDeviceMonth.energy_cost_inr + cost_increment, 6),
                    loss_cost_inr=func.round(EnergyDeviceMonth.loss_cost_inr + loss_cost_increment, 6),
                    version=EnergyDeviceMonth.version + 1,
                )
            )
            await self._session.execute(
                update(EnergyFleetDay)
                .where(
                    EnergyFleetDay.tenant_id == tenant_scope,
                    EnergyFleetDay.day == day_bucket,
                )
                .values(
                    energy_kwh=EnergyFleetDay.energy_kwh + delta.energy_kwh,
                    idle_kwh=EnergyFleetDay.idle_kwh + delta.idle_kwh,
                    offhours_kwh=EnergyFleetDay.offhours_kwh + delta.offhours_kwh,
                    overconsumption_kwh=EnergyFleetDay.overconsumption_kwh + delta.overconsumption_kwh,
                    loss_kwh=EnergyFleetDay.loss_kwh + delta.loss_kwh,
                    energy_cost_inr=func.round(EnergyFleetDay.energy_cost_inr + cost_increment, 6),
                    loss_cost_inr=func.round(EnergyFleetDay.loss_cost_inr + loss_cost_increment, 6),
                    version=EnergyFleetDay.version + 1,
                )
            )
            await self._session.execute(
                update(EnergyFleetMonth)
                .where(
                    EnergyFleetMonth.tenant_id == tenant_scope,
                    EnergyFleetMonth.month == month_bucket,
                )
                .values(
                    energy_kwh=EnergyFleetMonth.energy_kwh + delta.energy_kwh,
                    idle_kwh=EnergyFleetMonth.idle_kwh + delta.idle_kwh,
                    offhours_kwh=EnergyFleetMonth.offhours_kwh + delta.offhours_kwh,
                    overconsumption_kwh=EnergyFleetMonth.overconsumption_kwh + delta.overconsumption_kwh,
                    loss_kwh=EnergyFleetMonth.loss_kwh + delta.loss_kwh,
                    energy_cost_inr=func.round(EnergyFleetMonth.energy_cost_inr + cost_increment, 6),
                    loss_cost_inr=func.round(EnergyFleetMonth.loss_cost_inr + loss_cost_increment, 6),
                    version=EnergyFleetMonth.version + 1,
                )
            )

            await self._session.refresh(dd)
            await self._session.refresh(dm)

            quality = set(json.loads(dd.quality_flags or "[]"))
            quality.update(delta.flags)
            dd.quality_flags = json.dumps(sorted(quality))
            dm_quality = set(json.loads(dm.quality_flags or "[]"))
            dm_quality.update(delta.flags)
            dm.quality_flags = json.dumps(sorted(dm_quality))

        return {
            "device_id": device_id,
            "device_name": meta.get("device_name") or device_id,
            "ts": ts.isoformat(),
            "delta_energy_kwh": delta.energy_kwh,
            "delta_loss_kwh": delta.loss_kwh,
            "quality_flags": sorted(delta.flags),
            "energy_debug": {
                "energy_method": delta.energy_method,
                "quality_class": delta.quality_class,
                "reason_code": delta.reason_code,
                "algorithm_version": delta.algorithm_version,
            },
            "version": current_version + 1,
            "freshness_ts": datetime.now(timezone.utc).isoformat(),
        }

    async def _get_or_create_device_day_cached(
        self,
        tenant_id: str,
        device_id: str,
        day_bucket: date,
        cache: dict[tuple[str, str, date], EnergyDeviceDay] | None,
    ) -> EnergyDeviceDay:
        if cache is None:
            return await self._get_or_create_device_day(device_id, day_bucket, tenant_id)
        key = (tenant_id, device_id, day_bucket)
        row = cache.get(key)
        if row is None:
            row = EnergyDeviceDay(tenant_id=tenant_id, device_id=device_id, day=day_bucket)
            self._session.add(row)
            cache[key] = row
        return row

    async def _get_or_create_device_month_cached(
        self,
        tenant_id: str,
        device_id: str,
        month_bucket: date,
        cache: dict[tuple[str, str, date], EnergyDeviceMonth] | None,
    ) -> EnergyDeviceMonth:
        if cache is None:
            return await self._get_or_create_device_month(device_id, month_bucket, tenant_id)
        key = (tenant_id, device_id, month_bucket)
        row = cache.get(key)
        if row is None:
            row = EnergyDeviceMonth(tenant_id=tenant_id, device_id=device_id, month=month_bucket)
            self._session.add(row)
            cache[key] = row
        return row

    async def _get_or_create_fleet_day_cached(
        self,
        tenant_id: str,
        day_bucket: date,
        cache: dict[tuple[str, date], EnergyFleetDay] | None,
    ) -> EnergyFleetDay:
        if cache is None:
            return await self._get_or_create_fleet_day(day_bucket, tenant_id)
        key = (tenant_id, day_bucket)
        row = cache.get(key)
        if row is None:
            row = EnergyFleetDay(tenant_id=tenant_id, day=day_bucket)
            self._session.add(row)
            cache[key] = row
        return row

    async def _get_or_create_fleet_month_cached(
        self,
        tenant_id: str,
        month_bucket: date,
        cache: dict[tuple[str, date], EnergyFleetMonth] | None,
    ) -> EnergyFleetMonth:
        if cache is None:
            return await self._get_or_create_fleet_month(month_bucket, tenant_id)
        key = (tenant_id, month_bucket)
        row = cache.get(key)
        if row is None:
            row = EnergyFleetMonth(tenant_id=tenant_id, month=month_bucket)
            self._session.add(row)
            cache[key] = row
        return row

    async def apply_device_lifecycle(
        self,
        device_id: str,
        status: str,
        at: Optional[datetime] = None,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if tenant_id:
            ownership = await self._session.execute(
                select(EnergyDeviceDay.id).where(
                    EnergyDeviceDay.device_id == device_id,
                    EnergyDeviceDay.tenant_id == tenant_id,
                ).limit(1)
            )
            if ownership.scalar_one_or_none() is None:
                return {
                    "device_id": device_id,
                    "error": "device_not_found_for_tenant",
                    "session_state": None,
                    "version": 0,
                }

        state = await self._get_or_create_state(device_id)
        current_version = int(state.version or 0)
        updates = {"session_state": status}
        if at is not None:
            updates["last_ts"] = at
        success = await update_live_state_with_lock(self._session, device_id, updates)
        if not success:
            await self._session.rollback()
            return {"device_id": device_id, "session_state": state.session_state, "version": current_version}
        await self._session.commit()
        return {"device_id": device_id, "session_state": status, "version": current_version + 1}

    async def get_summary(self, tenant_id: Optional[str] = None) -> dict[str, Any]:
        local_tz = _get_platform_tz()
        now_local = datetime.now(local_tz)
        day_bucket = now_local.date()
        month_bucket = day_bucket.replace(day=1)
        tariff = await tariff_cache.get(tenant_id)
        rate = self._safe_rate(tariff)

        if not tenant_id:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "freshness_ts": datetime.now(timezone.utc).isoformat(),
                "version": 0,
                "currency": self._currency(tariff),
                "energy_widgets": {
                    "today_energy_kwh": 0.0,
                    "today_energy_cost_inr": 0.0,
                    "today_loss_kwh": 0.0,
                    "today_loss_cost_inr": 0.0,
                    "month_energy_kwh": 0.0,
                    "month_energy_cost_inr": 0.0,
                    "currency": self._currency(tariff),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
            }

        fd_rows = (
            await self._session.execute(
                select(EnergyFleetDay).where(
                    EnergyFleetDay.tenant_id == tenant_id,
                    EnergyFleetDay.day == day_bucket,
                )
            )
        ).scalars().all()
        fm_rows = (
            await self._session.execute(
                select(EnergyFleetMonth).where(
                    EnergyFleetMonth.tenant_id == tenant_id,
                    EnergyFleetMonth.month == month_bucket,
                )
            )
        ).scalars().all()
        fd = fd_rows[0] if fd_rows else None
        fm = fm_rows[0] if fm_rows else None
        today_energy = float(fd.energy_kwh) if fd else 0.0
        today_loss = float(fd.loss_kwh) if fd else 0.0
        month_energy = float(fm.energy_kwh) if fm else 0.0
        today_cost = self._stored_energy_cost(fd, rate) if fd else 0.0
        today_loss_cost = self._stored_loss_cost(fd, rate) if fd else 0.0
        month_cost = self._stored_energy_cost(fm, rate) if fm else 0.0
        live_widgets = await self._fetch_live_dashboard_energy_widgets(tenant_id)
        if live_widgets is not None:
            persisted_today_cost = today_cost
            persisted_today_kwh = today_energy
            persisted_today_loss = today_loss
            persisted_today_loss_cost = today_loss_cost
            today_energy = float(live_widgets.get("today_energy_kwh") or today_energy)
            today_loss = float(live_widgets.get("today_loss_kwh") or today_loss)
            month_energy = float(live_widgets.get("month_energy_kwh") or month_energy)
            delta_kwh = max(0.0, today_energy - persisted_today_kwh)
            delta_loss_kwh = max(0.0, today_loss - persisted_today_loss)
            today_cost = persisted_today_cost + (delta_kwh * rate)
            today_loss_cost = persisted_today_loss_cost + (delta_loss_kwh * rate)
            month_cost = max(0.0, month_cost - persisted_today_cost + today_cost)
        version = max(int(fd.version if fd else 0), int(fm.version if fm else 0))

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "freshness_ts": datetime.now(timezone.utc).isoformat(),
            "version": version,
            "currency": self._currency(tariff),
            "energy_widgets": {
                "today_energy_kwh": round(today_energy, 4),
                "today_energy_cost_inr": round(today_cost, 4),
                "today_loss_kwh": round(today_loss, 4),
                "today_loss_cost_inr": round(today_loss_cost, 4),
                "month_energy_kwh": round(month_energy, 4),
                "month_energy_cost_inr": round(month_cost, 4),
                "currency": self._currency(tariff),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    async def get_today_loss_breakdown(self, tenant_id: Optional[str] = None) -> dict[str, Any]:
        day_bucket = datetime.now(_get_platform_tz()).date()
        tariff = await tariff_cache.get(tenant_id)
        rate = self._safe_rate(tariff)

        if not tenant_id:
            zero_totals = {
                "idle_kwh": 0.0,
                "off_hours_kwh": 0.0,
                "overconsumption_kwh": 0.0,
                "total_loss_kwh": 0.0,
                "today_energy_kwh": 0.0,
                "idle_cost_inr": 0.0,
                "off_hours_cost_inr": 0.0,
                "overconsumption_cost_inr": 0.0,
                "total_loss_cost_inr": 0.0,
                "today_energy_cost_inr": 0.0,
            }
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "freshness_ts": datetime.now(timezone.utc).isoformat(),
                "version": 0,
                "currency": self._currency(tariff),
                "totals": zero_totals,
                "rows": [],
                "data_quality": "ok",
            }

        query = select(EnergyDeviceDay).where(
            EnergyDeviceDay.day == day_bucket,
            EnergyDeviceDay.tenant_id == tenant_id,
        )
        rows = (await self._session.execute(query)).scalars().all()

        row_map: dict[str, dict[str, Any]] = {
            str(row.device_id): {
                "device_id": str(row.device_id),
                "version": int(row.version or 0),
                "energy_kwh": float(row.energy_kwh or 0.0),
                "energy_cost_inr": float(row.energy_cost_inr or 0.0) if getattr(row, "energy_cost_inr", None) is not None else None,
                "idle_kwh": float(row.idle_kwh or 0.0),
                "offhours_kwh": float(row.offhours_kwh or 0.0),
                "overconsumption_kwh": float(row.overconsumption_kwh or 0.0),
                "loss_kwh": float(row.loss_kwh or 0.0),
                "loss_cost_inr": float(row.loss_cost_inr or 0.0) if getattr(row, "loss_cost_inr", None) is not None else None,
                "status": "computed",
                "reason": None,
            }
            for row in rows
        }

        allowed_device_ids = await self._get_allowed_device_ids(tenant_id) if tenant_id else None
        live_overlay_ids = list(allowed_device_ids) if allowed_device_ids is not None else list(row_map.keys())
        if tenant_id and live_overlay_ids:
            live_totals = await asyncio.gather(
                *(self._fetch_live_current_day_totals(device_id, tenant_id) for device_id in live_overlay_ids)
            )
            for device_id, live_today in zip(live_overlay_ids, live_totals):
                if not live_today or live_today.get("date") != day_bucket.isoformat():
                    continue
                existing = row_map.get(device_id, {"version": 0})
                row_map[device_id] = {
                    "device_id": device_id,
                    "version": int(existing.get("version") or 0),
                    "energy_kwh": float(live_today.get("energy_kwh") or 0.0),
                    "energy_cost_inr": None,
                    "idle_kwh": float(live_today.get("idle_kwh") or 0.0),
                    "offhours_kwh": float(live_today.get("offhours_kwh") or 0.0),
                    "overconsumption_kwh": float(live_today.get("overconsumption_kwh") or 0.0),
                    "loss_kwh": float(live_today.get("loss_kwh") or 0.0),
                    "loss_cost_inr": None,
                    "status": "computed",
                    "reason": "live_projection_overlay",
                }
            rows = [SimpleNamespace(**item) for item in row_map.values()]

        table = []
        totals = {
            "idle_kwh": 0.0,
            "off_hours_kwh": 0.0,
            "overconsumption_kwh": 0.0,
            "total_loss_kwh": 0.0,
            "today_energy_kwh": 0.0,
            "idle_cost_inr": 0.0,
            "off_hours_cost_inr": 0.0,
            "overconsumption_cost_inr": 0.0,
            "total_loss_cost_inr": 0.0,
            "today_energy_cost_inr": 0.0,
        }

        device_ids_in_table = [row.device_id for row in rows]
        meta_results = await asyncio.gather(
            *[meta_cache.get(did, tenant_id) for did in device_ids_in_table]
        )
        device_names = {
            did: meta.get("device_name") or did
            for did, meta in zip(device_ids_in_table, meta_results)
        }

        for row in rows:
            idle = float(row.idle_kwh or 0.0)
            off = float(row.offhours_kwh or 0.0)
            over = float(row.overconsumption_kwh or 0.0)
            loss = float(row.loss_kwh or 0.0)
            energy = float(row.energy_kwh or 0.0)

            row_loss_cost = self._stored_loss_cost(row, rate)

            if row_loss_cost > 0 and loss > 0:
                idle_cost = round(row_loss_cost * (idle / loss), 4)
                off_cost = round(row_loss_cost * (off / loss), 4) if off > 0 else 0.0
                over_cost = round(row_loss_cost * (over / loss), 4) if over > 0 else 0.0
                bucket_sum = idle_cost + off_cost + over_cost
                remainder = round(row_loss_cost - bucket_sum, 4)
                if abs(remainder) >= 0.0001:
                    buckets = [("idle", idle_cost, idle), ("over", over_cost, over), ("off", off_cost, off)]
                    largest = max(buckets, key=lambda b: b[2])
                    if largest[0] == "idle":
                        idle_cost = round(idle_cost + remainder, 4)
                    elif largest[0] == "over":
                        over_cost = round(over_cost + remainder, 4)
                    else:
                        off_cost = round(off_cost + remainder, 4)
            else:
                idle_cost = round(idle * rate, 4)
                off_cost = round(off * rate, 4)
                over_cost = round(over * rate, 4)

            totals["idle_kwh"] += idle
            totals["off_hours_kwh"] += off
            totals["overconsumption_kwh"] += over
            totals["total_loss_kwh"] += loss
            totals["today_energy_kwh"] += energy
            totals["idle_cost_inr"] += idle_cost
            totals["off_hours_cost_inr"] += off_cost
            totals["overconsumption_cost_inr"] += over_cost
            totals["total_loss_cost_inr"] += row_loss_cost
            totals["today_energy_cost_inr"] += self._stored_energy_cost(row, rate)

            table.append(
                {
                    "device_id": row.device_id,
                    "device_name": device_names.get(row.device_id, row.device_id),
                    "idle_kwh": round(idle, 4),
                    "idle_cost_inr": idle_cost,
                    "off_hours_kwh": round(off, 4),
                    "off_hours_cost_inr": off_cost,
                    "overconsumption_kwh": round(over, 4),
                    "overconsumption_cost_inr": over_cost,
                    "total_loss_kwh": round(loss, 4),
                    "total_loss_cost_inr": round(row_loss_cost, 4),
                    "status": getattr(row, "status", "computed"),
                    "reason": getattr(row, "reason", None),
                }
            )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "freshness_ts": datetime.now(timezone.utc).isoformat(),
            "version": max((int(r.version or 0) for r in rows), default=0),
            "currency": self._currency(tariff),
            "totals": {k: round(v, 4) for k, v in totals.items()},
            "rows": table,
            "data_quality": "ok",
        }

    async def get_monthly_calendar(
        self,
        year: int,
        month: int,
        tenant_id: Optional[str] = None,
        device_ids: Optional[list[str]] = None,
        plant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        month_start = date(year, month, 1)
        next_month = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
        tariff = await tariff_cache.get(tenant_id)
        rate = self._safe_rate(tariff)

        if not tenant_id:
            days = []
            cur = month_start
            while cur < next_month:
                days.append({"date": cur.isoformat(), "energy_kwh": 0.0, "energy_cost_inr": 0.0, "loss_kwh": 0.0, "loss_cost_inr": 0.0})
                cur = cur + timedelta(days=1)
            return {
                "year": year,
                "month": month,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "freshness_ts": datetime.now(timezone.utc).isoformat(),
                "version": 0,
                "currency": self._currency(tariff),
                "summary": {
                    "total_energy_kwh": 0.0,
                    "total_energy_cost_inr": 0.0,
                    "total_loss_kwh": 0.0,
                    "total_loss_cost_inr": 0.0,
                },
                "days": days,
                "data_quality": "ok",
            }

        day_map: dict[date, dict[str, float]] = {}
        if device_ids:
            query = select(EnergyDeviceDay).where(
                EnergyDeviceDay.device_id.in_(device_ids),
                EnergyDeviceDay.day >= month_start,
                EnergyDeviceDay.day < next_month,
            )
        else:
            query = select(EnergyFleetDay).where(
                EnergyFleetDay.tenant_id == tenant_id,
                EnergyFleetDay.day >= month_start,
                EnergyFleetDay.day < next_month,
            )
        rows = (await self._session.execute(query)).scalars().all()
        for row in rows:
            bucket = day_map.setdefault(row.day, {"energy": 0.0, "cost": 0.0, "loss": 0.0, "loss_cost": 0.0, "version": 0})
            bucket["energy"] += float(row.energy_kwh or 0.0)
            bucket["cost"] += self._stored_energy_cost(row, rate)
            bucket["loss"] += float(row.loss_kwh or 0.0)
            bucket["loss_cost"] += self._stored_loss_cost(row, rate)
            bucket["version"] = max(bucket["version"], int(row.version or 0))

        today_local = datetime.now(_get_platform_tz()).date()
        if tenant_id and month_start <= today_local < next_month:
            live_totals = await self._fetch_live_dashboard_today_totals(tenant_id, plant_id=plant_id)
            if live_totals is not None:
                live_today_energy = float(live_totals.get("today_energy_kwh") or 0.0)
                live_today_loss = float(live_totals.get("today_loss_kwh") or 0.0)
                existing = day_map.get(today_local, {"energy": 0.0, "cost": 0.0, "loss": 0.0, "loss_cost": 0.0, "version": 0})
                persisted_energy = float(existing.get("energy") or 0.0)
                persisted_cost = float(existing.get("cost") or 0.0)
                persisted_loss = float(existing.get("loss") or 0.0)
                persisted_loss_cost = float(existing.get("loss_cost") or 0.0)
                delta_kwh = max(0.0, live_today_energy - persisted_energy)
                delta_loss_kwh = max(0.0, live_today_loss - persisted_loss)
                day_map[today_local] = {
                    "energy": round(persisted_energy + delta_kwh, 4),
                    "cost": persisted_cost + (delta_kwh * rate),
                    "loss": round(persisted_loss + delta_loss_kwh, 4),
                    "loss_cost": persisted_loss_cost + (delta_loss_kwh * rate),
                    "version": int(existing.get("version") or 0),
                }

        days = []
        cur = month_start
        total_kwh = 0.0
        total_cost = 0.0
        total_loss = 0.0
        total_loss_cost = 0.0
        while cur < next_month:
            row = day_map.get(cur)
            energy = float(row["energy"]) if row else 0.0
            cost = float(row["cost"]) if row else 0.0
            loss = float(row["loss"]) if row else 0.0
            loss_cost = float(row["loss_cost"]) if row else 0.0
            total_kwh += energy
            total_cost += cost
            total_loss += loss
            total_loss_cost += loss_cost
            days.append({
                "date": cur.isoformat(),
                "energy_kwh": round(energy, 4),
                "energy_cost_inr": round(cost, 4),
                "loss_kwh": round(loss, 4),
                "loss_cost_inr": round(loss_cost, 4),
            })
            cur = cur + timedelta(days=1)

        return {
            "year": year,
            "month": month,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "freshness_ts": datetime.now(timezone.utc).isoformat(),
            "version": max((int(r["version"]) for r in day_map.values()), default=0),
            "currency": self._currency(tariff),
            "summary": {
                "total_energy_kwh": round(total_kwh, 4),
                "total_energy_cost_inr": round(total_cost, 4),
                "total_loss_kwh": round(total_loss, 4),
                "total_loss_cost_inr": round(total_loss_cost, 4),
            },
            "days": days,
            "data_quality": "ok",
        }

    async def get_device_range(self, device_id: str, start: date, end: date, tenant_id: Optional[str] = None) -> dict[str, Any]:
        if not tenant_id:
            return {
                "device_id": device_id,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "totals": {
                    "energy_kwh": 0.0,
                    "loss_kwh": 0.0,
                    "idle_kwh": 0.0,
                    "offhours_kwh": 0.0,
                    "overconsumption_kwh": 0.0,
                    "energy_cost_inr": 0.0,
                    "loss_cost_inr": 0.0,
                },
                "days": [],
                "version": 0,
                "freshness_ts": datetime.now(timezone.utc).isoformat(),
                "currency": "INR",
            }

        query = select(EnergyDeviceDay).where(
            EnergyDeviceDay.device_id == device_id,
            EnergyDeviceDay.tenant_id == tenant_id,
            EnergyDeviceDay.day >= start,
            EnergyDeviceDay.day <= end,
        )
        rows = (
            await self._session.execute(query)
        ).scalars().all()

        total_energy = sum(float(r.energy_kwh or 0.0) for r in rows)
        total_loss = sum(float(r.loss_kwh or 0.0) for r in rows)
        idle = sum(float(r.idle_kwh or 0.0) for r in rows)
        off = sum(float(r.offhours_kwh or 0.0) for r in rows)
        over = sum(float(r.overconsumption_kwh or 0.0) for r in rows)
        tariff = await tariff_cache.get(tenant_id)
        rate = self._safe_rate(tariff)

        day_map = {
            r.day: {
                "date": r.day.isoformat(),
                "energy_kwh": float(r.energy_kwh or 0.0),
                "energy_cost_inr": self._stored_energy_cost(r, rate),
                "idle_kwh": float(r.idle_kwh or 0.0),
                "offhours_kwh": float(r.offhours_kwh or 0.0),
                "overconsumption_kwh": float(r.overconsumption_kwh or 0.0),
                "loss_kwh": float(r.loss_kwh or 0.0),
                "loss_cost_inr": self._stored_loss_cost(r, rate),
                "quality_flags": json.loads(r.quality_flags or "[]"),
                "version": int(r.version or 0),
            }
            for r in rows
        }

        today_local = datetime.now(_get_platform_tz()).date()
        if start <= today_local <= end:
            live_today = await self._fetch_live_current_day_totals(device_id, tenant_id)
            if live_today and live_today.get("date") == today_local.isoformat():
                persisted_entry = day_map.get(today_local)
                live_kwh = float(live_today.get("energy_kwh") or 0.0)
                live_loss_kwh = float(live_today.get("loss_kwh") or 0.0)
                persisted_kwh = float(persisted_entry["energy_kwh"]) if persisted_entry else 0.0
                persisted_cost = float(persisted_entry["energy_cost_inr"]) if persisted_entry else 0.0
                persisted_loss_kwh = float(persisted_entry["loss_kwh"]) if persisted_entry else 0.0
                persisted_loss_cost = float(persisted_entry["loss_cost_inr"]) if persisted_entry else 0.0
                persisted_idle_kwh = float(persisted_entry["idle_kwh"]) if persisted_entry else 0.0
                persisted_offhours_kwh = float(persisted_entry["offhours_kwh"]) if persisted_entry else 0.0
                persisted_overconsumption_kwh = float(persisted_entry["overconsumption_kwh"]) if persisted_entry else 0.0
                live_idle_kwh = float(live_today.get("idle_kwh") or 0.0)
                live_offhours_kwh = float(live_today.get("offhours_kwh") or 0.0)
                live_overconsumption_kwh = float(live_today.get("overconsumption_kwh") or 0.0)
                delta_kwh = max(0.0, live_kwh - persisted_kwh)
                delta_loss_kwh = max(0.0, live_loss_kwh - persisted_loss_kwh)
                delta_idle_kwh = max(0.0, live_idle_kwh - persisted_idle_kwh)
                delta_offhours_kwh = max(0.0, live_offhours_kwh - persisted_offhours_kwh)
                delta_overconsumption_kwh = max(0.0, live_overconsumption_kwh - persisted_overconsumption_kwh)
                day_map[today_local] = {
                    "date": today_local.isoformat(),
                    "energy_kwh": round(persisted_kwh + delta_kwh, 4),
                    "energy_cost_inr": persisted_cost + (delta_kwh * rate),
                    "idle_kwh": round(persisted_idle_kwh + delta_idle_kwh, 4),
                    "offhours_kwh": round(persisted_offhours_kwh + delta_offhours_kwh, 4),
                    "overconsumption_kwh": round(persisted_overconsumption_kwh + delta_overconsumption_kwh, 4),
                    "loss_kwh": round(persisted_loss_kwh + delta_loss_kwh, 4),
                    "loss_cost_inr": persisted_loss_cost + (delta_loss_kwh * rate),
                    "quality_flags": ["live_projection_overlay"],
                    "version": max(int(day_map.get(today_local, {}).get("version") or 0), 0),
                }

        ordered_days = [day_map[key] for key in sorted(day_map.keys())]
        total_energy = sum(float(day["energy_kwh"] or 0.0) for day in ordered_days)
        total_loss = sum(float(day["loss_kwh"] or 0.0) for day in ordered_days)
        total_energy_cost = sum(float(day["energy_cost_inr"] or 0.0) for day in ordered_days)
        total_loss_cost = sum(float(day["loss_cost_inr"] or 0.0) for day in ordered_days)
        idle = sum(float(day["idle_kwh"] or 0.0) for day in ordered_days)
        off = sum(float(day["offhours_kwh"] or 0.0) for day in ordered_days)
        over = sum(float(day["overconsumption_kwh"] or 0.0) for day in ordered_days)
        days = [
            {
                "date": day["date"],
                "energy_kwh": round(float(day["energy_kwh"] or 0.0), 4),
                "energy_cost_inr": round(float(day["energy_cost_inr"] or 0.0), 4),
                "idle_kwh": round(float(day["idle_kwh"] or 0.0), 4),
                "offhours_kwh": round(float(day["offhours_kwh"] or 0.0), 4),
                "overconsumption_kwh": round(float(day["overconsumption_kwh"] or 0.0), 4),
                "loss_kwh": round(float(day["loss_kwh"] or 0.0), 4),
                "loss_cost_inr": round(float(day["loss_cost_inr"] or 0.0), 4),
                "quality_flags": list(day.get("quality_flags") or []),
                "version": int(day.get("version") or 0),
            }
            for day in ordered_days
        ]

        return {
            "device_id": device_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "totals": {
                "energy_kwh": round(total_energy, 4),
                "loss_kwh": round(total_loss, 4),
                "idle_kwh": round(idle, 4),
                "offhours_kwh": round(off, 4),
                "overconsumption_kwh": round(over, 4),
                "energy_cost_inr": round(total_energy_cost, 4),
                "loss_cost_inr": round(total_loss_cost, 4),
            },
            "days": days,
            "version": max((int(r.version or 0) for r in rows), default=0),
            "freshness_ts": datetime.now(timezone.utc).isoformat(),
            "currency": self._currency(tariff),
        }
