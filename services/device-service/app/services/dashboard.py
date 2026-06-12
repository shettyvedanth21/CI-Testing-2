"""Service layer for home dashboard aggregates."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time as clock_time
from io import BytesIO
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.monitoring import (
    CALENDAR_COST_SNAPSHOT_AGE_SECONDS,
    DASHBOARD_COST_DATA_AGE_SECONDS,
    DASHBOARD_COST_DATA_STATE_TOTAL,
    DASHBOARD_COST_REFRESH_FAILURES_TOTAL,
    FLEET_STREAM_DISCONNECTS_TOTAL,
    FLEET_STREAM_EMIT_LAG_SECONDS,
    SNAPSHOT_AGE_SECONDS,
    SNAPSHOT_MATERIALIZE_DURATION_SECONDS,
    SNAPSHOT_MATERIALIZE_FAILURES_TOTAL,
    fleet_stream_broadcaster,
)
from app.models.device import (
    DashboardSnapshot,
    Device,
    DeviceLatestTelemetrySnapshot,
    DeviceLiveState,
    DevicePerformanceTrend,
    DeviceRecentTelemetrySample,
    DeviceShift,
    RuntimeStatus,
)
from services.shared.energy_accounting import aggregate_window, build_samples as shared_build_samples
from services.shared.telemetry_normalization import normalize_telemetry_sample
from services.shared.tenant_context import TenantContext, build_internal_headers
from app.services.idle_running import TariffCache
from app.services.emission_factor_cache import EmissionFactorCache, build_co2_overview
from app.services.shared_http import get_client, request_with_retries
from app.services.load_thresholds import classify_current_band, resolve_device_thresholds
from app.services.runtime_state import resolve_load_state, resolve_runtime_status
from app.services.status_model import resolve_operational_status

try:  # pragma: no cover - optional during local unit tests
    from minio import Minio
except Exception:  # pragma: no cover - dependency may not be installed in some test environments
    Minio = None

UTC = ZoneInfo("UTC")
MAX_GAP_SEC = 900.0
CACHE_TTL_SECONDS = 0
FETCH_CONCURRENCY = 8
LOSS_TOLERANCE_INR = 0.01
FLEET_SNAPSHOT_KEY = "dashboard:fleet-state:v1"
DASHBOARD_SUMMARY_KEY = "dashboard:summary:v1"
TODAY_LOSS_KEY = "dashboard:today-loss:v1"
ENERGY_WIDGETS_KEY = "dashboard:energy-widgets:v1"
MONTHLY_ENERGY_PREFIX = "dashboard:monthly-energy:v1"

logger = logging.getLogger(__name__)


def _iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _get_platform_tz() -> ZoneInfo:
    from app.config import settings

    return ZoneInfo(settings.PLATFORM_TIMEZONE)


class DashboardDeviceNotFoundError(ValueError):
    """Raised when dashboard bootstrap is requested for a missing device."""


@dataclass
class IntervalSample:
    ts_utc: datetime
    ts_local: datetime
    duration_sec: float
    power_kw: Optional[float]
    current_a: Optional[float]
    voltage_v: Optional[float]
    pf: Optional[float]


class DashboardService:
    """Aggregate system-level and per-device metrics for the home dashboard."""

    _cache_lock = asyncio.Lock()
    _cache: dict[str, tuple[float, dict[str, Any]]] = {}
    _downstream_lock = asyncio.Lock()
    _downstream_failures: dict[str, int] = {}
    _downstream_cooldown_until: dict[str, float] = {}
    _snapshot_minio_client: Any = None

    def __init__(self, session: AsyncSession, ctx: TenantContext | None = None):
        self._session = session
        self._ctx = ctx

    @staticmethod
    def _service_base_url(service_key: str) -> str:
        if service_key == "data_service":
            return settings.DATA_SERVICE_BASE_URL or ""
        if service_key == "rule_engine":
            return settings.RULE_ENGINE_SERVICE_BASE_URL or ""
        return ""

    @staticmethod
    def _service_path(service_key: str, full_url: str) -> str:
        base = DashboardService._service_base_url(service_key).rstrip("/")
        if full_url.startswith(base):
            return full_url[len(base):]
        return full_url

    def _snapshot_key(self, base_key: str) -> str:
        return base_key

    def _snapshot_tenant_id(self) -> str:
        if self._ctx is None:
            raise RuntimeError("tenant_id missing at dashboard snapshot access point")
        return self._ctx.require_tenant()

    def _snapshot_cache_key(self, snapshot_key: str) -> str:
        return f"{self._snapshot_tenant_id()}:{snapshot_key}"

    @staticmethod
    def _snapshot_storage_backend() -> str:
        backend = str(settings.SNAPSHOT_STORAGE_BACKEND or "auto").strip().lower()
        if backend == "mysql":
            return "mysql"
        if backend == "minio":
            return "minio"

        endpoint = str(settings.SNAPSHOT_MINIO_ENDPOINT or "").strip()
        access_key = str(settings.SNAPSHOT_MINIO_ACCESS_KEY or "").strip()
        secret_key = str(settings.SNAPSHOT_MINIO_SECRET_KEY or "").strip()
        if endpoint and access_key and secret_key:
            return "minio"
        return "mysql"

    @staticmethod
    def _snapshot_object_key(tenant_id: str, snapshot_key: str, generated_at: datetime) -> str:
        timestamp = generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"{tenant_id}/{snapshot_key}/{timestamp}.json"

    @classmethod
    def _get_snapshot_minio_client(cls) -> Any:
        if cls._snapshot_minio_client is not None:
            return cls._snapshot_minio_client
        if Minio is None:
            raise RuntimeError("minio client library is not installed")
        endpoint = str(settings.SNAPSHOT_MINIO_ENDPOINT or "").strip()
        access_key = str(settings.SNAPSHOT_MINIO_ACCESS_KEY or "").strip()
        secret_key = str(settings.SNAPSHOT_MINIO_SECRET_KEY or "").strip()
        missing: list[str] = []
        if not endpoint:
            missing.append("SNAPSHOT_MINIO_ENDPOINT")
        if not access_key:
            missing.append("SNAPSHOT_MINIO_ACCESS_KEY")
        if not secret_key:
            missing.append("SNAPSHOT_MINIO_SECRET_KEY")
        if missing:
            raise RuntimeError(f"snapshot minio config missing: {', '.join(missing)}")
        cls._snapshot_minio_client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=bool(settings.SNAPSHOT_MINIO_SECURE),
        )
        return cls._snapshot_minio_client

    @classmethod
    def _ensure_snapshot_bucket(cls) -> None:
        client = cls._get_snapshot_minio_client()
        bucket = settings.SNAPSHOT_MINIO_BUCKET
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

    @classmethod
    def _upload_snapshot_to_minio(
        cls,
        tenant_id: str,
        snapshot_key: str,
        generated_at: datetime,
        payload_json: str,
    ) -> str:
        cls._ensure_snapshot_bucket()
        client = cls._get_snapshot_minio_client()
        object_key = cls._snapshot_object_key(tenant_id, snapshot_key, generated_at)
        data = payload_json.encode("utf-8")
        client.put_object(
            settings.SNAPSHOT_MINIO_BUCKET,
            object_key,
            BytesIO(data),
            len(data),
            content_type="application/json",
        )
        return object_key

    @classmethod
    def _download_snapshot_from_minio(cls, s3_key: str) -> str:
        client = cls._get_snapshot_minio_client()
        response = client.get_object(settings.SNAPSHOT_MINIO_BUCKET, s3_key)
        try:
            raw = response.read()
            if isinstance(raw, bytes):
                return raw.decode("utf-8")
            return str(raw)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            release_conn = getattr(response, "release_conn", None)
            if callable(release_conn):
                release_conn()

    @classmethod
    def _delete_snapshot_from_minio_if_exists(cls, s3_key: str | None) -> None:
        if not s3_key:
            return
        client = cls._get_snapshot_minio_client()
        try:
            client.remove_object(settings.SNAPSHOT_MINIO_BUCKET, s3_key)
        except Exception:
            return

    @classmethod
    def _delete_expired_snapshots_from_storage(cls, s3_keys: list[str]) -> None:
        if not s3_keys:
            return
        client = cls._get_snapshot_minio_client()
        for s3_key in s3_keys:
            try:
                client.remove_object(settings.SNAPSHOT_MINIO_BUCKET, s3_key)
            except Exception:
                pass

    @classmethod
    async def _is_circuit_open(cls, service_key: str) -> bool:
        async with cls._downstream_lock:
            until_ts = cls._downstream_cooldown_until.get(service_key, 0.0)
            return until_ts > clock_time.monotonic()

    @classmethod
    async def _record_downstream_success(cls, service_key: str) -> None:
        async with cls._downstream_lock:
            cls._downstream_failures[service_key] = 0
            cls._downstream_cooldown_until.pop(service_key, None)

    @classmethod
    async def _record_downstream_failure(cls, service_key: str) -> None:
        async with cls._downstream_lock:
            failures = cls._downstream_failures.get(service_key, 0) + 1
            cls._downstream_failures[service_key] = failures
            threshold = max(1, settings.DASHBOARD_CIRCUIT_BREAKER_FAILURE_THRESHOLD)
            if failures >= threshold:
                cls._downstream_cooldown_until[service_key] = (
                    clock_time.monotonic() + max(1, settings.DASHBOARD_CIRCUIT_BREAKER_COOLDOWN_SECONDS)
                )

    async def _http_get_json(
        self,
        service_key: str,
        url: str,
        params: Optional[dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        if await self._is_circuit_open(service_key):
            return None, "circuit_open"

        retries = max(0, settings.DASHBOARD_DOWNSTREAM_RETRIES)
        timeout = max(0.5, settings.DASHBOARD_DOWNSTREAM_TIMEOUT_SECONDS)
        last_reason = "downstream_failure"

        for _ in range(retries + 1):
            try:
                client = await get_client(
                    self._service_base_url(service_key),
                )
                response = await client.get(
                    self._service_path(service_key, url),
                    params=params,
                    headers=build_internal_headers(
                        "device-service",
                        tenant_id if tenant_id is not None else (self._ctx.tenant_id if self._ctx is not None else None),
                    ),
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    last_reason = "invalid_payload"
                    continue
                await self._record_downstream_success(service_key)
                return payload, None
            except httpx.TimeoutException:
                last_reason = "timeout"
            except httpx.HTTPStatusError:
                last_reason = "http_status"
            except Exception:
                last_reason = "request_error"

        await self._record_downstream_failure(service_key)
        return None, last_reason

    async def _http_post_json(
        self,
        service_key: str,
        url: str,
        body: dict[str, Any],
        tenant_id: Optional[str] = None,
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        if await self._is_circuit_open(service_key):
            return None, "circuit_open"

        retries = max(0, settings.DASHBOARD_DOWNSTREAM_RETRIES)
        timeout = max(0.5, settings.DASHBOARD_DOWNSTREAM_TIMEOUT_SECONDS)
        last_reason = "downstream_failure"

        for _ in range(retries + 1):
            try:
                client = await get_client(
                    self._service_base_url(service_key),
                )
                response = await client.post(
                    self._service_path(service_key, url),
                    json=body,
                    headers=build_internal_headers(
                        "device-service",
                        tenant_id if tenant_id is not None else (self._ctx.tenant_id if self._ctx is not None else None),
                    ),
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    last_reason = "invalid_payload"
                    continue
                await self._record_downstream_success(service_key)
                return payload, None
            except httpx.TimeoutException:
                last_reason = "timeout"
            except httpx.HTTPStatusError:
                last_reason = "http_status"
            except Exception:
                last_reason = "request_error"

        await self._record_downstream_failure(service_key)
        return None, last_reason

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, set):
            return list(value)
        raise TypeError(f"Type is not JSON serializable: {type(value)!r}")

    @staticmethod
    def _build_warning(service_name: str, reason: Optional[str]) -> str:
        return f"{service_name}:{reason or 'unknown'}"

    @staticmethod
    def _extract_degraded_services(warnings: list[str]) -> list[str]:
        degraded: list[str] = []
        for warning in warnings:
            if not isinstance(warning, str) or ":" not in warning:
                continue
            service_name = warning.split(":", 1)[0].strip()
            if service_name and service_name not in degraded:
                degraded.append(service_name)
        return degraded

    async def _read_snapshot(
        self,
        key: str,
        stale_after_seconds: Optional[int] = None,
    ) -> tuple[Optional[dict[str, Any]], bool]:
        tenant_id = self._snapshot_tenant_id()
        cache_key = self._snapshot_cache_key(key)
        result = await self._session.execute(
            select(
                DashboardSnapshot.tenant_id,
                DashboardSnapshot.snapshot_key,
                DashboardSnapshot.s3_key,
                DashboardSnapshot.storage_backend,
                DashboardSnapshot.payload_json,
                DashboardSnapshot.generated_at,
                DashboardSnapshot.expires_at,
            ).where(
                DashboardSnapshot.tenant_id == tenant_id,
                DashboardSnapshot.snapshot_key == key,
            )
        )
        row = result.mappings().first()
        if row is None:
            return None, False

        expires_at = row.get("expires_at")
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(tz=UTC):
                return None, True

        stale_after = stale_after_seconds or settings.DASHBOARD_SNAPSHOT_STALE_AFTER_SECONDS
        generated_at = row.get("generated_at")
        if generated_at is None:
            return None, False
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        age_sec = (datetime.now(tz=UTC) - generated_at).total_seconds()
        SNAPSHOT_AGE_SECONDS.labels(snapshot_key=key).set(max(0.0, age_sec))
        is_stale = age_sec > max(1, stale_after)

        try:
            storage_backend = str(row.get("storage_backend") or "mysql").lower()
            raw_payload: Optional[str]
            if storage_backend == "minio":
                s3_key = row.get("s3_key")
                if not s3_key:
                    raise RuntimeError("dashboard snapshot is missing s3_key")
                raw_payload = self._download_snapshot_from_minio(str(s3_key))
            else:
                raw_payload = row.get("payload_json")
                if raw_payload is None:
                    raise RuntimeError("dashboard snapshot payload is missing")
            payload = json.loads(raw_payload)
        except Exception as exc:
            logger.error(
                "dashboard_snapshot_read_failed",
                extra={"tenant_id": tenant_id, "snapshot_key": key, "error": str(exc)},
            )
            cached_payload = await self._cache_get(cache_key)
            if cached_payload is not None:
                return cached_payload, True
            fallback_payload = row.get("payload_json")
            if fallback_payload is not None:
                try:
                    payload = json.loads(fallback_payload)
                    if isinstance(payload, dict):
                        await self._cache_set(cache_key, payload)
                        return payload, True
                except Exception:
                    pass
            raise HTTPException(
                status_code=503,
                detail={
                    "success": False,
                    "error": {
                        "code": "SNAPSHOT_STORAGE_UNAVAILABLE",
                        "message": "Snapshot payload could not be loaded from storage",
                    },
                },
            )
        if not isinstance(payload, dict):
            return None, False
        await self._cache_set(cache_key, payload)
        return payload, is_stale

    async def _write_snapshot(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        started = clock_time.perf_counter()
        tenant_id = self._snapshot_tenant_id()
        cache_key = self._snapshot_cache_key(key)
        generated_raw = payload.get("generated_at")
        generated_at = self._parse_ts(generated_raw) or datetime.now(tz=UTC)
        serialized = json.dumps(payload, default=self._json_default, separators=(",", ":"))
        storage_backend = self._snapshot_storage_backend()
        s3_key: Optional[str] = None
        expires_at = datetime.now(tz=UTC) + timedelta(
            seconds=max(60, int(settings.DASHBOARD_SNAPSHOT_TTL_SECONDS))
        )

        if storage_backend == "minio":
            try:
                s3_key = self._upload_snapshot_to_minio(tenant_id, key, generated_at, serialized)
                logger.info(
                    "dashboard_snapshot_written",
                    extra={
                        "tenant_id": tenant_id,
                        "snapshot_key": key,
                        "storage_backend": "minio",
                        "s3_key": s3_key,
                    },
                )
            except Exception as exc:
                storage_backend = "mysql"
                s3_key = None
                logger.warning(
                    "dashboard_snapshot_minio_upload_failed",
                    extra={
                        "tenant_id": tenant_id,
                        "snapshot_key": key,
                        "error": str(exc),
                        "fallback_backend": "mysql",
                    },
                )

        row = await self._session.get(
            DashboardSnapshot,
            {"tenant_id": tenant_id, "snapshot_key": key},
        )
        previous_s3_key = getattr(row, "s3_key", None) if row is not None else None
        if row is None:
            row = DashboardSnapshot(
                tenant_id=tenant_id,
                snapshot_key=key,
                payload_json=serialized if storage_backend == "mysql" else None,
                s3_key=s3_key,
                storage_backend=storage_backend,
                generated_at=generated_at,
                expires_at=expires_at,
            )
            self._session.add(row)
        else:
            row.payload_json = serialized if storage_backend == "mysql" else None
            row.s3_key = s3_key
            row.storage_backend = storage_backend
            row.generated_at = generated_at
            row.expires_at = expires_at

        await self._session.flush()
        if previous_s3_key and previous_s3_key != s3_key:
            self._delete_snapshot_from_minio_if_exists(previous_s3_key)
        await self._cache_set(cache_key, payload)
        if storage_backend != "minio":
            logger.info(
                "dashboard_snapshot_written",
                extra={
                    "tenant_id": tenant_id,
                    "snapshot_key": key,
                    "storage_backend": storage_backend,
                },
            )
        SNAPSHOT_MATERIALIZE_DURATION_SECONDS.labels(snapshot_key=key).observe(
            max(0.0, clock_time.perf_counter() - started)
        )
        return payload

    @classmethod
    async def _cache_get(cls, key: str) -> Optional[dict[str, Any]]:
        async with cls._cache_lock:
            item = cls._cache.get(key)
            if not item:
                return None
            ts, payload = item
            if (datetime.now(tz=UTC).timestamp() - ts) > CACHE_TTL_SECONDS:
                cls._cache.pop(key, None)
                return None
            return payload

    @classmethod
    async def _cache_set(cls, key: str, payload: dict[str, Any]) -> None:
        async with cls._cache_lock:
            cls._cache[key] = (datetime.now(tz=UTC).timestamp(), payload)

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            num = float(value)
            if math.isnan(num) or math.isinf(num):
                return None
            return num
        except Exception:
            return None

    @staticmethod
    def _decode_json_object(payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            decoded = json.loads(payload)
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    async def _fetch_recent_telemetry_seed(self, device_id: str, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent telemetry from the MySQL live projection lane.

        Full machine bootstrap is a live/status endpoint. It must not call
        data-service/Influx for seed telemetry because many open dashboards can
        amplify that into expensive fan-out pressure. The live projection writes
        a bounded recent sample buffer specifically for this fast read path.
        """

        rows = (
            await self._session.execute(
                select(DeviceRecentTelemetrySample)
                .where(
                    DeviceRecentTelemetrySample.device_id == device_id,
                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                )
                .order_by(
                    DeviceRecentTelemetrySample.sample_ts.desc(),
                    DeviceRecentTelemetrySample.id.desc(),
                )
                .limit(max(1, limit))
            )
        ).scalars().all()
        recent = [
            payload
            for payload in (self._decode_json_object(row.telemetry_json) for row in rows)
            if payload
        ]
        if recent:
            return recent

        snapshot = await self._session.get(
            DeviceLatestTelemetrySnapshot,
            {"device_id": device_id, "tenant_id": tenant_id},
        )
        numeric_fields = self._decode_json_object(snapshot.numeric_fields_json if snapshot else None)
        if snapshot is None or snapshot.sample_ts is None or not numeric_fields:
            return []

        return [
            {
                "timestamp": _iso_utc(snapshot.sample_ts),
                "device_id": device_id,
                "schema_version": "v1",
                "enrichment_status": "pending",
                **numeric_fields,
            }
        ]

    @staticmethod
    def _parse_ts(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            return None

    @staticmethod
    def _has_blocking_cost_quality(payload: dict[str, Any]) -> bool:
        warnings = payload.get("warnings")
        if isinstance(warnings, list) and len(warnings) > 0:
            return True
        data_quality = str(payload.get("data_quality") or "ok").lower()
        if data_quality not in {"ok", "fresh"}:
            return True
        if payload.get("reconciliation_warning"):
            return True
        return False

    def _evaluate_cost_data_state(
        self,
        payload: Optional[dict[str, Any]],
        is_stale_snapshot: bool,
        freshness_seconds: int,
        extra_reasons: Optional[list[str]] = None,
    ) -> tuple[str, list[str], Optional[datetime], Optional[float]]:
        reasons = list(extra_reasons or [])
        if payload is None:
            reasons.append("snapshot_unavailable")
            return "unavailable", reasons, None, None

        generated_at = self._parse_ts(payload.get("generated_at"))
        if generated_at is None:
            reasons.append("missing_generated_at")
            return "unavailable", reasons, None, None

        age_seconds = max(0.0, (datetime.now(tz=UTC) - generated_at).total_seconds())
        if is_stale_snapshot:
            reasons.append("snapshot_stale")
        if age_seconds > max(1, freshness_seconds):
            reasons.append("freshness_breach")
        if self._has_blocking_cost_quality(payload):
            reasons.append("quality_warning")

        if any(reason in {"snapshot_unavailable", "missing_generated_at", "refresh_failed"} for reason in reasons):
            return "unavailable", reasons, generated_at, age_seconds
        if reasons:
            return "stale", reasons, generated_at, age_seconds
        return "fresh", reasons, generated_at, age_seconds

    @staticmethod
    def _extract_first_number(row: dict[str, Any], keys: list[str]) -> Optional[float]:
        for key in keys:
            if key in row:
                num = DashboardService._safe_float(row.get(key))
                if num is not None:
                    return num
        return None

    @staticmethod
    def _extract_power_kw(row: dict[str, Any]) -> Optional[float]:
        normalized = normalize_telemetry_sample(row, row)
        return (normalized.business_power_w / 1000.0) if normalized.business_power_w > 0 else None

    @staticmethod
    def _extract_current_a(row: dict[str, Any]) -> Optional[float]:
        return normalize_telemetry_sample(row, row).current_a

    @staticmethod
    def _extract_voltage_v(row: dict[str, Any]) -> Optional[float]:
        return normalize_telemetry_sample(row, row).voltage_v

    @staticmethod
    def _extract_pf(row: dict[str, Any]) -> Optional[float]:
        return normalize_telemetry_sample(row, row).pf_business

    @staticmethod
    def _build_samples(rows: list[dict[str, Any]]) -> list[IntervalSample]:
        return shared_build_samples(rows, _get_platform_tz(), max_gap_sec=MAX_GAP_SEC)

    @staticmethod
    def _is_inside_shift(local_ts: datetime, shifts: list[DeviceShift]) -> bool:
        if not shifts:
            return False
        now_t = local_ts.time()
        weekday = local_ts.weekday()
        for shift in shifts:
            if not shift.is_active:
                continue
            start = shift.shift_start
            end = shift.shift_end
            shift_day = shift.day_of_week
            if end > start:
                if start <= now_t < end and (shift_day is None or shift_day == weekday):
                    return True
            else:
                if now_t >= start:
                    if shift_day is None or shift_day == weekday:
                        return True
                elif now_t < end:
                    prev_day = (weekday - 1) % 7
                    if shift_day is None or shift_day == prev_day:
                        return True
        return False

    async def _fetch_telemetry_window(
        self,
        device_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[dict[str, Any]]:
        payload, _ = await self._http_get_json(
            service_key="data_service",
            url=f"{settings.DATA_SERVICE_BASE_URL}/api/v1/data/telemetry/{device_id}",
            params={
                "start_time": start_utc.isoformat(),
                "end_time": end_utc.isoformat(),
                "limit": 10000,
            },
            tenant_id=self._ctx.tenant_id if self._ctx is not None else None,
        )
        if not isinstance(payload, dict):
            return []
        return payload.get("data", {}).get("items", []) or []

    async def _get_tariff(self, tenant_id: Optional[str] = None) -> tuple[float, str]:
        effective_tenant = tenant_id if tenant_id is not None else (self._ctx.tenant_id if self._ctx is not None else None)
        try:
            payload = await TariffCache.get(effective_tenant)
            rate = self._safe_float(payload.get("rate")) or 0.0
            currency = str(payload.get("currency") or "INR")
            return rate, currency
        except Exception as exc:
            logger.warning(
                "dashboard_tariff_lookup_failed",
                extra={"tenant_id": effective_tenant, "error": str(exc)},
            )
            return 0.0, "INR"

    async def _get_all_devices_with_shifts(self) -> list[Device]:
        query = select(Device).where(Device.deleted_at.is_(None))
        if self._ctx is not None and self._ctx.tenant_id is not None:
            query = query.where(Device.tenant_id == self._ctx.tenant_id)
        result = await self._session.execute(query.options(selectinload(Device.shifts)))
        return list(result.scalars().unique().all())

    async def _compute_energy_and_loss(
        self,
        devices: list[Device],
        start_utc: datetime,
        end_utc: datetime,
        tariff_rate: float,
    ) -> dict[str, Any]:
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        errors: list[str] = []
        by_device: list[dict[str, Any]] = []
        total_energy_kwh = 0.0
        total_loss_kwh = 0.0

        async def _process(device: Device) -> None:
            nonlocal total_energy_kwh, total_loss_kwh
            async with sem:
                rows = await self._fetch_telemetry_window(device.device_id, start_utc, end_utc)
            if not rows:
                by_device.append(
                    {
                        "device_id": device.device_id,
                        "device_name": device.device_name,
                        "idle_kwh": 0.0,
                        "off_hours_kwh": 0.0,
                        "overconsumption_kwh": 0.0,
                        "total_loss_kwh": 0.0,
                        "status": "partial",
                        "reason": "No telemetry in requested window",
                    }
                )
                return

            accounting = aggregate_window(
                rows,
                platform_tz=_get_platform_tz(),
                shifts=[s for s in (device.shifts or []) if s.is_active],
                idle_threshold=resolve_device_thresholds(device).derived_idle_threshold_a,
                over_threshold=resolve_device_thresholds(device).derived_overconsumption_threshold_a,
            )
            if accounting.samples == 0:
                by_device.append(
                    {
                        "device_id": device.device_id,
                        "device_name": device.device_name,
                        "idle_kwh": 0.0,
                        "off_hours_kwh": 0.0,
                        "overconsumption_kwh": 0.0,
                        "total_loss_kwh": 0.0,
                        "status": "partial",
                        "reason": "Telemetry timestamps invalid",
                    }
                )
                return

            energy_kwh = float(accounting.total.energy_kwh)
            idle_kwh = float(accounting.total.idle_kwh)
            offhours_kwh = float(accounting.total.offhours_kwh)
            over_kwh = float(accounting.total.overconsumption_kwh)
            device_loss_kwh = float(accounting.total.total_loss_kwh)
            total_energy_kwh += energy_kwh
            total_loss_kwh += device_loss_kwh
            by_device.append(
                {
                    "device_id": device.device_id,
                    "device_name": device.device_name,
                    "idle_kwh": round(idle_kwh, 4),
                    "off_hours_kwh": round(offhours_kwh, 4),
                    "overconsumption_kwh": round(over_kwh, 4),
                    "total_loss_kwh": round(device_loss_kwh, 4),
                    "status": "computed",
                    "reason": None,
                }
            )

        await asyncio.gather(*[_process(device) for device in devices], return_exceptions=False)

        total_energy_cost = round(total_energy_kwh * tariff_rate, 4)
        total_loss_cost = round(total_loss_kwh * tariff_rate, 4)
        invariant_ok = total_loss_cost <= (total_energy_cost + LOSS_TOLERANCE_INR)
        if not invariant_ok:
            errors.append("Loss cost exceeded energy cost tolerance")

        return {
            "by_device": by_device,
            "total_energy_kwh": round(total_energy_kwh, 4),
            "total_energy_cost_inr": total_energy_cost,
            "total_loss_kwh": round(total_loss_kwh, 4),
            "total_loss_cost_inr": total_loss_cost,
            "invariant_checks": {
                "today_loss_lte_today_energy_cost": invariant_ok,
                "tolerance_inr": LOSS_TOLERANCE_INR,
            },
            "no_nan_inf": True,
            "errors": errors,
        }

    def _compute_device_energy_and_loss_from_rows(
        self,
        device: Device,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rows:
            return {
                "device_id": device.device_id,
                "device_name": device.device_name,
                "idle_kwh": 0.0,
                "off_hours_kwh": 0.0,
                "overconsumption_kwh": 0.0,
                "total_loss_kwh": 0.0,
                "status": "partial",
                "reason": "No telemetry in requested window",
            }

        accounting = aggregate_window(
            rows,
            platform_tz=_get_platform_tz(),
            shifts=[s for s in (device.shifts or []) if s.is_active],
            idle_threshold=resolve_device_thresholds(device).derived_idle_threshold_a,
            over_threshold=resolve_device_thresholds(device).derived_overconsumption_threshold_a,
        )
        if accounting.samples == 0:
            return {
                "device_id": device.device_id,
                "device_name": device.device_name,
                "idle_kwh": 0.0,
                "off_hours_kwh": 0.0,
                "overconsumption_kwh": 0.0,
                "total_loss_kwh": 0.0,
                "status": "partial",
                "reason": "Telemetry timestamps invalid",
            }

        return {
            "device_id": device.device_id,
            "device_name": device.device_name,
            "energy_kwh": round(float(accounting.total.energy_kwh), 4),
            "idle_kwh": round(float(accounting.total.idle_kwh), 4),
            "off_hours_kwh": round(float(accounting.total.offhours_kwh), 4),
            "overconsumption_kwh": round(float(accounting.total.overconsumption_kwh), 4),
            "total_loss_kwh": round(float(accounting.total.total_loss_kwh), 4),
            "status": "computed",
            "reason": None,
        }

    @staticmethod
    def _filter_rows_to_window(
        rows: list[dict[str, Any]],
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for row in rows:
            ts_raw = row.get("timestamp") or row.get("time")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if start_utc <= ts < end_utc:
                filtered.append(row)
        return filtered

    async def _fetch_telemetry_windows_for_devices(
        self,
        devices: list[Device],
        start_utc: datetime,
        end_utc: datetime,
    ) -> dict[str, list[dict[str, Any]]]:
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        result: dict[str, list[dict[str, Any]]] = {}

        async def _fetch(device: Device) -> None:
            async with sem:
                rows = await self._fetch_telemetry_window(device.device_id, start_utc, end_utc)
            result[device.device_id] = rows

        await asyncio.gather(*[_fetch(device) for device in devices], return_exceptions=False)
        return result

    @staticmethod
    def _summarize_energy_and_loss(
        by_device: list[dict[str, Any]],
        tariff_rate: float,
        errors: list[str],
    ) -> dict[str, Any]:
        total_loss_kwh = round(sum(float(row.get("total_loss_kwh", 0.0)) for row in by_device), 4)
        total_energy_kwh = round(sum(float(row.get("energy_kwh", 0.0)) for row in by_device), 4)
        total_energy_cost = round(total_energy_kwh * tariff_rate, 4)
        total_loss_cost = round(total_loss_kwh * tariff_rate, 4)
        invariant_ok = total_loss_cost <= (total_energy_cost + LOSS_TOLERANCE_INR)
        if not invariant_ok:
            errors.append("Loss cost exceeded energy cost tolerance")
        return {
            "by_device": by_device,
            "total_energy_kwh": total_energy_kwh,
            "total_energy_cost_inr": total_energy_cost,
            "total_loss_kwh": total_loss_kwh,
            "total_loss_cost_inr": total_loss_cost,
            "invariant_checks": {
                "today_loss_lte_today_energy_cost": invariant_ok,
                "tolerance_inr": LOSS_TOLERANCE_INR,
            },
            "no_nan_inf": True,
            "errors": errors,
        }

    async def _get_alerts_summary(self) -> tuple[Dict[str, int], Optional[str]]:
        """Fetch alert summary from rule-engine service."""
        url = f"{settings.RULE_ENGINE_SERVICE_BASE_URL}/api/v1/alerts/events/summary"
        payload, reason = await self._http_get_json(service_key="rule_engine", url=url)
        if not isinstance(payload, dict):
            return {
                "active_alerts": 0,
                "alerts_triggered": 0,
                "alerts_cleared": 0,
                "rules_created": 0,
            }, self._build_warning("rule_engine", reason)
        data = payload.get("data", {})
        if not isinstance(data, dict):
            data = {}
        return {
            "active_alerts": int(data.get("active_alerts", 0)),
            "alerts_triggered": int(data.get("alerts_triggered", 0)),
            "alerts_cleared": int(data.get("alerts_cleared", 0)),
            "rules_created": int(data.get("rules_created", 0)),
        }, None

    async def _fetch_latest_telemetry_values(
        self,
        client: httpx.AsyncClient,
        device_id: str,
    ) -> dict[str, float]:
        """Fetch latest telemetry point and return numeric fields only."""
        try:
            url = f"{settings.DATA_SERVICE_BASE_URL}/api/v1/data/telemetry/{device_id}"
            response = await request_with_retries(
                client,
                "GET",
                url,
                operation="dashboard_fetch_latest_telemetry_values",
                params={"limit": "1"},
                headers=build_internal_headers(
                    "device-service",
                    self._ctx.tenant_id if self._ctx is not None else None,
                ),
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("data", {}).get("items", []) if isinstance(payload, dict) else []
            if not items:
                return {}
            latest = items[0]
            numeric_values: dict[str, float] = {}
            for key, value in latest.items():
                if key in {"timestamp", "device_id", "schema_version", "enrichment_status", "table"}:
                    continue
                if isinstance(value, (int, float)):
                    numeric_values[key] = float(value)
            return numeric_values
        except Exception:
            return {}

    async def _build_fleet_state_snapshot(self) -> dict[str, Any]:
        latest_bucket_subq = (
            select(
                DevicePerformanceTrend.device_id.label("device_id"),
                func.max(DevicePerformanceTrend.bucket_start_utc).label("latest_bucket"),
            )
            .group_by(DevicePerformanceTrend.device_id)
            .subquery()
        )
        latest_trend_subq = (
            select(
                DevicePerformanceTrend.device_id.label("device_id"),
                DevicePerformanceTrend.health_score.label("health_score"),
                DevicePerformanceTrend.uptime_percentage.label("uptime_percentage"),
            )
            .join(
                latest_bucket_subq,
                and_(
                    DevicePerformanceTrend.device_id == latest_bucket_subq.c.device_id,
                    DevicePerformanceTrend.bucket_start_utc == latest_bucket_subq.c.latest_bucket,
                ),
            )
            .subquery()
        )
        active_shift_subq = (
            select(
                DeviceShift.device_id.label("device_id"),
                func.count(DeviceShift.id).label("active_shift_count"),
            )
            .where(DeviceShift.is_active.is_(True))
            .group_by(DeviceShift.device_id)
            .subquery()
        )

        device_query = (
            select(
                Device.device_id,
                Device.device_name,
                Device.device_type,
                Device.plant_id,
                Device.location,
                Device.full_load_current_a,
                Device.idle_threshold_pct_of_fla,
                Device.first_telemetry_timestamp,
                Device.last_seen_timestamp,
                DeviceLiveState.runtime_status.label("live_runtime_status"),
                DeviceLiveState.load_state.label("live_load_state"),
                DeviceLiveState.last_telemetry_ts.label("live_last_seen_timestamp"),
                DeviceLiveState.last_current_a.label("live_last_current_a"),
                DeviceLiveState.last_voltage_v.label("live_last_voltage_v"),
                latest_trend_subq.c.health_score,
                latest_trend_subq.c.uptime_percentage,
                active_shift_subq.c.active_shift_count,
            )
            .outerjoin(
                DeviceLiveState,
                and_(
                    DeviceLiveState.device_id == Device.device_id,
                    DeviceLiveState.tenant_id == Device.tenant_id,
                ),
            )
            .outerjoin(latest_trend_subq, latest_trend_subq.c.device_id == Device.device_id)
            .outerjoin(active_shift_subq, active_shift_subq.c.device_id == Device.device_id)
            .where(Device.deleted_at.is_(None))
        )
        if self._ctx is not None and self._ctx.tenant_id is not None:
            device_query = device_query.where(Device.tenant_id == self._ctx.tenant_id)
        rows = (await self._session.execute(device_query.order_by(Device.device_name.asc()))).all()

        warnings: list[str] = []

        devices: list[dict[str, Any]] = []
        for row in rows:
            last_seen_timestamp = row.live_last_seen_timestamp or row.last_seen_timestamp
            runtime_status = resolve_runtime_status(last_seen_timestamp)
            load_state = resolve_load_state(row.live_load_state, last_seen_timestamp)
            thresholds = resolve_device_thresholds(row)
            current_band = (
                classify_current_band(
                    self._safe_float(row.live_last_current_a),
                    self._safe_float(row.live_last_voltage_v),
                    thresholds,
                )
                if runtime_status == RuntimeStatus.RUNNING.value
                else "unknown"
            )

            devices.append(
                {
                    "device_id": row.device_id,
                    "device_name": row.device_name,
                    "device_type": row.device_type,
                    "plant_id": row.plant_id,
                    "runtime_status": runtime_status,
                    "load_state": load_state,
                    "current_band": current_band,
                    "operational_status": resolve_operational_status(
                        runtime_status=runtime_status,
                        load_state=load_state,
                        current_band=current_band,
                        has_telemetry=last_seen_timestamp is not None,
                    ),
                    "location": row.location,
                    "first_telemetry_timestamp": _iso_utc(row.first_telemetry_timestamp),
                    "last_seen_timestamp": last_seen_timestamp.isoformat() if last_seen_timestamp else None,
                    "health_score": round(float(row.health_score), 2) if row.health_score is not None else None,
                    "uptime_percentage": round(float(row.uptime_percentage), 2) if row.uptime_percentage is not None else None,
                    "has_uptime_config": int(row.active_shift_count or 0) > 0,
                    "freshness_ts": datetime.now(tz=UTC).isoformat(),
                }
            )

        return {
            "success": True,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "stale": bool(warnings),
            "warnings": warnings,
            "degraded_services": self._extract_degraded_services(warnings),
            "devices": devices,
        }

    async def materialize_fleet_state_snapshot(self) -> dict[str, Any]:
        try:
            payload = await self._build_fleet_state_snapshot()
            await self._write_snapshot(self._snapshot_key(FLEET_SNAPSHOT_KEY), payload)
            await self._session.commit()
            tenant_id = self._ctx.require_tenant() if self._ctx is not None and self._ctx.tenant_id is not None else None
            if tenant_id is not None:
                await fleet_stream_broadcaster.publish(
                    tenant_id,
                    "fleet_update",
                    {
                        "generated_at": payload.get("generated_at"),
                        "stale": bool(payload.get("stale", False)),
                        "warnings": payload.get("warnings", []),
                        "degraded_services": payload.get("degraded_services", []),
                        "devices": payload.get("devices", []),
                    },
                )
            return payload
        except Exception as exc:
            SNAPSHOT_MATERIALIZE_FAILURES_TOTAL.labels(
                snapshot_key=FLEET_SNAPSHOT_KEY,
                reason="materialize_error",
            ).inc()
            logger.error("fleet_snapshot_materialization_failed", extra={"error": str(exc)})
            raise

    async def get_fleet_snapshot(
        self,
        page: int = 1,
        page_size: int = 50,
        sort: str = "device_name",
        runtime_filter: Optional[str] = None,
    ) -> dict[str, Any]:
        snapshot, stale = await self._read_snapshot(self._snapshot_key(FLEET_SNAPSHOT_KEY))
        if snapshot is None:
            snapshot = await self.materialize_fleet_state_snapshot()
            stale = False

        all_devices = list(snapshot.get("devices", [])) if isinstance(snapshot, dict) else []
        if runtime_filter:
            all_devices = [d for d in all_devices if d.get("runtime_status") == runtime_filter]

        if sort == "last_seen":
            all_devices.sort(key=lambda d: (d.get("last_seen_timestamp") is None, d.get("last_seen_timestamp")), reverse=True)
        else:
            all_devices.sort(key=lambda d: str(d.get("device_name") or "").lower())

        total = len(all_devices)
        total_pages = max((total + page_size - 1) // page_size, 1)
        page = min(max(page, 1), total_pages)
        offset = (page - 1) * page_size
        page_devices = all_devices[offset : offset + page_size]
        for device in page_devices:
            if "data_freshness_ts" not in device:
                device["data_freshness_ts"] = device.get("freshness_ts")

        warnings = list(snapshot.get("warnings", []))
        stale_flag = bool(stale or snapshot.get("stale", False))
        if stale_flag and not warnings:
            warnings = ["dashboard:stale_snapshot"]
        return {
            "success": True,
            "generated_at": snapshot.get("generated_at"),
            "stale": stale_flag,
            "warnings": warnings,
            "degraded_services": self._extract_degraded_services(warnings),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "devices": page_devices,
        }

    async def get_dashboard_bootstrap(self, device_id: str, tenant_id: str) -> dict[str, Any]:
        from app.services.device import DeviceService
        from app.services.device_property import DevicePropertyService
        from app.services.shift import ShiftService
        from app.services.health_config import HealthConfigService
        from app.services.idle_running import IdleRunningService

        device_service = DeviceService(self._session, self._ctx)
        device = await device_service.get_device(device_id, tenant_id)
        if device is None:
            raise DashboardDeviceNotFoundError(device_id)

        live_state_result = await self._session.execute(
            select(DeviceLiveState).where(
                DeviceLiveState.device_id == device_id,
                DeviceLiveState.tenant_id == tenant_id,
            )
        )
        live_state = live_state_result.scalar_one_or_none()
        live_version = int(live_state.version) if live_state is not None else 0
        thresholds = resolve_device_thresholds(device)

        shift_service = ShiftService(self._session)
        health_service = HealthConfigService(self._session)
        widget_service = DevicePropertyService(self._session)

        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            user_id="dashboard-bootstrap",
            role="system",
            plant_ids=[],
            is_super_admin=False,
        )
        idle_service = IdleRunningService(self._session, tenant_ctx)
        async def _safe_section(name: str, loader, default):
            try:
                return await loader()
            except Exception as exc:
                logger.warning(
                    "dashboard_bootstrap_section_failed",
                    extra={"section": name, "device_id": device_id, "tenant_id": tenant_id, "error": str(exc)},
                )
                return default

        telemetry_seed_task = self._fetch_recent_telemetry_seed(device_id, tenant_id, limit=100)
        shifts_task = shift_service.get_shifts_by_device(device_id, tenant_id)
        health_configs_task = health_service.get_health_configs_by_device(device_id, tenant_id)
        widget_config_task = _safe_section(
            "widget_config",
            lambda: widget_service.get_dashboard_widget_config(device_id, tenant_id),
            None,
        )
        idle_config_task = _safe_section("idle_config", lambda: idle_service.get_idle_config(device_id, tenant_id), None)
        waste_config_task = _safe_section("waste_config", lambda: idle_service.get_waste_config(device_id, tenant_id), None)
        loss_stats_task = _safe_section(
            "loss_stats",
            lambda: self.get_device_loss_stats_from_device(device, device_id, tenant_id),
            None,
        )

        (
            telemetry_rows,
            shifts,
            health_configs,
            widget_config,
            idle_config,
            waste_config,
            loss_stats,
        ) = await asyncio.gather(
            telemetry_seed_task,
            shifts_task,
            health_configs_task,
            widget_config_task,
            idle_config_task,
            waste_config_task,
            loss_stats_task,
        )

        uptime = await shift_service.calculate_uptime(device_id, tenant_id, shifts=shifts)
        authoritative_last_seen = (
            live_state.last_telemetry_ts if live_state is not None and live_state.last_telemetry_ts is not None
            else device.last_seen_timestamp
        )
        now_utc = datetime.now(timezone.utc)
        runtime_status = resolve_runtime_status(authoritative_last_seen, now_utc=now_utc)
        load_state = resolve_load_state(
            live_state.load_state if live_state is not None else None,
            authoritative_last_seen,
            now_utc=now_utc,
        )
        current_value = (
            float(live_state.last_current_a)
            if live_state is not None and live_state.last_current_a is not None
            else None
        )
        voltage_value = (
            float(live_state.last_voltage_v)
            if live_state is not None and live_state.last_voltage_v is not None
            else None
        )
        current_band = (
            classify_current_band(current_value, voltage_value, thresholds)
            if runtime_status == RuntimeStatus.RUNNING.value
            else "unknown"
        )
        projection_current_state = {
            "device_id": device_id,
            "state": load_state if runtime_status == RuntimeStatus.RUNNING.value else "unknown",
            "current_band": current_band,
            "current": current_value,
            "voltage": voltage_value,
            "threshold": thresholds.derived_idle_threshold_a,
            "full_load_current_a": thresholds.full_load_current_a,
            "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
            "derived_idle_threshold_a": thresholds.derived_idle_threshold_a,
            "derived_overconsumption_threshold_a": thresholds.derived_overconsumption_threshold_a,
            "configured": thresholds.configured,
            "timestamp": authoritative_last_seen.isoformat() if authoritative_last_seen is not None else None,
            "idle_streak_started_at": (
                live_state.idle_streak_started_at.astimezone(UTC).isoformat()
                if live_state is not None and live_state.idle_streak_started_at is not None
                else None
            ),
            "idle_streak_duration_sec": int(live_state.idle_streak_duration_sec or 0) if live_state is not None else 0,
            "current_field": None,
            "voltage_field": None,
        }

        shift_items = [
            {
                "id": s.id,
                "device_id": s.device_id,
                "tenant_id": s.tenant_id,
                "shift_name": s.shift_name,
                "shift_start": s.shift_start.isoformat() if s.shift_start else None,
                "shift_end": s.shift_end.isoformat() if s.shift_end else None,
                "maintenance_break_minutes": s.maintenance_break_minutes,
                "day_of_week": s.day_of_week,
                "is_active": s.is_active,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in shifts
        ]

        health_config_items = [
            {
                "id": c.id,
                "device_id": c.device_id,
                "tenant_id": c.tenant_id,
                "parameter_name": c.parameter_name,
                "normal_min": c.normal_min,
                "normal_max": c.normal_max,
                "weight": c.weight,
                "ignore_zero_value": c.ignore_zero_value,
                "is_active": c.is_active,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
            }
            for c in health_configs
        ]

        health_score = None
        telemetry_business = normalize_telemetry_sample(telemetry_rows[0], device).to_dict() if telemetry_rows else None
        if telemetry_rows and health_configs:
            latest = telemetry_rows[0]
            telemetry_values = HealthConfigService.extract_numeric_telemetry_values(latest)
            if telemetry_values:
                machine_state = "RUNNING"
                live_load_state = str(live_state.load_state or "").strip().lower() if live_state is not None else ""
                if live_load_state == "idle":
                    machine_state = "IDLE"
                elif live_load_state == "unloaded":
                    machine_state = "UNLOAD"
                try:
                    health_score = await health_service.calculate_health_score(
                        device_id=device_id,
                        telemetry_values=telemetry_values,
                        machine_state=machine_state,
                        tenant_id=tenant_id,
                    )
                except Exception:
                    health_score = None

        result = {
            "success": True,
            "generated_at": datetime.now(tz=UTC),
            "version": live_version,
            "device": device,
            "telemetry": telemetry_rows,
            "telemetry_business": telemetry_business,
            "uptime": uptime,
            "shifts": shift_items,
            "health_configs": health_config_items,
            "health_score": health_score,
            "widget_config": widget_config,
            "current_state": projection_current_state,
            "idle_stats": None,
            "idle_config": idle_config,
            "waste_config": waste_config,
            "loss_stats": loss_stats,
            "co2_overview": loss_stats.get("co2_overview") if isinstance(loss_stats, dict) else None,
        }
        return result

    async def _safe_get_idle_stats(
        self,
        idle_service: "IdleRunningService",
        device_id: str,
        tenant_id: str,
        device: Any,
    ) -> dict[str, Any]:
        try:
            return await idle_service.get_idle_stats(device_id, tenant_id)
        except Exception as exc:
            logger.warning(
                "dashboard_idle_stats_failed",
                extra={"device_id": device_id, "tenant_id": tenant_id, "error": str(exc)},
            )
            return {
                "device_id": device_id,
                "today": None,
                "month": None,
                "tariff_configured": False,
                "pf_estimated": False,
                "threshold_configured": False,
                "idle_current_threshold": None,
                "data_source_type": getattr(device, "data_source_type", None),
            }

    async def get_device_loss_stats_from_device(
        self,
        device: Any,
        device_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        thresholds = resolve_device_thresholds(device)

        state = await self._session.get(DeviceLiveState, {"device_id": device_id, "tenant_id": tenant_id})
        tariff_rate, currency = await self._get_tariff()
        tariff_configured = tariff_rate > 0
        local_day = datetime.now(tz=UTC).astimezone(_get_platform_tz()).date()
        day_matches = state is not None and state.day_bucket == local_day

        idle_kwh = float(state.today_idle_kwh or 0.0) if day_matches and state is not None else 0.0
        off_hours_kwh = float(state.today_offhours_kwh or 0.0) if day_matches and state is not None else 0.0
        over_kwh = float(state.today_overconsumption_kwh or 0.0) if day_matches and state is not None else 0.0
        total_loss_kwh = float(state.today_loss_kwh or 0.0) if day_matches and state is not None else 0.0
        today_energy_kwh = float(state.today_energy_kwh or 0.0) if day_matches and state is not None else 0.0

        def _cost(value: float) -> float | None:
            return round(value * tariff_rate, 4) if tariff_configured else None

        month_energy_kwh = float(state.month_energy_kwh or 0.0) if state is not None else 0.0
        factor_payload = await EmissionFactorCache.get(tenant_id)
        co2_overview = build_co2_overview(
            tenant_id=tenant_id,
            today_energy_kwh=today_energy_kwh,
            today_loss_kwh=total_loss_kwh,
            today_loss_available=day_matches and state is not None,
            month_energy_kwh=month_energy_kwh,
            factor_payload=factor_payload,
        )

        return {
            "device_id": device_id,
            "day_bucket": local_day.isoformat(),
            "last_telemetry_ts": state.last_telemetry_ts.isoformat() if state and state.last_telemetry_ts else None,
            "updated_at": state.updated_at.isoformat() if state and state.updated_at else None,
            "full_load_current_a": thresholds.full_load_current_a,
            "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
            "derived_idle_threshold_a": thresholds.derived_idle_threshold_a,
            "derived_overconsumption_threshold_a": thresholds.derived_overconsumption_threshold_a,
            "tariff_configured": tariff_configured,
            "currency": currency,
            "today": {
                "idle_kwh": round(idle_kwh, 4),
                "idle_cost_inr": _cost(idle_kwh),
                "off_hours_kwh": round(off_hours_kwh, 4),
                "off_hours_cost_inr": _cost(off_hours_kwh),
                "overconsumption_kwh": round(over_kwh, 4),
                "overconsumption_cost_inr": _cost(over_kwh),
                "total_loss_kwh": round(total_loss_kwh, 4),
                "total_loss_cost_inr": _cost(total_loss_kwh),
                "today_energy_kwh": round(today_energy_kwh, 4),
                "today_energy_cost_inr": _cost(today_energy_kwh),
            },
            "co2_overview": co2_overview,
        }

    async def get_device_loss_stats(self, device_id: str, tenant_id: str) -> dict[str, Any]:
        device = await self._session.execute(
            select(Device).where(
                Device.device_id == device_id,
                Device.tenant_id == tenant_id,
                Device.deleted_at.is_(None),
            )
        )
        device_row = device.scalar_one_or_none()
        if device_row is None:
            raise DashboardDeviceNotFoundError(device_id)
        thresholds = resolve_device_thresholds(device_row)

        state = await self._session.get(DeviceLiveState, {"device_id": device_id, "tenant_id": tenant_id})
        tariff_rate, currency = await self._get_tariff()
        tariff_configured = tariff_rate > 0
        local_day = datetime.now(tz=UTC).astimezone(_get_platform_tz()).date()
        day_matches = state is not None and state.day_bucket == local_day

        idle_kwh = float(state.today_idle_kwh or 0.0) if day_matches and state is not None else 0.0
        off_hours_kwh = float(state.today_offhours_kwh or 0.0) if day_matches and state is not None else 0.0
        over_kwh = float(state.today_overconsumption_kwh or 0.0) if day_matches and state is not None else 0.0
        total_loss_kwh = float(state.today_loss_kwh or 0.0) if day_matches and state is not None else 0.0
        today_energy_kwh = float(state.today_energy_kwh or 0.0) if day_matches and state is not None else 0.0

        def _cost(value: float) -> float | None:
            return round(value * tariff_rate, 4) if tariff_configured else None

        month_energy_kwh = float(state.month_energy_kwh or 0.0) if state is not None else 0.0
        factor_payload = await EmissionFactorCache.get(tenant_id)
        co2_overview = build_co2_overview(
            tenant_id=tenant_id,
            today_energy_kwh=today_energy_kwh,
            today_loss_kwh=total_loss_kwh,
            today_loss_available=day_matches and state is not None,
            month_energy_kwh=month_energy_kwh,
            factor_payload=factor_payload,
        )

        return {
            "device_id": device_id,
            "day_bucket": local_day.isoformat(),
            "last_telemetry_ts": state.last_telemetry_ts.isoformat() if state and state.last_telemetry_ts else None,
            "updated_at": state.updated_at.isoformat() if state and state.updated_at else None,
            "full_load_current_a": thresholds.full_load_current_a,
            "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
            "derived_idle_threshold_a": thresholds.derived_idle_threshold_a,
            "derived_overconsumption_threshold_a": thresholds.derived_overconsumption_threshold_a,
            "tariff_configured": tariff_configured,
            "currency": currency,
            "today": {
                "idle_kwh": round(idle_kwh, 4),
                "idle_cost_inr": _cost(idle_kwh),
                "off_hours_kwh": round(off_hours_kwh, 4),
                "off_hours_cost_inr": _cost(off_hours_kwh),
                "overconsumption_kwh": round(over_kwh, 4),
                "overconsumption_cost_inr": _cost(over_kwh),
                "total_loss_kwh": round(total_loss_kwh, 4),
                "total_loss_cost_inr": _cost(total_loss_kwh),
                "today_energy_kwh": round(today_energy_kwh, 4),
                "today_energy_cost_inr": _cost(today_energy_kwh),
            },
            "co2_overview": co2_overview,
        }

    async def materialize_dashboard_summary_snapshot(self) -> dict[str, Any]:
        try:
            fleet, fleet_stale = await self._read_snapshot(self._snapshot_key(FLEET_SNAPSHOT_KEY))
            if fleet is None:
                fleet = await self.materialize_fleet_state_snapshot()
                fleet_stale = False

            devices = list(fleet.get("devices", []))
            health_values = [float(d["health_score"]) for d in devices if d.get("health_score") is not None]
            uptime_values = [float(d["uptime_percentage"]) for d in devices if d.get("uptime_percentage") is not None]
            running_count = sum(1 for d in devices if d.get("runtime_status") == RuntimeStatus.RUNNING.value)
            status_counts = {
                "unknown": sum(1 for d in devices if d.get("operational_status") == "unknown"),
                "stopped": sum(1 for d in devices if d.get("operational_status") == "stopped"),
                "idle": sum(1 for d in devices if d.get("operational_status") == "idle"),
                "running": sum(1 for d in devices if d.get("operational_status") == "running"),
                "overconsumption": sum(1 for d in devices if d.get("operational_status") == "overconsumption"),
            }
            uptime_configured_count = sum(1 for d in devices if bool(d.get("has_uptime_config")))
            total_devices = len(devices)
            from app.services.health_config import HealthConfigService

            health_service = HealthConfigService(self._session)
            active_health_configs = await health_service.get_active_health_configs_by_devices(
                [str(d.get("device_id")) for d in devices if d.get("device_id")],
                tenant_id=self._ctx.tenant_id if self._ctx is not None else None,
            )
            health_configured_count = len(active_health_configs)

            alerts, alerts_warning = await self._get_alerts_summary()
            if alerts_warning:
                fleet_warnings = list(fleet.get("warnings", []))
                if alerts_warning not in fleet_warnings:
                    fleet_warnings.append(alerts_warning)
                fleet["warnings"] = fleet_warnings
                fleet["degraded_services"] = self._extract_degraded_services(fleet_warnings)
            freshness_seconds = max(1, int(settings.DASHBOARD_COST_FRESHNESS_SECONDS))
            energy_widgets_payload, energy_stale = await self._read_snapshot(
                self._snapshot_key(ENERGY_WIDGETS_KEY),
                stale_after_seconds=freshness_seconds,
            )
            refresh_reasons: list[str] = []
            state_precheck, state_precheck_reasons, _, _ = self._evaluate_cost_data_state(
                energy_widgets_payload,
                is_stale_snapshot=energy_stale,
                freshness_seconds=freshness_seconds,
            )
            if state_precheck != "fresh":
                try:
                    _, refreshed_energy = await self.materialize_energy_and_loss_snapshots()
                    energy_widgets_payload = refreshed_energy
                    energy_stale = False
                except Exception as exc:
                    refresh_reasons.append("refresh_failed")
                    refresh_reasons.append(self._build_warning("energy_service", "refresh_failed"))
                    DASHBOARD_COST_REFRESH_FAILURES_TOTAL.labels(reason="energy_widgets_refresh_failed").inc()
                    logger.warning(
                        "dashboard_energy_widget_refresh_failed",
                        extra={"error": str(exc)},
                    )
            if energy_widgets_payload is None:
                refresh_reasons.append("snapshot_unavailable")
                refresh_reasons.append(self._build_warning("energy_service", "snapshot_unavailable"))
                energy_widgets_payload = {
                    "month_energy_kwh": 0.0,
                    "month_energy_cost_inr": 0.0,
                    "today_energy_kwh": 0.0,
                    "today_energy_cost_inr": 0.0,
                    "today_loss_kwh": 0.0,
                    "today_loss_cost_inr": 0.0,
                    "generated_at": datetime.now(tz=UTC).isoformat(),
                    "currency": "INR",
                    "data_quality": "stale",
                    "invariant_checks": {},
                    "reconciliation_warning": "energy_snapshot_unavailable",
                    "no_nan_inf": True,
                    "warnings": ["snapshot_unavailable"],
                }
                energy_stale = True

            cost_state, cost_reasons, cost_generated_at, cost_age_seconds = self._evaluate_cost_data_state(
                energy_widgets_payload,
                is_stale_snapshot=energy_stale,
                freshness_seconds=freshness_seconds,
                extra_reasons=refresh_reasons,
            )
            DASHBOARD_COST_DATA_STATE_TOTAL.labels(state=cost_state).inc()
            if cost_age_seconds is not None:
                DASHBOARD_COST_DATA_AGE_SECONDS.set(cost_age_seconds)

            payload = {
                "success": True,
                "generated_at": datetime.now(tz=UTC).isoformat(),
                "stale": bool(fleet_stale or energy_stale),
                "warnings": list(fleet.get("warnings", [])),
                "degraded_services": list(fleet.get("degraded_services", [])),
                "summary": {
                    "total_devices": total_devices,
                    "running_devices": running_count,
                    "stopped_devices": status_counts["stopped"],
                    "idle_devices": status_counts["idle"],
                    "in_load_devices": status_counts["running"],
                    "overconsumption_devices": status_counts["overconsumption"],
                    "unknown_devices": status_counts["unknown"],
                    "status_counts": status_counts,
                    "devices_with_health_data": len(health_values),
                    "devices_with_health_configured": health_configured_count,
                    "devices_missing_health_config": max(total_devices - health_configured_count, 0),
                    "devices_with_uptime_configured": uptime_configured_count,
                    "devices_missing_uptime_config": max(total_devices - uptime_configured_count, 0),
                    "system_health": round(sum(health_values) / len(health_values), 2) if health_values else None,
                    "average_efficiency": round(sum(uptime_values) / len(uptime_values), 2) if uptime_values else None,
                },
                "alerts": alerts,
                "devices": [
                    {
                        "device_id": d.get("device_id"),
                        "device_name": d.get("device_name"),
                        "device_type": d.get("device_type"),
                        "runtime_status": d.get("runtime_status"),
                        "operational_status": d.get("operational_status"),
                        "location": d.get("location"),
                        "first_telemetry_timestamp": d.get("first_telemetry_timestamp"),
                        "last_seen_timestamp": d.get("last_seen_timestamp"),
                        "health_score": d.get("health_score"),
                        "uptime_percentage": d.get("uptime_percentage"),
                    }
                    for d in devices
                ],
                "energy_widgets": energy_widgets_payload,
                "cost_data_state": cost_state,
                "cost_data_reasons": cost_reasons,
                "cost_generated_at": cost_generated_at.isoformat() if cost_generated_at else None,
            }
            if energy_stale:
                payload["warnings"].append("energy_widgets_stale")
            if cost_state != "fresh":
                payload["warnings"].append("cost_data_not_fresh")
            payload["degraded_services"] = self._extract_degraded_services(payload["warnings"])
            await self._write_snapshot(self._snapshot_key(DASHBOARD_SUMMARY_KEY), payload)
            await self._session.commit()
            return payload
        except Exception as exc:
            SNAPSHOT_MATERIALIZE_FAILURES_TOTAL.labels(
                snapshot_key=DASHBOARD_SUMMARY_KEY,
                reason="materialize_error",
            ).inc()
            logger.error("dashboard_summary_materialization_failed", extra={"error": str(exc)})
            raise

    async def get_dashboard_summary(self) -> Dict[str, Any]:
        payload, stale = await self._read_snapshot(self._snapshot_key(DASHBOARD_SUMMARY_KEY))
        requires_refresh = payload is None
        if payload is not None:
            if payload.get("cost_data_state") is None:
                requires_refresh = True
            elif bool(payload.get("stale", False)):
                requires_refresh = True
        if requires_refresh:
            payload = await self.materialize_dashboard_summary_snapshot()
            stale = False
        payload["stale"] = bool(stale or payload.get("stale", False))
        payload.setdefault("warnings", [])
        payload.setdefault("degraded_services", self._extract_degraded_services(payload["warnings"]))
        payload.setdefault("cost_data_state", "unavailable")
        payload.setdefault("cost_data_reasons", [])
        payload.setdefault("cost_generated_at", None)
        return payload

    async def materialize_energy_and_loss_snapshots(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            now_utc = datetime.now(tz=UTC)
            local_tz = _get_platform_tz()
            today_start_local = datetime.combine(now_utc.astimezone(local_tz).date(), time.min, tzinfo=local_tz)
            today_start_utc = today_start_local.astimezone(UTC)
            month_start_local = datetime.combine(
                now_utc.astimezone(local_tz).replace(day=1).date(),
                time.min,
                tzinfo=local_tz,
            )
            month_start_utc = month_start_local.astimezone(UTC)
            devices = await self._get_all_devices_with_shifts()
            tariff_rate, currency = await self._get_tariff()

            telemetry_by_device = await self._fetch_telemetry_windows_for_devices(
                devices, month_start_utc, now_utc,
            )

            agg_by_device: list[dict[str, Any]] = []
            for device in devices:
                month_rows = telemetry_by_device.get(device.device_id, [])
                today_rows = self._filter_rows_to_window(month_rows, today_start_utc, now_utc)
                agg_by_device.append(self._compute_device_energy_and_loss_from_rows(device, today_rows))

            month_agg_by_device: list[dict[str, Any]] = []
            for device in devices:
                month_rows = telemetry_by_device.get(device.device_id, [])
                month_agg_by_device.append(self._compute_device_energy_and_loss_from_rows(device, month_rows))

            agg = self._summarize_energy_and_loss(agg_by_device, tariff_rate, [])
            month_agg = self._summarize_energy_and_loss(month_agg_by_device, tariff_rate, [])

            idle_kwh = round(sum(float(row["idle_kwh"]) for row in agg["by_device"]), 4)
            offhours_kwh = round(sum(float(row["off_hours_kwh"]) for row in agg["by_device"]), 4)
            over_kwh = round(sum(float(row["overconsumption_kwh"]) for row in agg["by_device"]), 4)

            rows = []
            for row in agg["by_device"]:
                rows.append(
                    {
                        "device_id": row["device_id"],
                        "device_name": row["device_name"],
                        "idle_kwh": row["idle_kwh"],
                        "idle_cost_inr": round(float(row["idle_kwh"]) * tariff_rate, 4),
                        "off_hours_kwh": row["off_hours_kwh"],
                        "off_hours_cost_inr": round(float(row["off_hours_kwh"]) * tariff_rate, 4),
                        "overconsumption_kwh": row["overconsumption_kwh"],
                        "overconsumption_cost_inr": round(float(row["overconsumption_kwh"]) * tariff_rate, 4),
                        "total_loss_kwh": row["total_loss_kwh"],
                        "total_loss_cost_inr": round(float(row["total_loss_kwh"]) * tariff_rate, 4),
                        "status": row["status"],
                        "reason": row["reason"],
                    }
                )
            rows.sort(key=lambda r: r["total_loss_cost_inr"], reverse=True)

            loss_payload = {
                "success": True,
                "generated_at": now_utc.isoformat(),
                "currency": currency,
                "totals": {
                    "idle_kwh": idle_kwh,
                    "idle_cost_inr": round(idle_kwh * tariff_rate, 4),
                    "off_hours_kwh": offhours_kwh,
                    "off_hours_cost_inr": round(offhours_kwh * tariff_rate, 4),
                    "overconsumption_kwh": over_kwh,
                    "overconsumption_cost_inr": round(over_kwh * tariff_rate, 4),
                    "total_loss_kwh": agg["total_loss_kwh"],
                    "total_loss_cost_inr": agg["total_loss_cost_inr"],
                    "today_energy_kwh": agg["total_energy_kwh"],
                    "today_energy_cost_inr": agg["total_energy_cost_inr"],
                },
                "rows": rows,
                "data_quality": "ok" if not agg["errors"] else "partial",
                "invariant_checks": agg["invariant_checks"],
                "no_nan_inf": agg["no_nan_inf"],
                "warnings": agg["errors"],
            }
            energy_payload = {
                "month_energy_kwh": month_agg["total_energy_kwh"],
                "month_energy_cost_inr": month_agg["total_energy_cost_inr"],
                "today_energy_kwh": agg["total_energy_kwh"],
                "today_energy_cost_inr": agg["total_energy_cost_inr"],
                "today_loss_kwh": agg["total_loss_kwh"],
                "today_loss_cost_inr": agg["total_loss_cost_inr"],
                "generated_at": now_utc.isoformat(),
                "currency": currency,
                "data_quality": "ok" if not agg["errors"] else "partial",
                "invariant_checks": agg["invariant_checks"],
                "reconciliation_warning": None,
                "no_nan_inf": agg["no_nan_inf"],
            }

            await self._write_snapshot(self._snapshot_key(TODAY_LOSS_KEY), loss_payload)
            await self._write_snapshot(self._snapshot_key(ENERGY_WIDGETS_KEY), energy_payload)
            await self._session.commit()
            return loss_payload, energy_payload
        except Exception as exc:
            SNAPSHOT_MATERIALIZE_FAILURES_TOTAL.labels(
                snapshot_key=TODAY_LOSS_KEY,
                reason="materialize_error",
            ).inc()
            SNAPSHOT_MATERIALIZE_FAILURES_TOTAL.labels(
                snapshot_key=ENERGY_WIDGETS_KEY,
                reason="materialize_error",
            ).inc()
            logger.error("energy_loss_materialization_failed", extra={"error": str(exc)})
            raise

    async def get_today_loss_breakdown(self) -> dict[str, Any]:
        freshness_seconds = max(1, int(settings.DASHBOARD_COST_FRESHNESS_SECONDS))
        payload, stale = await self._read_snapshot(self._snapshot_key(TODAY_LOSS_KEY), stale_after_seconds=freshness_seconds)
        refresh_reasons: list[str] = []
        pre_state, _, _, _ = self._evaluate_cost_data_state(
            payload,
            is_stale_snapshot=stale,
            freshness_seconds=freshness_seconds,
        )
        if pre_state != "fresh":
            try:
                refreshed_loss, _ = await self.materialize_energy_and_loss_snapshots()
                payload = refreshed_loss
                stale = False
            except Exception as exc:
                refresh_reasons.append("refresh_failed")
                DASHBOARD_COST_REFRESH_FAILURES_TOTAL.labels(reason="today_loss_refresh_failed").inc()
                logger.warning(
                    "today_loss_refresh_failed",
                    extra={"error": str(exc)},
                )

        if payload is None:
            refresh_reasons.append("snapshot_unavailable")
            payload = {
                "success": True,
                "generated_at": datetime.now(tz=UTC).isoformat(),
                "currency": "INR",
                "totals": {
                    "idle_kwh": 0.0,
                    "idle_cost_inr": 0.0,
                    "off_hours_kwh": 0.0,
                    "off_hours_cost_inr": 0.0,
                    "overconsumption_kwh": 0.0,
                    "overconsumption_cost_inr": 0.0,
                    "total_loss_kwh": 0.0,
                    "total_loss_cost_inr": 0.0,
                    "today_energy_kwh": 0.0,
                    "today_energy_cost_inr": 0.0,
                },
                "rows": [],
                "data_quality": "stale",
                "invariant_checks": {},
                "no_nan_inf": True,
                "warnings": ["snapshot_unavailable"],
            }
            stale = True

        cost_state, cost_reasons, cost_generated_at, cost_age_seconds = self._evaluate_cost_data_state(
            payload,
            is_stale_snapshot=stale,
            freshness_seconds=freshness_seconds,
            extra_reasons=refresh_reasons,
        )
        DASHBOARD_COST_DATA_STATE_TOTAL.labels(state=cost_state).inc()
        if cost_age_seconds is not None:
            DASHBOARD_COST_DATA_AGE_SECONDS.set(cost_age_seconds)

        payload["stale"] = stale
        payload["cost_data_state"] = cost_state
        payload["cost_data_reasons"] = cost_reasons
        payload["cost_generated_at"] = cost_generated_at.isoformat() if cost_generated_at else None
        return payload

    async def materialize_monthly_energy_snapshot(self, year: int, month: int) -> dict[str, Any]:
        try:
            local_tz = _get_platform_tz()
            start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=local_tz)
            if month == 12:
                end_local = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=local_tz)
            else:
                end_local = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=local_tz)
            start_utc = start_local.astimezone(UTC)
            end_utc = end_local.astimezone(UTC)

            devices = await self._get_all_devices_with_shifts()
            tariff_rate, currency = await self._get_tariff()
            sem = asyncio.Semaphore(FETCH_CONCURRENCY)
            day_totals: dict[date, float] = {}

            async def _process(device: Device) -> None:
                async with sem:
                    rows = await self._fetch_telemetry_window(device.device_id, start_utc, end_utc)
                accounting = aggregate_window(
                    rows,
                    platform_tz=local_tz,
                    shifts=[],
                    idle_threshold=None,
                    over_threshold=None,
                )
                for bucket, totals in accounting.by_day.items():
                    if totals.energy_kwh <= 0:
                        continue
                    day_totals[bucket] = day_totals.get(bucket, 0.0) + float(totals.energy_kwh)

            await asyncio.gather(*[_process(device) for device in devices], return_exceptions=False)

            days: list[dict[str, Any]] = []
            running_day = start_local.date()
            while running_day < end_local.date():
                kwh = round(day_totals.get(running_day, 0.0), 4)
                days.append(
                    {
                        "date": running_day.isoformat(),
                        "energy_kwh": kwh,
                        "energy_cost_inr": round(kwh * tariff_rate, 4),
                    }
                )
                running_day += timedelta(days=1)

            month_kwh = round(sum(d["energy_kwh"] for d in days), 4)
            payload = {
                "success": True,
                "year": year,
                "month": month,
                "currency": currency,
                "generated_at": datetime.now(tz=UTC).isoformat(),
                "warnings": [],
                "summary": {
                    "total_energy_kwh": month_kwh,
                    "total_energy_cost_inr": round(month_kwh * tariff_rate, 4),
                },
                "days": days,
                "data_quality": "ok",
                "no_nan_inf": True,
            }
            snapshot_key = self._snapshot_key(f"{MONTHLY_ENERGY_PREFIX}:{year:04d}-{month:02d}")
            await self._write_snapshot(snapshot_key, payload)
            await self._session.commit()
            return payload
        except Exception as exc:
            SNAPSHOT_MATERIALIZE_FAILURES_TOTAL.labels(
                snapshot_key=f"{MONTHLY_ENERGY_PREFIX}:{year:04d}-{month:02d}",
                reason="materialize_error",
            ).inc()
            logger.error(
                "monthly_energy_materialization_failed",
                extra={"year": year, "month": month, "error": str(exc)},
            )
            raise

    async def get_monthly_energy(self, year: int, month: int) -> dict[str, Any]:
        key = self._snapshot_key(f"{MONTHLY_ENERGY_PREFIX}:{year:04d}-{month:02d}")
        payload, stale = await self._read_snapshot(key, stale_after_seconds=1800)
        refresh_reasons: list[str] = []
        pre_state, _, _, _ = self._evaluate_cost_data_state(
            payload,
            is_stale_snapshot=stale,
            freshness_seconds=1800,
        )
        if payload is None or pre_state != "fresh":
            try:
                payload = await self.materialize_monthly_energy_snapshot(year=year, month=month)
                stale = False
            except Exception as exc:
                refresh_reasons.append("refresh_failed")
                DASHBOARD_COST_REFRESH_FAILURES_TOTAL.labels(reason="calendar_refresh_failed").inc()
                logger.warning(
                    "calendar_monthly_refresh_failed",
                    extra={"year": year, "month": month, "error": str(exc)},
                )

        if payload is None:
            refresh_reasons.append("snapshot_unavailable")
            payload = {
                "success": True,
                "year": year,
                "month": month,
                "currency": "INR",
                "generated_at": datetime.now(tz=UTC).isoformat(),
                "summary": {"total_energy_kwh": 0.0, "total_energy_cost_inr": 0.0},
                "days": [],
                "data_quality": "stale",
                "no_nan_inf": True,
                "warnings": ["snapshot_unavailable"],
            }
            stale = True

        cost_state, cost_reasons, cost_generated_at, cost_age_seconds = self._evaluate_cost_data_state(
            payload,
            is_stale_snapshot=stale,
            freshness_seconds=1800,
            extra_reasons=refresh_reasons,
        )
        if cost_age_seconds is not None:
            CALENDAR_COST_SNAPSHOT_AGE_SECONDS.set(cost_age_seconds)
        DASHBOARD_COST_DATA_STATE_TOTAL.labels(state=cost_state).inc()

        payload["stale"] = stale
        payload["cost_data_state"] = cost_state
        payload["cost_data_reasons"] = cost_reasons
        payload["cost_generated_at"] = cost_generated_at.isoformat() if cost_generated_at else None
        return payload

    async def materialize_hot_snapshots(self) -> dict[str, Any]:
        fleet = await self.materialize_fleet_state_snapshot()
        summary = await self.materialize_dashboard_summary_snapshot()
        return {
            "fleet_devices": len(fleet.get("devices", [])),
            "summary_devices": len(summary.get("devices", [])),
            "generated_at": summary.get("generated_at"),
        }

    @staticmethod
    def observe_stream_emit_lag(created_at: datetime) -> None:
        now_utc = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        lag = max(0.0, (now_utc - created_at).total_seconds())
        FLEET_STREAM_EMIT_LAG_SECONDS.observe(lag)

    @staticmethod
    def record_stream_disconnect(reason: str) -> None:
        FLEET_STREAM_DISCONNECTS_TOTAL.labels(reason=reason).inc()
