"""Performance trend materialization and query service."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo
import logging

import httpx
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.device import Device, DevicePerformanceTrend, DeviceRecentTelemetrySample
from app.services.health_config import HealthConfigService
from app.services.shift import ShiftService
from app.services.shared_http import get_client, request_with_retries
from services.shared.tenant_context import TenantContext, build_internal_headers

logger = logging.getLogger(__name__)


RANGE_TO_DELTA = {
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


class PerformanceTrendService:
    """Service for building and querying materialized trend snapshots."""

    _HEALTH_MESSAGE_PREFIXES = (
        "Machine is ",
        "Health score calculated from ",
        "No health parameters configured.",
        "Weight validation failed:",
        "No matching telemetry parameters found for configured health metrics.",
    )
    _HISTORICAL_HEALTH_MACHINE_STATE = "RUNNING"
    _RECENT_HEALTH_REPAIR_WINDOW = RANGE_TO_DELTA["24h"]
    _FALLBACK_LOOKBACK_MIN = RANGE_TO_DELTA["24h"]
    _FALLBACK_LOOKBACK_MAX = RANGE_TO_DELTA["7d"]

    def __init__(self, session: AsyncSession, ctx: TenantContext | None = None):
        self._session = session
        self._ctx = ctx
        self._shift_service = ShiftService(session)
        self._health_service = HealthConfigService(session)
        self._tz = ZoneInfo(settings.PERFORMANCE_TRENDS_TIMEZONE)

    def _bucket_bounds_utc(self, now_utc: datetime) -> tuple[datetime, datetime]:
        interval = max(1, settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES)
        now_local = now_utc.astimezone(self._tz)
        minute_floor = (now_local.minute // interval) * interval
        bucket_end_local = now_local.replace(minute=minute_floor, second=0, microsecond=0)
        bucket_start_local = bucket_end_local - timedelta(minutes=interval)
        return (
            bucket_start_local.astimezone(timezone.utc),
            bucket_end_local.astimezone(timezone.utc),
        )

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _compose_backfilled_message(self, existing_message: Optional[str], health_message: Optional[str]) -> Optional[str]:
        if not existing_message:
            return health_message

        parts = [part for part in existing_message.split(" | ") if part]
        uptime_tail: list[str] = parts
        if parts and parts[0].startswith(self._HEALTH_MESSAGE_PREFIXES):
            uptime_tail = parts[1:]

        combined = []
        if health_message:
            combined.append(health_message)
        combined.extend(uptime_tail)
        return " | ".join(combined) if combined else None

    def _split_bucket_message(self, existing_message: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if not existing_message:
            return None, None
        parts = [part for part in existing_message.split(" | ") if part]
        if not parts:
            return None, None
        if parts[0].startswith(self._HEALTH_MESSAGE_PREFIXES):
            health_message = parts[0]
            uptime_message = " | ".join(parts[1:]) if len(parts) > 1 else None
            return health_message, uptime_message
        return None, " | ".join(parts)

    def _compose_bucket_message(
        self,
        *,
        health_message: Optional[str],
        uptime_message: Optional[str],
    ) -> Optional[str]:
        parts = [part for part in (health_message, uptime_message) if part]
        return " | ".join(parts) if parts else None

    @staticmethod
    def _metric_column(metric: str) -> str:
        return "health_score" if metric == "health" else "uptime_percentage"

    def _metric_message_from_bucket(self, metric: str, message: Optional[str]) -> Optional[str]:
        health_message, uptime_message = self._split_bucket_message(message)
        return health_message if metric == "health" else uptime_message

    def _metric_empty_message(self, metric: str) -> str:
        if metric == "health":
            return "No health trend data available for the selected window."
        return "No uptime trend data available for the selected window."

    def _metric_stale_message(self, metric: str, last_actual_ts: datetime) -> str:
        metric_name = "health" if metric == "health" else "uptime"
        ts_local = last_actual_ts.astimezone(self._tz).isoformat()
        return f"No new {metric_name} points in selected window. Showing last known value from {ts_local}."

    def _fallback_horizon(self, range_delta: timedelta) -> timedelta:
        return min(max(range_delta, self._FALLBACK_LOOKBACK_MIN), self._FALLBACK_LOOKBACK_MAX)

    @staticmethod
    def _decode_recent_payload(payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            decoded = json.loads(payload)
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    async def _fetch_recent_bucket_health_sample(
        self,
        device_id: str,
        tenant_id: str,
        bucket_start_utc: datetime,
        bucket_end_utc: datetime,
    ) -> tuple[dict[str, float], int, bool]:
        rows = (
            await self._session.execute(
                select(DeviceRecentTelemetrySample)
                .where(
                    DeviceRecentTelemetrySample.device_id == device_id,
                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                    DeviceRecentTelemetrySample.sample_ts >= bucket_start_utc,
                    DeviceRecentTelemetrySample.sample_ts <= bucket_end_utc,
                )
                .order_by(DeviceRecentTelemetrySample.sample_ts.desc(), DeviceRecentTelemetrySample.id.desc())
            )
        ).scalars().all()
        if not rows:
            return {}, 0, False

        for row in rows:
            payload = self._health_service.extract_numeric_telemetry_values(
                self._decode_recent_payload(row.telemetry_json)
            )
            if payload:
                return payload, len(rows), True
        return {}, len(rows), True

    async def _get_metric_fallback_point(
        self,
        *,
        device_id: str,
        tenant_id: str,
        metric: str,
        start_utc: datetime,
        range_delta: timedelta,
    ) -> DevicePerformanceTrend | None:
        metric_column = getattr(DevicePerformanceTrend, self._metric_column(metric))
        fallback_start_utc = start_utc - self._fallback_horizon(range_delta)
        result = await self._session.execute(
            select(DevicePerformanceTrend)
            .where(
                DevicePerformanceTrend.device_id == device_id,
                DevicePerformanceTrend.tenant_id == tenant_id,
                DevicePerformanceTrend.bucket_start_utc < start_utc,
                DevicePerformanceTrend.bucket_start_utc >= fallback_start_utc,
                metric_column.is_not(None),
            )
            .order_by(DevicePerformanceTrend.bucket_start_utc.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _repair_recent_missing_metric_rows_on_read(
        self,
        *,
        device_id: str,
        tenant_id: str,
        metric: str,
        rows: list[DevicePerformanceTrend],
        now_utc: datetime,
    ) -> bool:
        metric_key = self._metric_column(metric)
        repair_cutoff = now_utc - self._RECENT_HEALTH_REPAIR_WINDOW
        repair_candidates = [
            row
            for row in rows
            if getattr(row, metric_key) is None and self._to_utc(row.bucket_start_utc) >= repair_cutoff
        ]
        if not repair_candidates:
            return False

        start_utc = min(row.bucket_start_utc for row in repair_candidates)
        end_utc = max(row.bucket_end_utc for row in repair_candidates) + timedelta(seconds=1)
        if metric == "health":
            await self.backfill_health_scores(
                start_utc=start_utc,
                end_utc=end_utc,
                tenant_id=tenant_id,
                device_id=device_id,
                only_missing_health=True,
                include_current_bucket=False,
            )
        else:
            await self.backfill_uptime_components(
                start_utc=start_utc,
                end_utc=end_utc,
                tenant_id=tenant_id,
                device_id=device_id,
                only_missing_uptime=True,
                include_current_bucket=False,
            )
        return True

    async def _get_device(self, device_id: str, tenant_id: str) -> Device | None:
        result = await self._session.execute(
            select(Device).where(
                Device.device_id == device_id,
                Device.tenant_id == tenant_id,
                Device.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _fetch_bucket_telemetry_mean(
        self,
        device_id: str,
        tenant_id: str,
        bucket_start_utc: datetime,
        bucket_end_utc: datetime,
    ) -> tuple[dict[str, float], int]:
        params = {
            "start_time": bucket_start_utc.isoformat(),
            "end_time": bucket_end_utc.isoformat(),
            "aggregate": "mean",
            "interval": f"{max(1, settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES)}m",
            "limit": "1",
        }
        url = f"{settings.DATA_SERVICE_BASE_URL}/api/v1/data/telemetry/{device_id}"
        logger.debug(
            "performance_trends_fetch_bucket_telemetry_request",
            extra={"device_id": device_id, "url": url, "params": params},
        )
        try:
            client = await get_client(settings.DATA_SERVICE_BASE_URL)
            response = await request_with_retries(
                client,
                "GET",
                f"/api/v1/data/telemetry/{device_id}",
                operation="performance_trends_fetch_bucket_telemetry_mean",
                params=params,
                headers=build_internal_headers("device-service", tenant_id),
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.ConnectError as exc:
            logger.error(
                "performance_trends_fetch_bucket_telemetry_connect_error",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        except httpx.ConnectTimeout as exc:
            logger.error(
                "performance_trends_fetch_bucket_telemetry_connect_timeout",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        except Exception as exc:
            logger.error(
                "performance_trends_fetch_bucket_telemetry_error",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise

        items = payload.get("data", {}).get("items", []) if isinstance(payload, dict) else []
        if not items:
            return {}, 0

        latest = items[-1]
        return self._health_service.extract_numeric_telemetry_values(latest), len(items)

    async def _fetch_bucket_health_sample(
        self,
        device_id: str,
        tenant_id: str,
        bucket_start_utc: datetime,
        bucket_end_utc: datetime,
    ) -> tuple[dict[str, float], int]:
        recent_values, recent_points, recent_covered = await self._fetch_recent_bucket_health_sample(
            device_id,
            tenant_id,
            bucket_start_utc,
            bucket_end_utc,
        )
        if recent_covered:
            return recent_values, recent_points

        params = {
            "start_time": bucket_start_utc.isoformat(),
            "end_time": bucket_end_utc.isoformat(),
            "limit": "1",
        }
        url = f"{settings.DATA_SERVICE_BASE_URL}/api/v1/data/telemetry/{device_id}"
        logger.debug(
            "performance_trends_fetch_bucket_health_sample_request",
            extra={"device_id": device_id, "url": url, "params": params},
        )
        try:
            client = await get_client(settings.DATA_SERVICE_BASE_URL)
            response = await request_with_retries(
                client,
                "GET",
                f"/api/v1/data/telemetry/{device_id}",
                operation="performance_trends_fetch_bucket_health_sample",
                params=params,
                headers=build_internal_headers("device-service", tenant_id),
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.ConnectError as exc:
            logger.error(
                "performance_trends_fetch_bucket_health_sample_connect_error",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        except httpx.ConnectTimeout as exc:
            logger.error(
                "performance_trends_fetch_bucket_health_sample_connect_timeout",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        except Exception as exc:
            logger.error(
                "performance_trends_fetch_bucket_health_sample_error",
                extra={"device_id": device_id, "url": url, "exception_type": type(exc).__name__, "error": str(exc)},
            )
            raise

        items = payload.get("data", {}).get("items", []) if isinstance(payload, dict) else []
        if not items:
            return {}, 0

        latest = items[0]
        return self._health_service.extract_numeric_telemetry_values(latest), len(items)

    async def _get_uptime_components(
        self,
        device_id: str,
        tenant_id: str,
        bucket_start_utc: datetime,
        bucket_end_utc: datetime,
    ) -> tuple[Optional[float], int, int, int, str]:
        uptime = await self._shift_service.calculate_uptime_for_window(
            device_id,
            tenant_id,
            bucket_start_utc,
            bucket_end_utc,
        )
        active_shifts = [s for s in await self._shift_service.get_shifts_by_device(device_id, tenant_id) if s.is_active]
        break_minutes = sum(s.maintenance_break_minutes for s in active_shifts)
        return (
            uptime.get("uptime_percentage"),
            uptime.get("total_planned_minutes", 0),
            uptime.get("total_effective_minutes", 0),
            break_minutes,
            uptime.get("message", ""),
        )

    async def materialize_latest_bucket(self) -> dict[str, int]:
        """Compute and upsert trend snapshot for all devices for latest time bucket."""
        now_utc = datetime.now(timezone.utc)
        bucket_start_utc, bucket_end_utc = self._bucket_bounds_utc(now_utc)

        device_query = select(Device.device_id, Device.tenant_id).where(Device.deleted_at.is_(None))
        if self._ctx is not None and self._ctx.tenant_id is not None:
            device_query = device_query.where(Device.tenant_id == self._ctx.tenant_id)
        device_rows = await self._session.execute(device_query)
        device_ids = [(d[0], d[1]) for d in device_rows.all()]

        created = 0
        updated = 0
        failed = 0

        for device_id, tenant_id in device_ids:
            result = await self._materialize_device_bucket(
                device_id,
                tenant_id,
                bucket_start_utc,
                bucket_end_utc,
            )
            if result == "created":
                created += 1
            elif result == "updated":
                updated += 1
            else:
                failed += 1

        retention_cutoff = now_utc - timedelta(days=max(1, settings.PERFORMANCE_TRENDS_RETENTION_DAYS))
        await self._session.execute(
            delete(DevicePerformanceTrend)
            .where(DevicePerformanceTrend.bucket_start_utc < retention_cutoff)
            .execution_options(synchronize_session=False)
        )

        await self._session.commit()
        return {"devices_total": len(device_ids), "created": created, "updated": updated, "failed": failed}

    async def backfill_health_scores(
        self,
        *,
        start_utc: datetime,
        end_utc: datetime,
        tenant_id: str | None = None,
        device_id: str | None = None,
        only_missing_health: bool = True,
        include_current_bucket: bool = False,
        batch_size: int = 200,
    ) -> dict[str, int | str | bool]:
        start_utc = self._to_utc(start_utc)
        end_utc = self._to_utc(end_utc)
        if end_utc <= start_utc:
            raise ValueError("end_utc must be after start_utc")

        effective_tenant_id = tenant_id or (self._ctx.tenant_id if self._ctx is not None else None)
        if device_id and effective_tenant_id is None:
            raise ValueError("tenant_id is required when backfilling a specific device")

        current_bucket_start_utc, _ = self._bucket_bounds_utc(datetime.now(timezone.utc))
        query = (
            select(DevicePerformanceTrend)
            .where(
                DevicePerformanceTrend.bucket_start_utc >= start_utc,
                DevicePerformanceTrend.bucket_start_utc < end_utc,
            )
            .order_by(
                DevicePerformanceTrend.tenant_id.asc(),
                DevicePerformanceTrend.device_id.asc(),
                DevicePerformanceTrend.bucket_start_utc.asc(),
            )
        )
        if effective_tenant_id is not None:
            query = query.where(DevicePerformanceTrend.tenant_id == effective_tenant_id)
        if device_id is not None:
            query = query.where(DevicePerformanceTrend.device_id == device_id)
        if only_missing_health:
            query = query.where(DevicePerformanceTrend.health_score.is_(None))
        if not include_current_bucket:
            query = query.where(DevicePerformanceTrend.bucket_start_utc < current_bucket_start_utc)

        rows = (await self._session.execute(query)).scalars().all()

        scanned = len(rows)
        updated = 0
        unchanged = 0
        failed = 0
        committed = 0

        for row in rows:
            try:
                telemetry_values, points_used = await self._fetch_bucket_health_sample(
                    row.device_id,
                    row.tenant_id,
                    row.bucket_start_utc,
                    row.bucket_end_utc,
                )
                health_result = await self._health_service.calculate_health_score(
                    device_id=row.device_id,
                    telemetry_values=telemetry_values,
                    # Historical buckets do not persist authoritative machine-state snapshots.
                    machine_state=self._HISTORICAL_HEALTH_MACHINE_STATE,
                    tenant_id=row.tenant_id,
                )
                next_health_score = health_result.get("health_score")
                next_message = self._compose_backfilled_message(row.message, health_result.get("message"))
                next_is_valid = bool(next_health_score is not None or row.uptime_percentage is not None)

                changed = (
                    row.health_score != next_health_score
                    or row.points_used != points_used
                    or row.is_valid != next_is_valid
                    or row.message != next_message
                )
                if changed:
                    row.health_score = next_health_score
                    row.points_used = points_used
                    row.is_valid = next_is_valid
                    row.message = next_message
                    updated += 1
                else:
                    unchanged += 1
            except Exception as exc:
                failed += 1
                logger.error(
                    "performance_trends_health_backfill_failed",
                    extra={
                        "device_id": row.device_id,
                        "tenant_id": row.tenant_id,
                        "bucket_start_utc": row.bucket_start_utc.isoformat(),
                        "error": str(exc),
                    },
                )
                await self._session.rollback()
                continue

            pending = updated + unchanged
            if pending > 0 and pending % max(1, batch_size) == 0:
                await self._session.commit()
                committed += pending

        await self._session.commit()

        return {
            "mode": "existing_rows_health_backfill",
            "tenant_scoped": bool(effective_tenant_id),
            "only_missing_health": only_missing_health,
            "include_current_bucket": include_current_bucket,
            "scanned": scanned,
            "updated": updated,
            "unchanged": unchanged,
            "failed": failed,
        }

    async def backfill_uptime_components(
        self,
        *,
        start_utc: datetime,
        end_utc: datetime,
        tenant_id: str | None = None,
        device_id: str | None = None,
        only_missing_uptime: bool = True,
        include_current_bucket: bool = False,
        batch_size: int = 200,
    ) -> dict[str, int | str | bool]:
        start_utc = self._to_utc(start_utc)
        end_utc = self._to_utc(end_utc)
        if end_utc <= start_utc:
            raise ValueError("end_utc must be after start_utc")

        effective_tenant_id = tenant_id or (self._ctx.tenant_id if self._ctx is not None else None)
        if device_id and effective_tenant_id is None:
            raise ValueError("tenant_id is required when backfilling a specific device")

        current_bucket_start_utc, _ = self._bucket_bounds_utc(datetime.now(timezone.utc))
        query = (
            select(DevicePerformanceTrend)
            .where(
                DevicePerformanceTrend.bucket_start_utc >= start_utc,
                DevicePerformanceTrend.bucket_start_utc < end_utc,
            )
            .order_by(
                DevicePerformanceTrend.tenant_id.asc(),
                DevicePerformanceTrend.device_id.asc(),
                DevicePerformanceTrend.bucket_start_utc.asc(),
            )
        )
        if effective_tenant_id is not None:
            query = query.where(DevicePerformanceTrend.tenant_id == effective_tenant_id)
        if device_id is not None:
            query = query.where(DevicePerformanceTrend.device_id == device_id)
        if only_missing_uptime:
            query = query.where(DevicePerformanceTrend.uptime_percentage.is_(None))
        if not include_current_bucket:
            query = query.where(DevicePerformanceTrend.bucket_start_utc < current_bucket_start_utc)

        rows = (await self._session.execute(query)).scalars().all()

        scanned = len(rows)
        updated = 0
        unchanged = 0
        failed = 0

        for index, row in enumerate(rows, start=1):
            try:
                uptime_percentage, planned, effective, break_minutes, uptime_message = await self._get_uptime_components(
                    row.device_id,
                    row.tenant_id,
                    row.bucket_start_utc,
                    row.bucket_end_utc,
                )
                health_message, _ = self._split_bucket_message(row.message)
                next_message = self._compose_bucket_message(
                    health_message=health_message,
                    uptime_message=uptime_message,
                )
                next_is_valid = bool(row.health_score is not None or uptime_percentage is not None)

                changed = (
                    row.uptime_percentage != uptime_percentage
                    or row.planned_minutes != planned
                    or row.effective_minutes != effective
                    or row.break_minutes != break_minutes
                    or row.is_valid != next_is_valid
                    or row.message != next_message
                )
                if changed:
                    row.uptime_percentage = uptime_percentage
                    row.planned_minutes = planned
                    row.effective_minutes = effective
                    row.break_minutes = break_minutes
                    row.is_valid = next_is_valid
                    row.message = next_message
                    updated += 1
                else:
                    unchanged += 1
            except Exception as exc:
                failed += 1
                logger.error(
                    "performance_trends_uptime_backfill_failed",
                    extra={
                        "device_id": row.device_id,
                        "tenant_id": row.tenant_id,
                        "bucket_start_utc": row.bucket_start_utc.isoformat(),
                        "error": str(exc),
                    },
                )
                await self._session.rollback()
                continue

            if index % max(1, batch_size) == 0:
                await self._session.commit()

        await self._session.commit()
        return {
            "mode": "existing_rows_uptime_backfill",
            "tenant_scoped": bool(effective_tenant_id),
            "only_missing_uptime": only_missing_uptime,
            "include_current_bucket": include_current_bucket,
            "scanned": scanned,
            "updated": updated,
            "unchanged": unchanged,
            "failed": failed,
        }

    async def repair_recent_health_window(
        self,
        *,
        device_id: str,
        tenant_id: str,
        rewrite_existing_health: bool,
        window: timedelta | None = None,
    ) -> dict[str, int | str | bool]:
        now_utc = datetime.now(timezone.utc)
        repair_window = window or self._RECENT_HEALTH_REPAIR_WINDOW
        return await self.backfill_health_scores(
            start_utc=now_utc - repair_window,
            end_utc=now_utc,
            tenant_id=tenant_id,
            device_id=device_id,
            only_missing_health=not rewrite_existing_health,
            include_current_bucket=False,
        )

    async def _materialize_device_bucket(
        self,
        device_id: str,
        tenant_id: str,
        bucket_start_utc: datetime,
        bucket_end_utc: datetime,
    ) -> str:
        try:
            device = await self._get_device(device_id, tenant_id)
            if device is None:
                return "failed"

            telemetry_values, points_used = await self._fetch_bucket_health_sample(
                device_id,
                tenant_id,
                bucket_start_utc,
                bucket_end_utc,
            )

            health_result = await self._health_service.calculate_health_score(
                device_id=device_id,
                telemetry_values=telemetry_values,
                # Historical buckets do not persist authoritative machine-state snapshots.
                machine_state=self._HISTORICAL_HEALTH_MACHINE_STATE,
                tenant_id=tenant_id,
            )
            uptime_percentage, planned, effective, break_minutes, uptime_message = await self._get_uptime_components(
                device_id,
                tenant_id,
                bucket_start_utc,
                bucket_end_utc,
            )

            health_score = health_result.get("health_score")
            message_parts = []
            if health_result.get("message"):
                message_parts.append(health_result["message"])
            if uptime_message:
                message_parts.append(uptime_message)

            existing = await self._session.execute(
                select(DevicePerformanceTrend).where(
                    and_(
                        DevicePerformanceTrend.device_id == device_id,
                        DevicePerformanceTrend.tenant_id == tenant_id,
                        DevicePerformanceTrend.bucket_start_utc == bucket_start_utc,
                    )
                )
            )
            row = existing.scalar_one_or_none()

            if row:
                row.bucket_end_utc = bucket_end_utc
                row.bucket_timezone = settings.PERFORMANCE_TRENDS_TIMEZONE
                row.interval_minutes = settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES
                row.health_score = health_score
                row.uptime_percentage = uptime_percentage
                row.planned_minutes = planned
                row.effective_minutes = effective
                row.break_minutes = break_minutes
                row.points_used = points_used
                row.is_valid = bool(health_score is not None or uptime_percentage is not None)
                row.message = " | ".join(message_parts) if message_parts else None
                return "updated"

            self._session.add(
                DevicePerformanceTrend(
                    device_id=device_id,
                    tenant_id=tenant_id,
                    bucket_start_utc=bucket_start_utc,
                    bucket_end_utc=bucket_end_utc,
                    bucket_timezone=settings.PERFORMANCE_TRENDS_TIMEZONE,
                    interval_minutes=settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES,
                    health_score=health_score,
                    uptime_percentage=uptime_percentage,
                    planned_minutes=planned,
                    effective_minutes=effective,
                    break_minutes=break_minutes,
                    points_used=points_used,
                    is_valid=bool(health_score is not None or uptime_percentage is not None),
                    message=" | ".join(message_parts) if message_parts else None,
                )
            )
            return "created"
        except Exception as exc:
            logger.error(
                "Failed to materialize performance trend bucket",
                extra={"device_id": device_id, "error": str(exc)},
            )
            return "failed"

    async def get_trends(
        self,
        device_id: str,
        tenant_id: str,
        metric: str,
        range_key: str,
    ) -> dict[str, Any]:
        range_delta = RANGE_TO_DELTA.get(range_key, RANGE_TO_DELTA["24h"])
        now_utc = datetime.now(timezone.utc)
        start_utc = now_utc - range_delta

        device = await self._get_device(device_id, tenant_id)
        if device is None:
            return {
                "device_id": device_id,
                "metric": metric,
                "range": range_key,
                "interval_minutes": settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES,
                "timezone": settings.PERFORMANCE_TRENDS_TIMEZONE,
                "points": [],
                "total_points": 0,
                "sampled_points": 0,
                "message": "Device not found for tenant.",
            }

        rows = (
            await self._session.execute(
                select(DevicePerformanceTrend)
                .where(
                    and_(
                        DevicePerformanceTrend.device_id == device_id,
                        DevicePerformanceTrend.tenant_id == tenant_id,
                        DevicePerformanceTrend.bucket_start_utc >= start_utc,
                    )
                )
                .order_by(DevicePerformanceTrend.bucket_start_utc.asc())
            )
        ).scalars().all()

        repaired_recent_rows = await self._repair_recent_missing_metric_rows_on_read(
            device_id=device_id,
            tenant_id=tenant_id,
            metric=metric,
            rows=rows,
            now_utc=now_utc,
        )
        if repaired_recent_rows:
            rows = (
                await self._session.execute(
                    select(DevicePerformanceTrend)
                    .where(
                        and_(
                            DevicePerformanceTrend.device_id == device_id,
                            DevicePerformanceTrend.tenant_id == tenant_id,
                            DevicePerformanceTrend.bucket_start_utc >= start_utc,
                        )
                    )
                    .order_by(DevicePerformanceTrend.bucket_start_utc.asc())
                )
            ).scalars().all()

        metric_key = self._metric_column(metric)
        metric_rows = [row for row in rows if getattr(row, metric_key) is not None]
        total_points = len(metric_rows)
        max_points = max(50, settings.PERFORMANCE_TRENDS_MAX_POINTS)
        stride = max(1, total_points // max_points) if total_points > max_points else 1
        sampled_rows = metric_rows[::stride]

        points = []
        for row in sampled_rows:
            ts_local = row.bucket_start_utc.astimezone(self._tz)
            points.append(
                {
                    "timestamp": ts_local.isoformat(),
                    "health_score": row.health_score,
                    "uptime_percentage": row.uptime_percentage,
                    "planned_minutes": row.planned_minutes,
                    "effective_minutes": row.effective_minutes,
                    "break_minutes": row.break_minutes,
                }
            )

        fallback_row = None
        if not points:
            fallback_row = await self._get_metric_fallback_point(
                device_id=device_id,
                tenant_id=tenant_id,
                metric=metric,
                start_utc=start_utc,
                range_delta=range_delta,
            )

        if sampled_rows:
            metric_message = self._metric_message_from_bucket(metric, sampled_rows[-1].message) or (sampled_rows[-1].message or "")
            last_actual_timestamp = sampled_rows[-1].bucket_start_utc.astimezone(self._tz).isoformat()
            is_stale = False
            fallback_point = None
        elif fallback_row is not None:
            metric_message = self._metric_stale_message(metric, fallback_row.bucket_start_utc)
            last_actual_timestamp = fallback_row.bucket_start_utc.astimezone(self._tz).isoformat()
            metric_value = getattr(fallback_row, metric_key)
            fallback_point = {
                "timestamp": last_actual_timestamp,
                "value": float(metric_value),
            }
            is_stale = True
        else:
            fallback_point = None
            last_actual_timestamp = None
            recent_metric_messages = [
                self._metric_message_from_bucket(metric, row.message)
                for row in reversed(rows)
                if self._metric_message_from_bucket(metric, row.message)
            ]
            metric_message = recent_metric_messages[0] if recent_metric_messages else self._metric_empty_message(metric)
            is_stale = False

        return {
            "device_id": device_id,
            "metric": metric,
            "range": range_key,
            "interval_minutes": settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES,
            "timezone": settings.PERFORMANCE_TRENDS_TIMEZONE,
            "points": points,
            "total_points": total_points,
            "sampled_points": len(points),
            "message": metric_message,
            "metric_message": metric_message,
            "range_start": start_utc.astimezone(self._tz).isoformat(),
            "range_end": now_utc.astimezone(self._tz).isoformat(),
            "is_stale": is_stale,
            "last_actual_timestamp": last_actual_timestamp,
            "fallback_point": fallback_point,
        }
