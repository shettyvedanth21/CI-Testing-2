"""Repository helpers for durable device state interval logging."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import DeviceStateInterval, DeviceStateIntervalType, _normalize_aware_utc


class DeviceStateIntervalRepository:
    """Data access layer for interval open/close lifecycle operations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def normalize_timestamp(value: Optional[datetime]) -> Optional[datetime]:
        return _normalize_aware_utc(value)

    @staticmethod
    def _normalize_state_type(state_type: str | DeviceStateIntervalType) -> str:
        return state_type.value if isinstance(state_type, DeviceStateIntervalType) else str(state_type)

    async def find_open_intervals(
        self,
        *,
        tenant_id: str,
        device_id: str,
        state_type: str | DeviceStateIntervalType,
        for_update: bool = False,
    ) -> list[DeviceStateInterval]:
        query = (
            select(DeviceStateInterval)
            .where(
                DeviceStateInterval.tenant_id == tenant_id,
                DeviceStateInterval.device_id == device_id,
                DeviceStateInterval.state_type == self._normalize_state_type(state_type),
                DeviceStateInterval.is_open.is_(True),
            )
            .order_by(DeviceStateInterval.started_at.asc(), DeviceStateInterval.id.asc())
        )
        if for_update:
            query = query.with_for_update()
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def find_current_open_interval(
        self,
        *,
        tenant_id: str,
        device_id: str,
        state_type: str | DeviceStateIntervalType,
        for_update: bool = False,
    ) -> Optional[DeviceStateInterval]:
        rows = await self.find_open_intervals(
            tenant_id=tenant_id,
            device_id=device_id,
            state_type=state_type,
            for_update=for_update,
        )
        return rows[0] if rows else None

    async def add(self, interval: DeviceStateInterval) -> DeviceStateInterval:
        self._session.add(interval)
        await self._session.flush()
        return interval

    async def list_open_intervals(
        self,
        *,
        tenant_id: str | None = None,
        device_id: str | None = None,
        state_types: list[str | DeviceStateIntervalType] | None = None,
        for_update: bool = False,
    ) -> list[DeviceStateInterval]:
        query = select(DeviceStateInterval).where(DeviceStateInterval.is_open.is_(True))
        if tenant_id is not None:
            query = query.where(DeviceStateInterval.tenant_id == tenant_id)
        if device_id is not None:
            query = query.where(DeviceStateInterval.device_id == device_id)
        if state_types:
            normalized_types = [self._normalize_state_type(value) for value in state_types]
            query = query.where(DeviceStateInterval.state_type.in_(normalized_types))
        query = query.order_by(
            DeviceStateInterval.tenant_id.asc(),
            DeviceStateInterval.device_id.asc(),
            DeviceStateInterval.state_type.asc(),
            DeviceStateInterval.started_at.asc(),
            DeviceStateInterval.id.asc(),
        )
        if for_update:
            query = query.with_for_update()
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def list_device_intervals(
        self,
        *,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        state_type: str | DeviceStateIntervalType | None = None,
        is_open: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DeviceStateInterval], int]:
        normalized_start = self.normalize_timestamp(start_time)
        normalized_end = self.normalize_timestamp(end_time)
        normalized_state_type = self._normalize_state_type(state_type) if state_type is not None else None
        normalized_limit = max(1, min(int(limit), 1000))
        normalized_offset = max(0, int(offset))

        filters = [
            DeviceStateInterval.tenant_id == tenant_id,
            DeviceStateInterval.device_id == device_id,
        ]
        if normalized_state_type is not None:
            filters.append(DeviceStateInterval.state_type == normalized_state_type)
        if is_open is not None:
            filters.append(DeviceStateInterval.is_open.is_(bool(is_open)))
        if normalized_start is not None:
            # Keep intervals that are still open at range start, or that ended after range start.
            filters.append(
                or_(
                    DeviceStateInterval.ended_at.is_(None),
                    DeviceStateInterval.ended_at >= normalized_start,
                )
            )
        if normalized_end is not None:
            filters.append(DeviceStateInterval.started_at <= normalized_end)

        where_clause = and_(*filters)
        count_query = select(func.count(DeviceStateInterval.id)).where(where_clause)
        query = (
            select(DeviceStateInterval)
            .where(where_clause)
            .order_by(DeviceStateInterval.started_at.desc(), DeviceStateInterval.id.desc())
            .limit(normalized_limit)
            .offset(normalized_offset)
        )

        count_result = await self._session.execute(count_query)
        total = int(count_result.scalar() or 0)
        result = await self._session.execute(query)
        rows = list(result.scalars().all())
        return rows, total

    async def delete_closed_intervals_older_than(
        self,
        *,
        tenant_id: str,
        cutoff_ts: datetime,
        batch_size: int,
    ) -> int:
        normalized_cutoff = self.normalize_timestamp(cutoff_ts)
        assert normalized_cutoff is not None
        normalized_batch_size = max(1, int(batch_size))

        id_query = (
            select(DeviceStateInterval.id)
            .where(
                DeviceStateInterval.tenant_id == tenant_id,
                DeviceStateInterval.is_open.is_(False),
                DeviceStateInterval.ended_at.is_not(None),
                DeviceStateInterval.ended_at < normalized_cutoff,
            )
            .order_by(DeviceStateInterval.ended_at.asc(), DeviceStateInterval.id.asc())
            .limit(normalized_batch_size)
        )
        id_rows = (await self._session.execute(id_query)).scalars().all()
        ids = [int(value) for value in id_rows]
        if not ids:
            return 0

        result = await self._session.execute(
            delete(DeviceStateInterval).where(
                DeviceStateInterval.tenant_id == tenant_id,
                DeviceStateInterval.id.in_(ids),
            )
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def count_open_intervals_by_state(
        self,
        *,
        tenant_id: str,
    ) -> dict[str, int]:
        query = (
            select(DeviceStateInterval.state_type, func.count(DeviceStateInterval.id))
            .where(
                DeviceStateInterval.tenant_id == tenant_id,
                DeviceStateInterval.is_open.is_(True),
            )
            .group_by(DeviceStateInterval.state_type)
        )
        rows = (await self._session.execute(query)).all()
        counts: dict[str, int] = {}
        for state_type, count in rows:
            counts[str(state_type)] = int(count or 0)
        return counts

    async def count_stale_open_intervals(
        self,
        *,
        tenant_id: str,
        older_than_ts: datetime,
    ) -> int:
        normalized_threshold = self.normalize_timestamp(older_than_ts)
        assert normalized_threshold is not None
        result = await self._session.execute(
            select(func.count(DeviceStateInterval.id)).where(
                DeviceStateInterval.tenant_id == tenant_id,
                DeviceStateInterval.is_open.is_(True),
                DeviceStateInterval.started_at < normalized_threshold,
            )
        )
        return int(result.scalar() or 0)
