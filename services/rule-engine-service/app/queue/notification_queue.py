from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationQueueItem:
    outbox_id: str
    tenant_id: str
    channel: str
    attempt: int = 1
    receipt: str | None = None


class NotificationQueue(Protocol):
    async def enqueue(self, item: NotificationQueueItem) -> None: ...
    async def get(self) -> NotificationQueueItem | None: ...
    async def ack(self, item: NotificationQueueItem) -> None: ...
    async def dead_letter(self, item: NotificationQueueItem, reason: str) -> None: ...
    async def metrics(self) -> dict[str, int]: ...


class InMemoryNotificationQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[NotificationQueueItem] = asyncio.Queue()
        self._dead_letter_count = 0

    async def enqueue(self, item: NotificationQueueItem) -> None:
        await self._queue.put(item)

    async def get(self) -> NotificationQueueItem | None:
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            return None

    async def ack(self, item: NotificationQueueItem) -> None:
        try:
            self._queue.task_done()
        except ValueError:
            return

    async def dead_letter(self, item: NotificationQueueItem, reason: str) -> None:
        self._dead_letter_count += 1
        try:
            self._queue.task_done()
        except ValueError:
            pass
        logger.error("notification_queue_dead_lettered", extra={"outbox_id": item.outbox_id, "reason": reason})

    async def metrics(self) -> dict[str, int]:
        return {
            "queue_depth": self._queue.qsize(),
            "pending_messages": 0,
            "dead_letter_count": self._dead_letter_count,
        }


class RedisNotificationQueue:
    def __init__(self) -> None:
        from redis.asyncio import Redis

        if not settings.REDIS_URL:
            raise RuntimeError("REDIS_URL is required for Redis notification queue")
        self._redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        self._stream = settings.NOTIFICATION_OUTBOX_STREAM
        self._dead_stream = settings.NOTIFICATION_OUTBOX_DEAD_LETTER_STREAM
        self._group = settings.NOTIFICATION_OUTBOX_CONSUMER_GROUP
        self._consumer = settings.NOTIFICATION_OUTBOX_CONSUMER_NAME
        self._group_ready = False

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def enqueue(self, item: NotificationQueueItem) -> None:
        await self._ensure_group()
        await self._redis.xadd(
            self._stream,
            {
                "payload": json.dumps(
                    {
                        **asdict(item),
                        "receipt": None,
                        "enqueued_at": datetime.now(timezone.utc).isoformat(),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            },
            maxlen=settings.NOTIFICATION_OUTBOX_QUEUE_MAXLEN,
            approximate=True,
        )

    async def _read_stale_pending(self) -> NotificationQueueItem | None:
        entries = await self._redis.xautoclaim(
            self._stream,
            self._group,
            self._consumer,
            min_idle_time=settings.NOTIFICATION_OUTBOX_CLAIM_IDLE_MS,
            start_id="0-0",
            count=1,
        )
        claimed = entries[1] if isinstance(entries, (list, tuple)) and len(entries) > 1 else []
        if not claimed:
            return None
        receipt, values = claimed[0]
        payload = json.loads(values["payload"])
        return NotificationQueueItem(
            outbox_id=payload["outbox_id"],
            tenant_id=payload["tenant_id"],
            channel=payload["channel"],
            attempt=int(payload.get("attempt") or 1),
            receipt=receipt,
        )

    async def get(self) -> NotificationQueueItem | None:
        await self._ensure_group()
        stale = await self._read_stale_pending()
        if stale is not None:
            return stale
        try:
            entries = await self._redis.xreadgroup(
                groupname=self._group,
                consumername=self._consumer,
                streams={self._stream: ">"},
                count=1,
                block=settings.NOTIFICATION_OUTBOX_READ_BLOCK_MS,
            )
        except asyncio.CancelledError:
            return None
        if not entries:
            return None
        _, records = entries[0]
        if not records:
            return None
        receipt, values = records[0]
        payload = json.loads(values["payload"])
        return NotificationQueueItem(
            outbox_id=payload["outbox_id"],
            tenant_id=payload["tenant_id"],
            channel=payload["channel"],
            attempt=int(payload.get("attempt") or 1),
            receipt=receipt,
        )

    async def ack(self, item: NotificationQueueItem) -> None:
        if item.receipt:
            await self._redis.xack(self._stream, self._group, item.receipt)

    async def dead_letter(self, item: NotificationQueueItem, reason: str) -> None:
        await self._redis.xadd(
            self._dead_stream,
            {
                "outbox_id": item.outbox_id,
                "tenant_id": item.tenant_id,
                "channel": item.channel,
                "attempt": str(item.attempt),
                "reason": reason[:2048],
                "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=settings.NOTIFICATION_OUTBOX_QUEUE_MAXLEN,
            approximate=True,
        )
        await self.ack(item)

    async def metrics(self) -> dict[str, int]:
        await self._ensure_group()
        queue_depth = int(await self._redis.xlen(self._stream))
        dead_letter_count = int(await self._redis.xlen(self._dead_stream))
        pending_messages = 0
        groups = await self._redis.xinfo_groups(self._stream)
        for group in groups:
            if group.get("name") == self._group:
                pending_messages = int(group.get("pending", 0))
                break
        return {
            "queue_depth": queue_depth,
            "pending_messages": pending_messages,
            "dead_letter_count": dead_letter_count,
        }


_queue_singleton: NotificationQueue | None = None


def get_notification_queue() -> NotificationQueue:
    global _queue_singleton
    if _queue_singleton is None:
        if settings.QUEUE_BACKEND == "redis":
            _queue_singleton = RedisNotificationQueue()
        else:
            _queue_singleton = InMemoryNotificationQueue()
    return _queue_singleton
