from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WasteJob:
    job_id: str
    tenant_id: str
    params_json: str
    attempt: int = 1
    receipt: str | None = None


class WasteQueue(Protocol):
    async def enqueue(self, job: WasteJob) -> None: ...
    async def get_job(self) -> WasteJob | None: ...
    async def ack(self, job: WasteJob) -> None: ...
    async def dead_letter(self, job: WasteJob, reason: str) -> None: ...
    async def metrics(self) -> dict[str, int]: ...


class InMemoryWasteQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[WasteJob] = asyncio.Queue()
        self._dead_letter_count = 0

    async def enqueue(self, job: WasteJob) -> None:
        await self._queue.put(job)

    async def get_job(self) -> WasteJob | None:
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            return None

    async def ack(self, job: WasteJob) -> None:
        try:
            self._queue.task_done()
        except ValueError:
            return

    async def dead_letter(self, job: WasteJob, reason: str) -> None:
        self._dead_letter_count += 1
        try:
            self._queue.task_done()
        except ValueError:
            pass
        logger.error("waste_job_dead_lettered", extra={"job_id": job.job_id, "reason": reason})

    async def metrics(self) -> dict[str, int]:
        return {
            "queue_depth": self._queue.qsize(),
            "pending_messages": 0,
            "dead_letter_count": self._dead_letter_count,
        }


class RedisWasteQueue:
    def __init__(self) -> None:
        from redis.asyncio import Redis

        if not settings.REDIS_URL:
            raise RuntimeError("REDIS_URL is required for the Redis waste queue")

        read_timeout_seconds = max(10, (settings.WASTE_QUEUE_READ_BLOCK_MS / 1000) + 5)
        self._redis = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            retry_on_timeout=True,
            socket_connect_timeout=5,
            socket_timeout=read_timeout_seconds,
        )
        self._stream = settings.WASTE_QUEUE_STREAM
        self._dead_stream = settings.WASTE_QUEUE_DEAD_LETTER_STREAM
        self._group = settings.WASTE_QUEUE_CONSUMER_GROUP
        self._consumer = settings.WASTE_QUEUE_CONSUMER_NAME
        self._group_ready = False

    @staticmethod
    def _is_missing_consumer_group_error(exc: Exception) -> bool:
        return "NOGROUP" in str(exc).upper()

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def enqueue(self, job: WasteJob) -> None:
        await self._ensure_group()
        payload = {
            "payload": json.dumps(
                {
                    **asdict(job),
                    "receipt": None,
                    "enqueued_at": datetime.now(timezone.utc).isoformat(),
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
        await self._redis.xadd(
            self._stream,
            payload,
            maxlen=settings.WASTE_QUEUE_MAXLEN,
            approximate=True,
        )

    async def _read_stale_pending(self) -> WasteJob | None:
        entries = await self._redis.xautoclaim(
            self._stream,
            self._group,
            self._consumer,
            min_idle_time=settings.WASTE_QUEUE_CLAIM_IDLE_MS,
            start_id="0-0",
            count=1,
        )
        claimed = entries[1] if isinstance(entries, (list, tuple)) and len(entries) > 1 else []
        if not claimed:
            return None
        receipt, values = claimed[0]
        payload = json.loads(values["payload"])
        return WasteJob(
            job_id=payload["job_id"],
            tenant_id=payload["tenant_id"],
            params_json=payload["params_json"],
            attempt=int(payload.get("attempt") or 1),
            receipt=receipt,
        )

    async def get_job(self) -> WasteJob | None:
        recovered_missing_group = False
        while True:
            await self._ensure_group()
            try:
                stale = await self._read_stale_pending()
            except (RedisTimeoutError, RedisConnectionError) as exc:
                logger.warning(
                    "waste_queue_redis_transient_read_error",
                    extra={"stream": self._stream, "consumer_group": self._group, "error": str(exc)},
                )
                return None
            if stale is not None:
                return stale
            try:
                entries = await self._redis.xreadgroup(
                    groupname=self._group,
                    consumername=self._consumer,
                    streams={self._stream: ">"},
                    count=1,
                    block=settings.WASTE_QUEUE_READ_BLOCK_MS,
                )
            except asyncio.CancelledError:
                return None
            except Exception as exc:
                if not recovered_missing_group and self._is_missing_consumer_group_error(exc):
                    recovered_missing_group = True
                    self._group_ready = False
                    logger.warning(
                        "waste_queue_consumer_group_missing_recreating",
                        extra={"stream": self._stream, "consumer_group": self._group, "error": str(exc)},
                    )
                    continue
                if isinstance(exc, (RedisTimeoutError, RedisConnectionError)):
                    logger.warning(
                        "waste_queue_redis_transient_read_error",
                        extra={"stream": self._stream, "consumer_group": self._group, "error": str(exc)},
                    )
                    return None
                raise
            break
        if not entries:
            return None
        _, records = entries[0]
        if not records:
            return None
        receipt, values = records[0]
        payload = json.loads(values["payload"])
        return WasteJob(
            job_id=payload["job_id"],
            tenant_id=payload["tenant_id"],
            params_json=payload["params_json"],
            attempt=int(payload.get("attempt") or 1),
            receipt=receipt,
        )

    async def ack(self, job: WasteJob) -> None:
        if job.receipt:
            await self._redis.xack(self._stream, self._group, job.receipt)

    async def dead_letter(self, job: WasteJob, reason: str) -> None:
        await self._redis.xadd(
            self._dead_stream,
            {
                "job_id": job.job_id,
                "tenant_id": job.tenant_id,
                "attempt": str(job.attempt),
                "reason": reason[:2048],
                "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=settings.WASTE_QUEUE_MAXLEN,
            approximate=True,
        )
        await self.ack(job)

    async def metrics(self) -> dict[str, int]:
        await self._ensure_group()
        queue_depth = int(await self._redis.xlen(self._stream))
        pending_messages = 0
        dead_letter_count = int(await self._redis.xlen(self._dead_stream))
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


_queue_singleton: WasteQueue | None = None


def get_waste_queue() -> WasteQueue:
    global _queue_singleton
    if _queue_singleton is None:
        if settings.WASTE_QUEUE_BACKEND == "redis":
            _queue_singleton = RedisWasteQueue()
        else:
            _queue_singleton = InMemoryWasteQueue()
    return _queue_singleton
