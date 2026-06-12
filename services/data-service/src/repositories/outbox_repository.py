"""Async repository for telemetry outbox and reconciliation state."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.models import Base, OutboxMessage, OutboxStatus, OutboxTarget, ReconciliationLog
from src.utils import get_logger

logger = get_logger(__name__)

_ENGINE = None
_SESSION_FACTORY: async_sessionmaker[AsyncSession] | None = None


def _utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def get_async_engine() -> AsyncEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_async_engine(
            settings.mysql_async_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=settings.db_pool_recycle,
            pool_timeout=settings.db_pool_timeout,
            future=True,
        )
    return _ENGINE


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _ENGINE, _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _ENGINE = get_async_engine()
        _SESSION_FACTORY = async_sessionmaker(
            _ENGINE,
            expire_on_commit=False,
            autoflush=False,
        )
    return _SESSION_FACTORY


class OutboxRepository:
    """Repository for durable telemetry outbox operations."""

    _SCHEMA_LOCK_NAME = "data_service_outbox_schema"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self.session_factory = session_factory or get_session_factory()
        self.engine = get_async_engine()

    async def ensure_schema(self) -> None:
        async with self.engine.begin() as conn:
            try:
                await conn.execute(
                    text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
                    {"lock_name": self._SCHEMA_LOCK_NAME, "timeout_seconds": 30},
                )
                await conn.run_sync(
                    Base.metadata.create_all,
                    tables=[OutboxMessage.__table__, ReconciliationLog.__table__],
                )
            except OperationalError as exc:
                message = str(getattr(exc, "orig", exc)).lower()
                if "already exists" not in message:
                    raise
                logger.warning("Outbox schema creation raced with another startup instance; continuing.")
            finally:
                try:
                    await conn.execute(
                        text("SELECT RELEASE_LOCK(:lock_name)"),
                        {"lock_name": self._SCHEMA_LOCK_NAME},
                    )
                except Exception:
                    logger.warning("Failed to release outbox schema advisory lock", exc_info=True)

    async def enqueue_telemetry(
        self,
        *,
        device_id: str,
        telemetry_payload: dict[str, Any],
        targets: Iterable[OutboxTarget],
        max_retries: int | None = None,
        session: AsyncSession | None = None,
    ) -> list[OutboxMessage]:
        target_list = list(targets)
        if not target_list:
            return []
        own_session = session is None
        active_session = session or self.session_factory()
        try:
            if own_session:
                await active_session.begin()
            rows = [
                OutboxMessage(
                    device_id=device_id,
                    telemetry_json=telemetry_payload,
                    target=target,
                    status=OutboxStatus.PENDING,
                    retry_count=0,
                    max_retries=max_retries or settings.outbox_max_retries,
                )
                for target in target_list
            ]
            active_session.add_all(rows)
            await active_session.flush()
            if own_session:
                await active_session.commit()
            return rows
        except Exception:
            if own_session:
                await active_session.rollback()
            raise
        finally:
            if own_session:
                await active_session.close()

    async def enqueue_telemetry_batch(
        self,
        *,
        entries: Iterable[tuple[str, dict[str, Any], Iterable[OutboxTarget]]],
        max_retries: int | None = None,
        session: AsyncSession | None = None,
    ) -> list[OutboxMessage]:
        prepared: list[tuple[str, dict[str, Any], list[OutboxTarget]]] = []
        for device_id, telemetry_payload, targets in entries:
            target_list = list(targets)
            if target_list:
                prepared.append((device_id, telemetry_payload, target_list))
        if not prepared:
            return []
        own_session = session is None
        active_session = session or self.session_factory()
        try:
            if own_session:
                await active_session.begin()
            rows: list[OutboxMessage] = []
            mappings: list[dict[str, Any]] = []
            effective_max_retries = max_retries or settings.outbox_max_retries
            for device_id, telemetry_payload, targets in prepared:
                for target in targets:
                    mappings.append(
                        {
                            "device_id": device_id,
                            "telemetry_json": telemetry_payload,
                            "target": target.value if isinstance(target, OutboxTarget) else str(target),
                            "status": OutboxStatus.PENDING.value,
                            "retry_count": 0,
                            "max_retries": effective_max_retries,
                        }
                    )
            if mappings:
                dialect_name = str(getattr(getattr(getattr(active_session, "bind", None), "dialect", None), "name", "") or "")
                use_fast_insert = (
                    dialect_name == "mysql"
                    or not hasattr(active_session, "scalar")
                    or not hasattr(active_session, "add_all")
                )
                if use_fast_insert:
                    # Keep the low-overhead bulk path for the production MySQL backend.
                    await active_session.execute(OutboxMessage.__table__.insert(), mappings)
                else:
                    # SQLite-backed local validation does not synthesize BIGINT PK autoincrement
                    # the same way MySQL does, so assign surrogate ids explicitly.
                    current_max = await active_session.scalar(select(func.max(OutboxMessage.id)))
                    next_id = int(current_max or 0) + 1
                    rows = [
                        OutboxMessage(
                            id=next_id + index,
                            device_id=str(mapping["device_id"]),
                            telemetry_json=dict(mapping["telemetry_json"]),
                            target=OutboxTarget(str(mapping["target"])),
                            status=OutboxStatus(str(mapping["status"])),
                            retry_count=int(mapping["retry_count"]),
                            max_retries=int(mapping["max_retries"]),
                        )
                        for index, mapping in enumerate(mappings)
                    ]
                    active_session.add_all(rows)
                    await active_session.flush()
            if own_session:
                await active_session.commit()
            return rows
        except Exception:
            if own_session:
                await active_session.rollback()
            raise
        finally:
            if own_session:
                await active_session.close()

    async def claim_pending_batch(
        self,
        *,
        session: AsyncSession,
        batch_size: int,
        backoff_base_seconds: int,
    ) -> list[OutboxMessage]:
        stmt = (
            select(OutboxMessage)
            .where(
                text(
                    """
                    (
                        status = 'pending'
                        OR (
                            status = 'failed'
                            AND (
                                last_attempted_at IS NULL
                                OR TIMESTAMPDIFF(
                                    SECOND,
                                    last_attempted_at,
                                    UTC_TIMESTAMP()
                                ) >= GREATEST(1, POW(2, GREATEST(retry_count - 1, 0)) * :backoff_base_seconds)
                            )
                        )
                    )
                    """
                )
            )
            .order_by(OutboxMessage.created_at.asc(), OutboxMessage.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .params(backoff_base_seconds=max(1, backoff_base_seconds))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def mark_delivered(
        self,
        *,
        session: AsyncSession,
        message: OutboxMessage,
        delivered_at: datetime | None = None,
        flush: bool = True,
    ) -> None:
        message.status = OutboxStatus.DELIVERED
        message.delivered_at = _utc_naive(delivered_at or datetime.now(timezone.utc))
        message.last_attempted_at = message.delivered_at
        message.error_message = None
        if flush:
            await session.flush()

    async def mark_delivered_many(
        self,
        *,
        session: AsyncSession,
        message_ids: list[int],
        delivered_at: datetime | None = None,
        flush: bool = True,
    ) -> None:
        if not message_ids:
            return
        attempted = _utc_naive(delivered_at or datetime.now(timezone.utc))
        await session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id.in_(message_ids))
            .values(
                status=OutboxStatus.DELIVERED,
                delivered_at=attempted,
                last_attempted_at=attempted,
                error_message=None,
            )
        )
        if flush:
            await session.flush()

    async def mark_retryable_failure(
        self,
        *,
        session: AsyncSession,
        message: OutboxMessage,
        error_message: str,
        attempted_at: datetime | None = None,
        flush: bool = True,
    ) -> None:
        message.retry_count = int(message.retry_count or 0) + 1
        message.status = OutboxStatus.FAILED
        message.last_attempted_at = _utc_naive(attempted_at or datetime.now(timezone.utc))
        message.error_message = error_message[:4096]
        if flush:
            await session.flush()

    async def mark_retryable_failure_many(
        self,
        *,
        session: AsyncSession,
        message_ids: list[int],
        error_message: str,
        attempted_at: datetime | None = None,
        flush: bool = True,
    ) -> None:
        if not message_ids:
            return
        attempted = _utc_naive(attempted_at or datetime.now(timezone.utc))
        await session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id.in_(message_ids))
            .values(
                status=OutboxStatus.FAILED,
                retry_count=OutboxMessage.retry_count + 1,
                last_attempted_at=attempted,
                error_message=error_message[:4096],
            )
        )
        if flush:
            await session.flush()

    async def mark_dead(
        self,
        *,
        session: AsyncSession,
        message: OutboxMessage,
        error_message: str,
        attempted_at: datetime | None = None,
        flush: bool = True,
    ) -> None:
        message.retry_count = int(message.retry_count or 0) + 1
        message.status = OutboxStatus.DEAD
        message.last_attempted_at = _utc_naive(attempted_at or datetime.now(timezone.utc))
        message.error_message = error_message[:4096]
        if flush:
            await session.flush()

    async def mark_dead_without_retry_increment(
        self,
        *,
        session: AsyncSession,
        message: OutboxMessage,
        error_message: str,
        attempted_at: datetime | None = None,
        flush: bool = True,
    ) -> None:
        message.status = OutboxStatus.DEAD
        message.last_attempted_at = _utc_naive(attempted_at or datetime.now(timezone.utc))
        message.error_message = error_message[:4096]
        if flush:
            await session.flush()

    async def insert_reconciliation_log(
        self,
        *,
        device_id: str,
        checked_at: datetime,
        influx_ts: datetime | None,
        mysql_ts: datetime | None,
        drift_seconds: int | None,
        action_taken: str,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    ReconciliationLog(
                        device_id=device_id,
                        checked_at=_utc_naive(checked_at) or datetime.utcnow(),
                        influx_ts=_utc_naive(influx_ts),
                        mysql_ts=_utc_naive(mysql_ts),
                        drift_seconds=drift_seconds,
                        action_taken=action_taken[:255],
                    )
                )

    async def get_message(self, message_id: int) -> OutboxMessage | None:
        async with self.session_factory() as session:
            return await session.get(OutboxMessage, message_id)

    async def list_messages(self, *, status: OutboxStatus | None = None) -> list[OutboxMessage]:
        async with self.session_factory() as session:
            stmt = select(OutboxMessage).order_by(OutboxMessage.id.asc())
            if status is not None:
                stmt = stmt.where(OutboxMessage.status == status)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def count_messages(self, *, status: OutboxStatus | None = None) -> int:
        return len(await self.list_messages(status=status))

    async def get_status_counts(self) -> dict[str, int]:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM telemetry_outbox
                    GROUP BY status
                    """
                )
            )
            counts = {str(row[0]): int(row[1]) for row in result.all()}
        return {
            "pending": counts.get(OutboxStatus.PENDING.value, 0),
            "failed": counts.get(OutboxStatus.FAILED.value, 0),
            "delivered": counts.get(OutboxStatus.DELIVERED.value, 0),
            "dead": counts.get(OutboxStatus.DEAD.value, 0),
        }

    async def purge_retained_rows(
        self,
        *,
        delivered_before: datetime,
        dead_before: datetime,
        reconciliation_before: datetime,
        batch_size: int,
    ) -> dict[str, int]:
        """Delete terminal rows past retention without touching retryable work."""
        limit = max(1, int(batch_size))
        delivered_cutoff = _utc_naive(delivered_before)
        dead_cutoff = _utc_naive(dead_before)
        reconciliation_cutoff = _utc_naive(reconciliation_before)
        if delivered_cutoff is None or dead_cutoff is None or reconciliation_cutoff is None:
            raise ValueError("Retention cutoffs are required")

        async with self.session_factory() as session:
            async with session.begin():
                delivered = await session.execute(
                    text(
                        """
                        DELETE FROM telemetry_outbox
                        WHERE status = 'delivered'
                          AND COALESCE(delivered_at, last_attempted_at, created_at) < :cutoff
                        LIMIT :limit
                        """
                    ),
                    {"cutoff": delivered_cutoff, "limit": limit},
                )
                dead = await session.execute(
                    text(
                        """
                        DELETE FROM telemetry_outbox
                        WHERE status = 'dead'
                          AND COALESCE(last_attempted_at, created_at) < :cutoff
                        LIMIT :limit
                        """
                    ),
                    {"cutoff": dead_cutoff, "limit": limit},
                )
                reconciliation = await session.execute(
                    text(
                        """
                        DELETE FROM reconciliation_log
                        WHERE checked_at < :cutoff
                        LIMIT :limit
                        """
                    ),
                    {"cutoff": reconciliation_cutoff, "limit": limit},
                )

        return {
            "telemetry_outbox_delivered": int(delivered.rowcount or 0),
            "telemetry_outbox_dead": int(dead.rowcount or 0),
            "reconciliation_log": int(reconciliation.rowcount or 0),
        }

    async def reset_tables(self) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM reconciliation_log"))
                await session.execute(text("DELETE FROM telemetry_outbox"))

    async def close(self) -> None:
        await self.engine.dispose()
