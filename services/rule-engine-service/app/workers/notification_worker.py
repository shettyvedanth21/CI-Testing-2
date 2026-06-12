from __future__ import annotations

import asyncio
import logging
import socket
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.database import WorkerSessionLocal
from app.models.rule import NotificationDeliveryStatus
from app.queue import NotificationQueueItem, get_notification_queue
from app.repositories.notification_outbox import NotificationOutboxRepository
from app.services.notification_executor import NotificationExecutor
from app.services.notification_delivery import NotificationDeliveryAuditService
from services.shared.tenant_context import TenantContext


logger = logging.getLogger(__name__)

_REDIS_COUNTER_KEYS = {
    "retry": "counters:rule_engine_retry_events",
    "dead_letter": "counters:rule_engine_dead_letter_events",
}

_HEARTBEAT_ZSET = "notification_worker_heartbeats"
_HEARTBEAT_TTL_SECONDS = 90


async def recover_stale_attempted_on_startup() -> int:
    stale_after = timedelta(seconds=max(settings.NOTIFICATION_DELIVERY_TIMEOUT_SECONDS + 15, 30))
    now = datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    async with WorkerSessionLocal() as session:
        repo = NotificationOutboxRepository(session)
        recovered = await repo.recover_stale_attempted(stale_after=stale_after, now=now)
        if recovered > 0:
            logger.info(
                "notification_startup_recovered_stale_attempted",
                extra={"recovered_count": recovered},
            )
        return recovered


