"""Background retention cleanup for durable telemetry operational tables."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from src.config import settings
from src.repositories import DLQRepository, OutboxRepository
from src.utils import get_logger

logger = get_logger(__name__)


class RetentionCleanupService:
    """Periodically enforces bounded retention for append-heavy operational stores."""

    def __init__(
        self,
        *,
        outbox_repository: OutboxRepository,
        dlq_repository: DLQRepository,
        interval_seconds: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.outbox_repository = outbox_repository
        self.dlq_repository = dlq_repository
        self.interval_seconds = max(60, int(interval_seconds or settings.retention_cleanup_interval_sec))
        self.batch_size = max(1, int(batch_size or settings.retention_cleanup_batch_size))
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        await self.run_once()
        self._task = asyncio.create_task(self._run_loop(), name="retention-cleanup-service")
        logger.info(
            "retention_cleanup_started",
            interval_seconds=self.interval_seconds,
            batch_size=self.batch_size,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("retention_cleanup_stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
                break
            except asyncio.TimeoutError:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("retention_cleanup_loop_failed", error=str(exc))

    async def run_once(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        dlq_reclassified = await asyncio.to_thread(
            self.dlq_repository.reclassify_non_retryable_pending,
            batch_size=self.batch_size,
        )
        outbox_counts = await self.outbox_repository.purge_retained_rows(
            delivered_before=now - timedelta(days=max(1, settings.outbox_delivered_retention_days)),
            dead_before=now - timedelta(days=max(1, settings.outbox_dead_retention_days)),
            reconciliation_before=now - timedelta(days=max(1, settings.reconciliation_log_retention_days)),
            batch_size=self.batch_size,
        )
        dlq_cutoff = now - timedelta(days=max(1, settings.dlq_retention_days))
        dlq_deleted = await asyncio.to_thread(
            self.dlq_repository.purge_expired,
            created_before=dlq_cutoff,
            batch_size=self.batch_size,
        )
        counts = {
            **outbox_counts,
            "dlq_reclassified_non_retryable": int(dlq_reclassified),
            "dlq_messages": int(dlq_deleted),
        }
        if any(counts.values()):
            logger.info("retention_cleanup_completed", **counts)
        return counts
