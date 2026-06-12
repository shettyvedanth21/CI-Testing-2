"""Idle running detection and cost aggregation service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
import logging
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.device import (
    Device,
    DeviceLatestTelemetrySnapshot,
    DeviceLiveState,
    DeviceRecentTelemetrySample,
    IdleRunningLog,
    WasteSiteConfig,
    TELEMETRY_TIMEOUT_SECONDS,
)
from app.services.load_thresholds import (
    DEFAULT_IDLE_THRESHOLD_PCT_OF_FLA,
    ThresholdResolution,
    classify_current_band,
    classify_load_state,
    resolve_device_thresholds,
)
from app.services.runtime_state import resolve_runtime_status
from app.services.shared_http import get_client, request_with_retries
from services.shared.energy_accounting import aggregate_window
from services.shared.tariff_client import fetch_tenant_tariff
from services.shared.tenant_context import TenantContext, build_internal_headers

logger = logging.getLogger(__name__)


def _get_platform_tz() -> ZoneInfo:
    return ZoneInfo(settings.PLATFORM_TIMEZONE)


class ThresholdConfigurationError(ValueError):
    """Raised when idle and overconsumption thresholds create an invalid overlap."""


class TariffCache:
    """In-memory tariff cache with 60s TTL to reduce cross-service calls."""

    _value: dict[str | None, dict[str, Any]] = {
        None: {"configured": False, "rate": None, "currency": "INR", "updated_at": None, "stale": False, "cache": "empty"}
    }
    _expires_at: dict[str | None, datetime] = {}
    _ttl_seconds: int = 60

    @classmethod
    async def get(cls, tenant_id: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        expires_at = cls._expires_at.get(tenant_id)
        if expires_at and now < expires_at:
            return {**cls._value.get(tenant_id, cls._value[None]), "cache": "hit"}

        url = f"{settings.REPORTING_SERVICE_BASE_URL}/api/v1/settings/tariff"
        try:
            logger.debug("idle_running_tariff_request", extra={"url": url, "tenant_id": tenant_id})
            client = await get_client(settings.REPORTING_SERVICE_BASE_URL)
            tariff = await fetch_tenant_tariff(
                client,
                settings.REPORTING_SERVICE_BASE_URL,
                tenant_id,
                service_name="device-service",
            )
            cls._value[tenant_id] = {
                "configured": bool(tariff.get("configured")),
                "rate": float(tariff.get("rate") or 0.0) if tariff.get("configured") else None,
                "currency": tariff.get("currency") or "INR",
                "updated_at": None,
                "stale": False,
                "source": tariff.get("source"),
            }
            cls._expires_at[tenant_id] = now + timedelta(seconds=cls._ttl_seconds)
            return {**cls._value.get(tenant_id, cls._value[None]), "cache": "miss"}
        except httpx.ConnectError as exc:
            logger.error(
                "idle_running_tariff_connect_error",
                extra={"url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
        except httpx.ConnectTimeout as exc:
            logger.error(
                "idle_running_tariff_connect_timeout",
                extra={"url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
        except Exception as exc:
            logger.error(
                "idle_running_tariff_error",
                extra={"url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            if cls._value.get(tenant_id) or cls._value.get(None):
                stale = {**(cls._value.get(tenant_id) or cls._value[None]), "stale": True, "cache": "stale"}
                return stale
            return {
                "configured": False,
                "rate": None,
                "currency": "INR",
                "updated_at": None,
                "stale": True,
                "cache": "empty",
            }


@dataclass
class MappedTelemetry:
    current: Optional[float]
    voltage: Optional[float]
    power: Optional[float]
    power_factor: Optional[float]
    current_field: Optional[str]
    voltage_field: Optional[str]


class IdleRunningService:
    def __init__(self, session: AsyncSession, ctx: TenantContext | None = None):
        self._session = session
        self._ctx = ctx

    @staticmethod
    def detect_device_state(current: Optional[float], voltage: Optional[float], threshold: Optional[float]) -> str:
        thresholds = ThresholdResolution(
            full_load_current_a=threshold,
            idle_threshold_pct_of_fla=1.0,
            derived_idle_threshold_a=threshold,
            derived_overconsumption_threshold_a=threshold,
            configured=threshold is not None and threshold > 0,
            source="legacy",
        )
        return classify_load_state(current, voltage, thresholds)

    @staticmethod
    def detect_device_state_with_thresholds(
        current: Optional[float],
        voltage: Optional[float],
        thresholds: ThresholdResolution,
    ) -> str:
        return classify_load_state(current, voltage, thresholds)

    @staticmethod
    def detect_current_band(
        current: Optional[float],
        voltage: Optional[float],
        thresholds: ThresholdResolution,
    ) -> str:
        return classify_current_band(current, voltage, thresholds)

    @staticmethod
    def resolve_thresholds(device: Device) -> ThresholdResolution:
        return resolve_device_thresholds(device)

    @staticmethod
    def _normalized_numeric_fields(row: dict[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, v in row.items():
            if k in {"timestamp", "device_id", "schema_version", "enrichment_status", "table"}:
                continue
            if isinstance(v, (int, float)):
                out[k] = float(v)
        return out

    @staticmethod
    def _detect_current(fields: dict[str, float]) -> tuple[Optional[float], Optional[str]]:
        if "current" in fields:
            return fields["current"], "current"

        explicit_aliases = ["phase_current"]
        for alias in explicit_aliases:
            if alias in fields:
                return fields[alias], alias

        return None, None

    @staticmethod
    def _detect_voltage(fields: dict[str, float]) -> tuple[Optional[float], Optional[str]]:
        if "voltage" in fields:
            return fields["voltage"], "voltage"

        return None, None

    @staticmethod
    def _detect_power(fields: dict[str, float]) -> Optional[float]:
        if "power" in fields:
            return fields["power"]
        contains = [k for k in fields.keys() if k.lower() == "power" or "active_power" in k.lower()]
        if contains:
            return fields[sorted(contains)[0]]
        return None

    @staticmethod
    def _detect_pf(fields: dict[str, float]) -> Optional[float]:
        for key in ["power_factor", "pf", "cos_phi", "powerfactor"]:
            if key in fields:
                return fields[key]
        contains = [k for k in fields.keys() if "power_factor" in k.lower() or k.lower() == "pf"]
        if contains:
            return fields[sorted(contains)[0]]
        return None

    @classmethod
    def map_telemetry(cls, row: dict[str, Any]) -> MappedTelemetry:
        fields = cls._normalized_numeric_fields(row)
        current, current_field = cls._detect_current(fields)
        voltage, voltage_field = cls._detect_voltage(fields)
        power = cls._detect_power(fields)
        power_factor = cls._detect_pf(fields)
        return MappedTelemetry(
            current=current,
            voltage=voltage,
            power=power,
            power_factor=power_factor,
            current_field=current_field,
            voltage_field=voltage_field,
        )

    @staticmethod
    def _decode_snapshot_json(payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            decoded = json.loads(payload)
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    async def _fetch_telemetry(
        self,
        device_id: str,
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if start_time:
            params["start_time"] = start_time.isoformat()
        if end_time:
            params["end_time"] = end_time.isoformat()
        if limit is not None:
            params["limit"] = str(limit)

        url = f"{settings.DATA_SERVICE_BASE_URL}/api/v1/data/telemetry/{device_id}"
        logger.debug(
            "idle_running_fetch_telemetry_request",
            extra={"device_id": device_id, "url": url, "params": params},
        )
        try:
            client = await get_client(settings.DATA_SERVICE_BASE_URL)
            resp = await request_with_retries(
                client,
                "GET",
                f"/api/v1/data/telemetry/{device_id}",
                operation="idle_running_fetch_telemetry",
                params=params,
                headers=build_internal_headers(
                    "device-service",
                    self._ctx.tenant_id if self._ctx is not None else None,
                ),
                timeout=10.0,
            )
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict):
                items = payload.get("data", {}).get("items", [])
            else:
                items = payload if isinstance(payload, list) else []
        except httpx.ConnectError as exc:
            logger.error(
                "idle_running_fetch_telemetry_connect_error",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        except httpx.ConnectTimeout as exc:
            logger.error(
                "idle_running_fetch_telemetry_connect_timeout",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        except Exception as exc:
            logger.error(
                "idle_running_fetch_telemetry_error",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise

        def parse_ts(item: dict[str, Any]) -> float:
            ts = item.get("timestamp")
            if not ts:
                return 0.0
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0

        return sorted(items, key=parse_ts)

    async def _get_device(self, device_id: str, tenant_id: str) -> Optional[Device]:
        result = await self._session.execute(
            select(Device).where(
                Device.device_id == device_id,
                Device.tenant_id == tenant_id,
                Device.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _get_live_state(self, device_id: str, tenant_id: str) -> Optional[DeviceLiveState]:
        return await self._session.get(
            DeviceLiveState,
            {"device_id": device_id, "tenant_id": tenant_id},
        )

    async def _get_latest_snapshot(self, device_id: str, tenant_id: str) -> Optional[DeviceLatestTelemetrySnapshot]:
        return await self._session.get(
            DeviceLatestTelemetrySnapshot,
            {"device_id": device_id, "tenant_id": tenant_id},
        )

    @staticmethod
    def _normalize_utc(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
        return IdleRunningService._normalize_utc(parsed)

    async def _fetch_recent_projection_window(
        self,
        device_id: str,
        tenant_id: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> tuple[list[dict[str, Any]], bool]:
        start_utc = self._normalize_utc(start_time)
        end_utc = self._normalize_utc(end_time)
        if start_utc is None or end_utc is None:
            return [], False
        oldest_recent = (
            await self._session.execute(
                select(DeviceRecentTelemetrySample.sample_ts)
                .where(
                    DeviceRecentTelemetrySample.device_id == device_id,
                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                )
                .order_by(DeviceRecentTelemetrySample.sample_ts.asc(), DeviceRecentTelemetrySample.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        oldest_recent_utc = self._normalize_utc(oldest_recent)
        if oldest_recent_utc is None or start_utc < oldest_recent_utc:
            return [], False

        rows = (
            await self._session.execute(
                select(DeviceRecentTelemetrySample)
                .where(
                    DeviceRecentTelemetrySample.device_id == device_id,
                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                    DeviceRecentTelemetrySample.sample_ts >= start_utc,
                    DeviceRecentTelemetrySample.sample_ts <= end_utc,
                )
                .order_by(DeviceRecentTelemetrySample.sample_ts.asc(), DeviceRecentTelemetrySample.id.asc())
            )
        ).scalars().all()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = self._decode_snapshot_json(row.telemetry_json)
            sample_ts = self._normalize_utc(row.sample_ts)
            if not payload or sample_ts is None:
                continue
            payload.setdefault("timestamp", sample_ts.isoformat())
            payload.setdefault("device_id", device_id)
            items.append(payload)
        return items, True

    async def get_idle_config(self, device_id: str, tenant_id: str) -> dict[str, Any]:
        device = await self._get_device(device_id, tenant_id)
        if not device:
            raise ValueError("Device not found")
        thresholds = self.resolve_thresholds(device)
        return {
            "device_id": device_id,
            "full_load_current_a": thresholds.full_load_current_a,
            "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
            "derived_idle_threshold_a": thresholds.derived_idle_threshold_a,
            "derived_overconsumption_threshold_a": thresholds.derived_overconsumption_threshold_a,
            # Deprecated read-only compatibility field.
            "idle_current_threshold": thresholds.derived_idle_threshold_a,
            "configured": thresholds.configured,
            "source": thresholds.source,
        }

    async def set_idle_config(
        self,
        device_id: str,
        tenant_id: str,
        *,
        full_load_current_a: Optional[float],
        idle_threshold_pct_of_fla: Optional[float] = None,
        idle_current_threshold: Optional[float] = None,
    ) -> dict[str, Any]:
        device = await self._get_device(device_id, tenant_id)
        if not device:
            raise ValueError("Device not found")
        next_fla = float(full_load_current_a) if full_load_current_a is not None else (
            float(device.full_load_current_a) if device.full_load_current_a is not None else None
        )
        if next_fla is None and idle_current_threshold is not None:
            raise ThresholdConfigurationError(
                "Full load current must be configured before a deprecated idle threshold can be mapped to the FLA percentage model."
            )

        next_pct = idle_threshold_pct_of_fla
        if next_pct is None and idle_current_threshold is not None and next_fla is not None:
            next_pct = float(idle_current_threshold) / float(next_fla)
        if next_pct is None:
            next_pct = (
                float(device.idle_threshold_pct_of_fla)
                if device.idle_threshold_pct_of_fla is not None
                else DEFAULT_IDLE_THRESHOLD_PCT_OF_FLA
            )
        if next_pct <= 0 or next_pct >= 1:
            raise ThresholdConfigurationError(
                "Idle threshold percentage of FLA must be greater than 0 and less than 1."
            )

        if full_load_current_a is not None:
            device.full_load_current_a = Decimal(str(round(float(full_load_current_a), 4)))
        elif next_fla is not None and device.full_load_current_a is None:
            device.full_load_current_a = Decimal(str(round(float(next_fla), 4)))
        device.idle_threshold_pct_of_fla = Decimal(str(round(float(next_pct), 4)))
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(device)
        return await self.get_idle_config(device_id, tenant_id)

    async def get_waste_config(self, device_id: str, tenant_id: str) -> dict[str, Any]:
        device = await self._get_device(device_id, tenant_id)
        if not device:
            raise ValueError("Device not found")
        thresholds = self.resolve_thresholds(device)

        return {
            "device_id": device_id,
            "full_load_current_a": thresholds.full_load_current_a,
            "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
            "derived_idle_threshold_a": thresholds.derived_idle_threshold_a,
            "derived_overconsumption_threshold_a": thresholds.derived_overconsumption_threshold_a,
            # Deprecated read-only compatibility field.
            "overconsumption_current_threshold_a": thresholds.derived_overconsumption_threshold_a,
            # Deprecated; kept for backward compatibility.
            "unoccupied_weekday_start_time": None,
            "unoccupied_weekday_end_time": None,
            "unoccupied_weekend_start_time": None,
            "unoccupied_weekend_end_time": None,
            "has_device_override": False,
            "configured": thresholds.configured,
            "source": thresholds.source,
        }

    async def set_waste_config(
        self,
        device_id: str,
        tenant_id: str,
        overconsumption_current_threshold_a: Optional[float],
        unoccupied_weekday_start_time: Optional[str],
        unoccupied_weekday_end_time: Optional[str],
        unoccupied_weekend_start_time: Optional[str],
        unoccupied_weekend_end_time: Optional[str],
        full_load_current_a: Optional[float] = None,
    ) -> dict[str, Any]:
        device = await self._get_device(device_id, tenant_id)
        if not device:
            raise ValueError("Device not found")
        resolved_full_load_current_a = (
            full_load_current_a
            if full_load_current_a is not None
            else overconsumption_current_threshold_a
        )
        if resolved_full_load_current_a is not None:
            device.full_load_current_a = Decimal(str(round(float(resolved_full_load_current_a), 4)))
        # Deprecated fields are accepted but ignored in runtime logic.
        device.unoccupied_weekday_start_time = None
        device.unoccupied_weekday_end_time = None
        device.unoccupied_weekend_start_time = None
        device.unoccupied_weekend_end_time = None

        await self._session.flush()
        await self._session.commit()
        return await self.get_waste_config(device_id, tenant_id)

    @staticmethod
    def _validate_threshold_gap(
        *,
        idle_threshold: Optional[float],
        over_threshold: Optional[float],
    ) -> None:
        if idle_threshold is None or over_threshold is None:
            return
        if over_threshold <= idle_threshold:
            raise ThresholdConfigurationError(
                "Overconsumption threshold must be greater than idle threshold so waste categories remain exclusive."
            )

    async def get_site_waste_config(self, tenant_id: Optional[str] = None) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            # Deprecated and intentionally disabled by policy.
            "default_unoccupied_weekday_start_time": None,
            "default_unoccupied_weekday_end_time": None,
            "default_unoccupied_weekend_start_time": None,
            "default_unoccupied_weekend_end_time": None,
            "timezone": settings.PLATFORM_TIMEZONE,
            "configured": False,
        }

    async def set_site_waste_config(
        self,
        default_unoccupied_weekday_start_time: str,
        default_unoccupied_weekday_end_time: str,
        default_unoccupied_weekend_start_time: str,
        default_unoccupied_weekend_end_time: str,
        timezone_name: Optional[str],
        updated_by: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        # Deprecated endpoint: accept payload for compatibility, but keep feature disabled.
        return await self.get_site_waste_config(tenant_id)

    async def get_current_state(self, device_id: str, tenant_id: str) -> dict[str, Any]:
        device = await self._get_device(device_id, tenant_id)
        if not device:
            raise ValueError("Device not found")

        live_state = await self._get_live_state(device_id, tenant_id)
        snapshot = await self._get_latest_snapshot(device_id, tenant_id)
        thresholds = self.resolve_thresholds(device)

        snapshot_numeric_fields = self._decode_snapshot_json(snapshot.numeric_fields_json if snapshot else None)
        snapshot_source_fields = self._decode_snapshot_json(snapshot.source_fields_json if snapshot else None)
        mapped = self.map_telemetry(snapshot_numeric_fields) if snapshot_numeric_fields else MappedTelemetry(
            current=None,
            voltage=None,
            power=None,
            power_factor=None,
            current_field=None,
            voltage_field=None,
        )
        latest_ts = self._normalize_utc(snapshot.sample_ts) if snapshot is not None else None
        projection_ts = self._normalize_utc(live_state.last_telemetry_ts) if live_state is not None else None

        now_utc = datetime.now(timezone.utc)
        authoritative_ts = projection_ts or latest_ts
        stale = authoritative_ts is None or (now_utc - authoritative_ts).total_seconds() > TELEMETRY_TIMEOUT_SECONDS
        runtime_status = resolve_runtime_status(authoritative_ts, now_utc=now_utc)
        current_value = (
            float(live_state.last_current_a)
            if live_state is not None and live_state.last_current_a is not None
            else (
                float(snapshot.last_current_a)
                if snapshot is not None and snapshot.last_current_a is not None
                else mapped.current
            )
        )
        voltage_value = (
            float(live_state.last_voltage_v)
            if live_state is not None and live_state.last_voltage_v is not None
            else (
                float(snapshot.last_voltage_v)
                if snapshot is not None and snapshot.last_voltage_v is not None
                else mapped.voltage
            )
        )
        timestamp = authoritative_ts.isoformat() if authoritative_ts is not None else None
        current_band = (
            self.detect_current_band(current_value, voltage_value, thresholds)
            if runtime_status == "running" and not stale
            else "unknown"
        )
        state = (
            self.detect_device_state_with_thresholds(current_value, voltage_value, thresholds)
            if runtime_status == "running" and not stale
            else "unknown"
        )

        return {
            "device_id": device_id,
            "state": state,
            "current_band": current_band,
            "current": current_value,
            "voltage": voltage_value,
            "threshold": thresholds.derived_idle_threshold_a,
            "full_load_current_a": thresholds.full_load_current_a,
            "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
            "derived_idle_threshold_a": thresholds.derived_idle_threshold_a,
            "derived_overconsumption_threshold_a": thresholds.derived_overconsumption_threshold_a,
            "configured": thresholds.configured,
            "timestamp": timestamp,
            "idle_streak_started_at": (
                self._normalize_utc(live_state.idle_streak_started_at).isoformat()
                if live_state is not None and live_state.idle_streak_started_at is not None
                else None
            ),
            "idle_streak_duration_sec": int(live_state.idle_streak_duration_sec or 0) if live_state is not None else 0,
            "current_field": snapshot_source_fields.get("current_field") or mapped.current_field,
            "voltage_field": snapshot_source_fields.get("voltage_field") or mapped.voltage_field,
        }

    @staticmethod
    def _power_kw(mapped: MappedTelemetry) -> tuple[Optional[float], bool]:
        if mapped.current is None or mapped.voltage is None:
            return None, False
        if mapped.power is not None:
            return max(float(mapped.power), 0.0) / 1000.0, False
        pf = mapped.power_factor if mapped.power_factor is not None else 1.0
        pf_estimated = mapped.power_factor is None
        return max((float(mapped.current) * float(mapped.voltage) * abs(float(pf))) / 1000.0, 0.0), pf_estimated

    async def _get_or_create_day_log(
        self,
        device_id: str,
        day_start: datetime,
        now_utc: datetime,
    ) -> IdleRunningLog:
        tenant_scope = self._ctx.tenant_id if self._ctx is not None else None
        if tenant_scope is None:
            raise RuntimeError("tenant_id missing at idle_running_log write point")
        result = await self._session.execute(
            select(IdleRunningLog).where(
                IdleRunningLog.device_id == device_id,
                IdleRunningLog.tenant_id == tenant_scope,
                IdleRunningLog.period_start == day_start,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            return row

        row = IdleRunningLog(
            device_id=device_id,
            tenant_id=tenant_scope,
            period_start=day_start,
            period_end=day_start,
            idle_duration_sec=0,
            idle_energy_kwh=0,
            idle_cost=0,
            currency="INR",
            tariff_rate_used=0,
            pf_estimated=False,
            created_at=now_utc,
            updated_at=now_utc,
        )
        assert row.tenant_id is not None, "idle_running_log write missing tenant_id"
        self._session.add(row)
        await self._session.flush()
        return row

    async def aggregate_device_idle(
        self,
        device: Device,
        tenant_id: Optional[str] = None,
        now_utc: Optional[datetime] = None,
    ) -> None:
        thresholds = self.resolve_thresholds(device)
        if not thresholds.configured or thresholds.derived_idle_threshold_a is None:
            return

        now_utc = now_utc or datetime.now(timezone.utc)
        now_utc = self._to_utc(now_utc)
        platform_tz = _get_platform_tz()
        now_local = now_utc.astimezone(platform_tz)
        day_start_local = datetime.combine(now_local.date(), datetime.min.time(), tzinfo=platform_tz)
        tenant_scope = self._ctx.tenant_id if self._ctx is not None else None
        if tenant_scope is None:
            raise RuntimeError("tenant_id missing at idle_running_log write point")
        if tenant_id is not None and tenant_id != tenant_scope:
            raise RuntimeError("tenant_id missing at idle_running_log write point")
        row = await self._get_or_create_day_log(device.device_id, day_start_local, now_utc)

        row_period_end_local = self._to_local(row.period_end, platform_tz) if row.period_end else None
        from_local = row_period_end_local if row_period_end_local and row_period_end_local > day_start_local else day_start_local
        if from_local >= now_local:
            return

        points, recent_covered = await self._fetch_recent_projection_window(
            device.device_id,
            tenant_scope,
            start_time=from_local.astimezone(timezone.utc),
            end_time=now_utc,
        )
        if not recent_covered:
            points = await self._fetch_telemetry(
                device.device_id,
                start_time=from_local.astimezone(timezone.utc),
                end_time=now_utc,
            )
        if len(points) < 2:
            row.period_end = now_utc
            await self._session.flush()
            return

        window = aggregate_window(
            points,
            platform_tz=platform_tz,
            shifts=[shift for shift in (device.shifts or []) if shift.is_active],
            idle_threshold=thresholds.derived_idle_threshold_a,
            over_threshold=thresholds.derived_overconsumption_threshold_a,
            config_source=device,
        )

        row.idle_duration_sec = int(row.idle_duration_sec or 0) + int(window.total.idle_duration_sec)
        row.idle_energy_kwh = Decimal(str(round(float(row.idle_energy_kwh or 0) + float(window.total.idle_kwh), 6)))
        row.pf_estimated = bool(row.pf_estimated or window.total.pf_estimated)
        row.period_end = now_utc

        tariff = await TariffCache.get(tenant_scope)
        rate = tariff.get("rate")
        row.currency = tariff.get("currency") or row.currency or "INR"
        row.tariff_rate_used = Decimal(str(rate if rate is not None else 0))
        if rate is not None:
            row.idle_cost = Decimal(str(round(float(row.idle_energy_kwh) * float(rate), 4)))

        await self._session.flush()

    async def aggregate_all_configured_devices(self) -> dict[str, int]:
        tenant_scope = self._ctx.tenant_id if self._ctx is not None else None
        if tenant_scope is None:
            raise RuntimeError("tenant_id missing at idle_running_log write point")
        result = await self._session.execute(
            select(Device).where(
                Device.deleted_at.is_(None),
                Device.full_load_current_a.is_not(None),
                Device.tenant_id == tenant_scope,
            )
        )
        devices = result.scalars().all()
        processed = 0
        failed = 0
        now_utc = datetime.now(timezone.utc)

        for device in devices:
            try:
                await self.aggregate_device_idle(device, tenant_id=tenant_scope, now_utc=now_utc)
                processed += 1
            except Exception as exc:
                failed += 1
                logger.error("idle_aggregation_failed", extra={"device_id": device.device_id, "error": str(exc)})

        await self._session.commit()
        return {"processed": processed, "failed": failed, "total": len(devices)}

    @staticmethod
    def _duration_label(minutes: int) -> str:
        hours = minutes // 60
        mins = minutes % 60
        if hours > 0:
            return f"{hours} hr {mins} min"
        return f"{mins} min"

    async def get_idle_stats(self, device_id: str, tenant_id: str) -> dict[str, Any]:
        device = await self._get_device(device_id, tenant_id)
        if not device:
            raise ValueError("Device not found")
        data_source_type = device.data_source_type
        thresholds = self.resolve_thresholds(device)
        if not thresholds.configured or thresholds.derived_idle_threshold_a is None:
            return {
                "device_id": device_id,
                "today": None,
                "month": None,
                "tariff_configured": False,
                "pf_estimated": False,
                "threshold_configured": False,
                "idle_current_threshold": None,
                "full_load_current_a": None,
                "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
                "derived_idle_threshold_a": None,
                "derived_overconsumption_threshold_a": None,
                "data_source_type": data_source_type,
            }

        # Refresh aggregate up to "now" for near-real-time widget reads.
        try:
            await self.aggregate_device_idle(device, tenant_id=tenant_id)
            await self._session.commit()
        except Exception as exc:
            logger.warning("idle_stats_refresh_failed", extra={"device_id": device_id, "error": str(exc)})

        now_utc = datetime.now(timezone.utc)
        platform_tz = _get_platform_tz()
        now_local = now_utc.astimezone(platform_tz)
        day_start_local = datetime.combine(now_local.date(), datetime.min.time(), tzinfo=platform_tz)
        month_start_local = datetime.combine(now_local.replace(day=1).date(), datetime.min.time(), tzinfo=platform_tz)

        today_row = (
            await self._session.execute(
                select(IdleRunningLog).where(
                    IdleRunningLog.device_id == device_id,
                    IdleRunningLog.tenant_id == tenant_id,
                    IdleRunningLog.period_start == day_start_local,
                )
            )
        ).scalar_one_or_none()

        month_agg = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(IdleRunningLog.idle_duration_sec), 0),
                    func.coalesce(func.sum(IdleRunningLog.idle_energy_kwh), 0),
                    func.max(IdleRunningLog.pf_estimated),
                ).where(
                    IdleRunningLog.device_id == device_id,
                    IdleRunningLog.tenant_id == tenant_id,
                    IdleRunningLog.period_start >= month_start_local,
                    IdleRunningLog.period_start <= day_start_local,
                )
            )
        ).one()

        today_duration_sec = int(today_row.idle_duration_sec) if today_row else 0
        month_duration_sec = int(month_agg[0] or 0)
        month_energy = float(month_agg[1] or 0.0)
        pf_estimated = bool(month_agg[2] or (today_row.pf_estimated if today_row else False))
        live_state = await self._session.get(DeviceLiveState, {"device_id": device_id, "tenant_id": tenant_id})
        local_day = now_local.date()
        day_matches = live_state is not None and live_state.day_bucket == local_day
        today_energy = float(live_state.today_idle_kwh or 0.0) if day_matches and live_state is not None else float(today_row.idle_energy_kwh) if today_row else 0.0

        tariff = await TariffCache.get(tenant_id)
        tariff_rate = tariff.get("rate")
        currency = tariff.get("currency") or "INR"

        today_cost = round(today_energy * float(tariff_rate), 2) if tariff_rate is not None else None
        month_cost = round(month_energy * float(tariff_rate), 2) if tariff_rate is not None else None

        today_minutes = today_duration_sec // 60
        month_minutes = month_duration_sec // 60

        return {
            "device_id": device_id,
            "today": {
                "idle_duration_minutes": today_minutes,
                "idle_duration_label": self._duration_label(today_minutes),
                "idle_energy_kwh": round(today_energy, 4),
                "idle_cost": today_cost,
                "currency": currency,
            },
            "month": {
                "idle_duration_minutes": month_minutes,
                "idle_duration_label": self._duration_label(month_minutes),
                "idle_energy_kwh": round(month_energy, 4),
                "idle_cost": month_cost,
                "currency": currency,
            },
            "tariff_configured": tariff_rate is not None,
            "pf_estimated": pf_estimated,
            "threshold_configured": True,
            "idle_current_threshold": thresholds.derived_idle_threshold_a,
            "full_load_current_a": thresholds.full_load_current_a,
            "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
            "derived_idle_threshold_a": thresholds.derived_idle_threshold_a,
            "derived_overconsumption_threshold_a": thresholds.derived_overconsumption_threshold_a,
            "data_source_type": data_source_type,
            "tariff_cache": tariff.get("cache"),
            "tariff_stale": tariff.get("stale", False),
        }
    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _to_local(dt: datetime, tz: ZoneInfo) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).astimezone(tz)
        return dt.astimezone(tz)