class NotificationWorker:
    def __init__(self, concurrency: int | None = None) -> None:
        self._queue = get_notification_queue()
        self._concurrency = max(1, concurrency or settings.NOTIFICATION_WORKER_CONCURRENCY)
        self._worker_id = f"{settings.NOTIFICATION_OUTBOX_CONSUMER_NAME}-{socket.gethostname()}"
        self._tasks: list[asyncio.Task] = []
        self._requeue_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._stopping = False
        self._metrics_redis = None

    async def _init_metrics_redis(self) -> None:
        if self._metrics_redis is not None or not settings.REDIS_URL:
            return
        try:
            from redis.asyncio import Redis as AIORedis
            self._metrics_redis = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception:
            logger.debug("notification_worker_metrics_redis_unavailable")

    async def _incr_event(self, event: str) -> None:
        key = _REDIS_COUNTER_KEYS.get(event)
        if key is None or self._metrics_redis is None:
            return
        try:
            await self._metrics_redis.incr(key)
        except Exception:
            pass

    async def _heartbeat_loop(self) -> None:
        redis = self._metrics_redis
        if redis is None:
            try:
                from redis.asyncio import Redis as AIORedis
                redis = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
            except Exception:
                logger.warning("notification_worker_heartbeat_redis_unavailable")
                return
        try:
            while not self._stopping:
                try:
                    now = time.time()
                    await redis.zadd(_HEARTBEAT_ZSET, {self._worker_id: now})
                    await redis.zremrangebyscore(_HEARTBEAT_ZSET, "-inf", now - _HEARTBEAT_TTL_SECONDS)
                    await redis.expire(_HEARTBEAT_ZSET, _HEARTBEAT_TTL_SECONDS + 30)
                except Exception:
                    logger.debug("notification_worker_heartbeat_write_failed")
                await asyncio.sleep(30)
        finally:
            if redis is not self._metrics_redis:
                try:
                    await redis.close()
                except Exception:
                    pass

    async def start(self) -> None:
        await self._init_metrics_redis()
        self._requeue_task = asyncio.create_task(self._requeue_due_loop())
        self._tasks = [asyncio.create_task(self._worker_loop(slot)) for slot in range(self._concurrency)]
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await asyncio.gather(*self._tasks)

    async def stop(self) -> None:
        self._stopping = True
        if self._requeue_task is not None:
            self._requeue_task.cancel()
            await asyncio.gather(self._requeue_task, return_exceptions=True)
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._metrics_redis is not None:
            try:
                await self._metrics_redis.close()
            except Exception:
                pass
            self._metrics_redis = None

    async def _requeue_due_loop(self) -> None:
        while not self._stopping:
            try:
                async with WorkerSessionLocal() as session:
                    repo = NotificationOutboxRepository(session)
                    due_rows = await repo.list_due_queued(limit=settings.NOTIFICATION_OUTBOX_REQUEUE_BATCH_SIZE)
                    for row in due_rows:
                        await self._queue.enqueue(
                            NotificationQueueItem(
                                outbox_id=row.id,
                                tenant_id=row.tenant_id,
                                channel=row.channel,
                                attempt=int(row.retry_count or 0) + 1,
                            )
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("notification_worker_requeue_loop_failed")
            await asyncio.sleep(max(settings.NOTIFICATION_OUTBOX_REQUEUE_INTERVAL_SECONDS, 1))

    async def _worker_loop(self, slot: int) -> None:
        while not self._stopping:
            item = await self._queue.get()
            if item is None:
                continue
            try:
                await self._process_item(item, slot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("notification_worker_item_failed", extra={"outbox_id": item.outbox_id, "slot": slot})

    async def _process_item(self, item: NotificationQueueItem, slot: int) -> None:
        ctx = TenantContext(
            tenant_id=item.tenant_id,
            user_id="notification-worker",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )
        async with WorkerSessionLocal() as session:
            repo = NotificationOutboxRepository(session, ctx)
            claimed = await repo.claim_outbox_entry(
                outbox_id=item.outbox_id,
                worker_id=f"{self._worker_id}-{slot}",
                stale_after=timedelta(seconds=max(settings.NOTIFICATION_DELIVERY_TIMEOUT_SECONDS + 15, 30)),
            )
            if not claimed:
                await self._queue.ack(item)
                return
            row = await repo.get_by_outbox_id(item.outbox_id)
            if row is None:
                await self._queue.ack(item)
                return
            if row.ledger_log_id:
                audit = NotificationDeliveryAuditService(session, ctx)
                await audit.mark_attempted(
                    row.ledger_log_id,
                    attempted_at=datetime.now(timezone.utc),
                    metadata_json=dict(row.payload_json or {}),
                )
                await session.commit()

        try:
            async with WorkerSessionLocal() as session:
                executor = NotificationExecutor(session, ctx)
                result = await asyncio.wait_for(
                    executor.execute_outbox_delivery(
                        channel=row.channel,
                        rule_id=row.rule_id,
                        device_id=row.device_id or "",
                        subject=row.subject,
                        message=row.message,
                        recipient=row.recipient_raw,
                        alert_id=row.alert_id,
                        event_type=row.event_type,
                        ledger_log_id=row.ledger_log_id,
                        payload_json=dict(row.payload_json or {}),
                    ),
                    timeout=max(settings.NOTIFICATION_DELIVERY_TIMEOUT_SECONDS, 1),
                )
                await session.commit()
        except asyncio.TimeoutError:
            await self._handle_failure(
                item=item,
                row=row,
                ctx=ctx,
                failure_code="DELIVERY_TIMEOUT",
                failure_message=f"Notification delivery exceeded timeout ({settings.NOTIFICATION_DELIVERY_TIMEOUT_SECONDS}s)",
            )
            return
        except Exception as exc:
            await self._handle_failure(item=item, row=row, ctx=ctx, failure_code=exc.__class__.__name__, failure_message=str(exc))
            return

        final_status = NotificationDeliveryStatus.FAILED.value
        provider_message_id = None
        if result.recipient_results:
            first = result.recipient_results[0]
            final_status = first.status
            provider_message_id = first.provider_message_id

        async with WorkerSessionLocal() as session:
            repo = NotificationOutboxRepository(session, ctx)
            if final_status in {NotificationDeliveryStatus.PROVIDER_ACCEPTED.value, NotificationDeliveryStatus.DELIVERED.value}:
                await repo.mark_terminal(
                    outbox_id=row.id,
                    status=final_status,
                    provider_message_id=provider_message_id,
                )
                await self._queue.ack(item)
                return
            if final_status == NotificationDeliveryStatus.SKIPPED.value:
                await repo.mark_terminal(
                    outbox_id=row.id,
                    status=NotificationDeliveryStatus.SKIPPED.value,
                    failure_code="NO_ACTIVE_RECIPIENTS",
                    failure_message="No active recipients configured.",
                )
                await self._queue.ack(item)
                return
        await self._handle_failure(
            item=item,
            row=row,
            ctx=ctx,
            failure_code="DELIVERY_FAILED",
            failure_message="Notification delivery failed",
        )

    async def _handle_failure(
        self,
        *,
        item: NotificationQueueItem,
        row,
        ctx: TenantContext,
        failure_code: str,
        failure_message: str,
    ) -> None:
        retry_count = int(getattr(row, "retry_count", 0) or 0) + 1
        async with WorkerSessionLocal() as session:
            repo = NotificationOutboxRepository(session, ctx)
            audit = NotificationDeliveryAuditService(session, ctx)
            if retry_count >= settings.NOTIFICATION_OUTBOX_MAX_RETRIES:
                await repo.mark_terminal(
                    outbox_id=row.id,
                    status=NotificationDeliveryStatus.FAILED.value,
                    failure_code=failure_code,
                    failure_message=failure_message,
                    dead_lettered=True,
                    when=datetime.now(timezone.utc),
                )
                if row.ledger_log_id:
                    await audit.mark_failed(
                        row.ledger_log_id,
                        failure_code=failure_code,
                        failure_message=failure_message,
                        failed_at=datetime.now(timezone.utc),
                    )
                await session.commit()
                await self._incr_event("dead_letter")
                await self._queue.dead_letter(item, failure_message)
                return

            next_attempt = datetime.now(timezone.utc) + timedelta(
                seconds=min(
                    settings.NOTIFICATION_BACKOFF_BASE_SECONDS * (2 ** max(retry_count - 1, 0)),
                    settings.NOTIFICATION_BACKOFF_MAX_SECONDS,
                )
            )
            await repo.requeue(
                outbox_id=row.id,
                next_attempt_at=next_attempt,
                failure_code=failure_code,
                failure_message=failure_message,
            )
            await session.commit()
        await self._incr_event("retry")
        await self._queue.enqueue(
            NotificationQueueItem(
                outbox_id=row.id,
                tenant_id=row.tenant_id,
                channel=row.channel,
                attempt=retry_count + 1,
            )
        )
        await self._queue.ack(item)
