"""Durable interval logging service for device state transitions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import DeviceStateInterval, DeviceStateIntervalType
from app.repositories.device_state_intervals import DeviceStateIntervalRepository


class DeviceStateIntervalService:
    """Idempotent open/close helpers for interval rows."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = DeviceStateIntervalRepository(session)

    @staticmethod
    def _coerce_state_type(state_type: str | DeviceStateIntervalType) -> str:
        return state_type.value if isinstance(state_type, DeviceStateIntervalType) else str(state_type)

    async def open_interval(
        self,
        *,
        tenant_id: str,
        device_id: str,
        state_type: str | DeviceStateIntervalType,
        started_at: datetime,
        sample_ts: Optional[datetime] = None,
        opened_reason: Optional[str] = None,
        source: Optional[str] = None,
    ) -> DeviceStateInterval:
        normalized_state_type = self._coerce_state_type(state_type)
        normalized_started_at = self._repo.normalize_timestamp(started_at)
        normalized_sample_ts = self._repo.normalize_timestamp(sample_ts) or normalized_started_at
        assert normalized_started_at is not None

        open_rows = await self._repo.find_open_intervals(
            tenant_id=tenant_id,
            device_id=device_id,
            state_type=normalized_state_type,
            for_update=True,
        )
        if open_rows:
            primary = open_rows[0]
            if len(open_rows) > 1:
                await self._close_rows(
                    open_rows[1:],
                    ended_at=normalized_sample_ts,
                    sample_ts=normalized_sample_ts,
                    closed_reason="duplicate_open_reconciled",
                    source=source,
                )
            return primary

        interval = DeviceStateInterval(
            tenant_id=tenant_id,
            device_id=device_id,
            state_type=normalized_state_type,
            started_at=normalized_started_at,
            ended_at=None,
            duration_sec=None,
            is_open=True,
            opened_by_sample_ts=normalized_sample_ts,
            opened_reason=opened_reason,
            source=source,
        )
        return await self._repo.add(interval)

    async def close_interval(
        self,
        *,
        tenant_id: str,
        device_id: str,
        state_type: str | DeviceStateIntervalType,
        ended_at: datetime,
        sample_ts: Optional[datetime] = None,
        closed_reason: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Optional[DeviceStateInterval]:
        normalized_state_type = self._coerce_state_type(state_type)
        normalized_ended_at = self._repo.normalize_timestamp(ended_at)
        normalized_sample_ts = self._repo.normalize_timestamp(sample_ts) or normalized_ended_at
        assert normalized_ended_at is not None

        open_rows = await self._repo.find_open_intervals(
            tenant_id=tenant_id,
            device_id=device_id,
            state_type=normalized_state_type,
            for_update=True,
        )
        if not open_rows:
            return None

        await self._close_rows(
            open_rows,
            ended_at=normalized_ended_at,
            sample_ts=normalized_sample_ts,
            closed_reason=closed_reason,
            source=source,
        )
        return open_rows[0]

    async def sync_interval_state(
        self,
        *,
        tenant_id: str,
        device_id: str,
        state_type: str | DeviceStateIntervalType,
        is_active: bool,
        event_ts: datetime,
        sample_ts: Optional[datetime] = None,
        opened_reason: Optional[str] = None,
        closed_reason: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Optional[DeviceStateInterval]:
        if is_active:
            return await self.open_interval(
                tenant_id=tenant_id,
                device_id=device_id,
                state_type=state_type,
                started_at=event_ts,
                sample_ts=sample_ts,
                opened_reason=opened_reason,
                source=source,
            )
        return await self.close_interval(
            tenant_id=tenant_id,
            device_id=device_id,
            state_type=state_type,
            ended_at=event_ts,
            sample_ts=sample_ts,
            closed_reason=closed_reason,
            source=source,
        )

    async def reconcile_timeout_closure(
        self,
        *,
        tenant_id: str,
        device_id: str,
        ended_at: datetime,
        sample_ts: Optional[datetime] = None,
        closed_reason: str = "telemetry_timeout",
        source: str = "timeout_reconciler",
        state_types: Optional[list[str | DeviceStateIntervalType]] = None,
    ) -> list[DeviceStateInterval]:
        target_state_types = state_types or [
            DeviceStateIntervalType.RUNTIME_ON,
            DeviceStateIntervalType.IDLE,
            DeviceStateIntervalType.OVERCONSUMPTION,
        ]
        normalized_ended_at = self._repo.normalize_timestamp(ended_at)
        normalized_sample_ts = self._repo.normalize_timestamp(sample_ts)
        assert normalized_ended_at is not None

        closed_rows: list[DeviceStateInterval] = []
        for state_type in target_state_types:
            row = await self.close_interval(
                tenant_id=tenant_id,
                device_id=device_id,
                state_type=state_type,
                ended_at=normalized_ended_at,
                sample_ts=normalized_sample_ts,
                closed_reason=closed_reason,
                source=source,
            )
            if row is not None:
                closed_rows.append(row)
        return closed_rows

    async def cleanup_closed_intervals_for_tenant(
        self,
        *,
        tenant_id: str,
        retention_days: int,
        batch_size: int,
        max_batches: int,
        now_utc: Optional[datetime] = None,
    ) -> dict[str, int | str]:
        normalized_retention_days = max(int(retention_days), 1)
        normalized_batch_size = max(int(batch_size), 1)
        normalized_max_batches = max(int(max_batches), 1)
        reference_now = now_utc or datetime.now(timezone.utc)
        cutoff_ts = reference_now - timedelta(days=normalized_retention_days)

        deleted_total = 0
        batches = 0
        for _ in range(normalized_max_batches):
            deleted = await self._repo.delete_closed_intervals_older_than(
                tenant_id=tenant_id,
                cutoff_ts=cutoff_ts,
                batch_size=normalized_batch_size,
            )
            batches += 1
            deleted_total += int(deleted)
            if deleted < normalized_batch_size:
                break

        return {
            "tenant_id": tenant_id,
            "cutoff_ts": cutoff_ts.isoformat(),
            "deleted": deleted_total,
            "batches": batches,
        }

    async def collect_open_interval_observability(
        self,
        *,
        tenant_id: str,
        stale_open_alert_days: int,
        now_utc: Optional[datetime] = None,
    ) -> dict[str, int | dict[str, int]]:
        reference_now = now_utc or datetime.now(timezone.utc)
        stale_days = max(int(stale_open_alert_days), 1)
        stale_threshold = reference_now - timedelta(days=stale_days)

        open_counts = await self._repo.count_open_intervals_by_state(tenant_id=tenant_id)
        stale_open_count = await self._repo.count_stale_open_intervals(
            tenant_id=tenant_id,
            older_than_ts=stale_threshold,
        )
        return {
            "open_counts_by_state": open_counts,
            "open_total": int(sum(open_counts.values())),
            "stale_open_count": int(stale_open_count),
        }

    async def _close_rows(
        self,
        rows: list[DeviceStateInterval],
        *,
        ended_at: datetime,
        sample_ts: Optional[datetime],
        closed_reason: Optional[str],
        source: Optional[str],
    ) -> None:
        normalized_ended_at = self._repo.normalize_timestamp(ended_at)
        normalized_sample_ts = self._repo.normalize_timestamp(sample_ts)
        assert normalized_ended_at is not None

        for row in rows:
            close_at = normalized_ended_at if normalized_ended_at >= row.started_at else row.started_at
            row.ended_at = close_at
            row.duration_sec = max(int((close_at - row.started_at).total_seconds()), 0)
            row.is_open = False
            row.closed_by_sample_ts = normalized_sample_ts
            row.closed_reason = closed_reason
            if source is not None:
                row.source = source
        await self._session.flush()
