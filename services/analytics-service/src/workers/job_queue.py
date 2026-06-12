"""Queue backends for analytics jobs."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

import structlog

from services.shared.job_context import BoundJobPayload
from src.models.schemas import AnalyticsRequest

logger = structlog.get_logger()


@dataclass
class Job:
    """Queue job container."""

    job_id: str
    raw_payload: str
    attempt: int = 1
    receipt: Optional[str] = None


class QueueBackend(Protocol):
    async def submit_job(self, job_id: str, raw_payload: str, attempt: int = 1) -> None: ...
    async def get_job(self) -> Optional[Job]: ...
    async def ack_job(self, receipt: str) -> None: ...
    async def dead_letter(self, job: Job, reason: str) -> None: ...
    async def metrics(self) -> dict[str, int]: ...
    def task_done(self) -> None: ...
    def size(self) -> int: ...
    def empty(self) -> bool: ...


class InMemoryJobQueue:
    """In-memory queue backend used in tests/dev fallback."""

    def __init__(self, maxsize: int = 100):
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=maxsize)
        self._logger = logger.bind(worker="InMemoryJobQueue")

    async def submit_job(self, job_id: str, raw_payload: str, attempt: int = 1) -> None:
        job = Job(job_id=job_id, raw_payload=raw_payload, attempt=attempt)
        await self._queue.put(job)
        self._logger.info("job_queued", job_id=job_id, attempt=attempt)

    async def get_job(self) -> Optional[Job]:
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            return None

    async def ack_job(self, receipt: str) -> None:
        return

    async def dead_letter(self, job: Job, reason: str) -> None:
        self._logger.error("job_dead_lettered", job_id=job.job_id, attempt=job.attempt, reason=reason)

    async def metrics(self) -> dict[str, int]:
        return {
            "queued_messages": self._queue.qsize(),
            "claimed_messages": 0,
            "dead_letter_messages": 0,
        }

    def task_done(self) -> None:
        self._queue.task_done()

    def size(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()


class RedisJobQueue:
    """Redis streams-based durable queue backend."""

    def __init__(
        self,
        redis_url: str,
        stream_name: str,
        dead_letter_stream: str,
        consumer_group: str,
        consumer_name: str,
        maxsize: int = 10000,
    ):
        from redis.asyncio import Redis

        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._stream = stream_name
        self._dead_stream = dead_letter_stream
        self._group = consumer_group
        self._consumer = consumer_name
        self._maxsize = maxsize
        self._logger = logger.bind(worker="RedisJobQueue", consumer=self._consumer)
        self._group_ready = False

    @staticmethod
    def _is_missing_consumer_group_error(exc: Exception) -> bool:
        message = str(exc).upper()
        return "NOGROUP" in message

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def submit_job(self, job_id: str, raw_payload: str, attempt: int = 1) -> None:
        await self._ensure_group()
        payload = {
            "job_id": job_id,
            "attempt": str(attempt),
            "raw_payload": raw_payload,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        stream_length = int(await self._redis.xlen(self._stream))
        if stream_length >= self._maxsize:
            raise RuntimeError("queue capacity reached")
        await self._redis.xadd(self._stream, payload, maxlen=self._maxsize, approximate=True)
        self._logger.info("job_queued", job_id=job_id, attempt=attempt)

    async def get_job(self) -> Optional[Job]:
        recovered_missing_group = False
        while True:
            await self._ensure_group()
            try:
                entries = await self._redis.xreadgroup(
                    groupname=self._group,
                    consumername=self._consumer,
                    streams={self._stream: ">"},
                    count=1,
                    block=5000,
                )
            except asyncio.CancelledError:
                return None
            except Exception as exc:
                if not recovered_missing_group and self._is_missing_consumer_group_error(exc):
                    recovered_missing_group = True
                    self._group_ready = False
                    self._logger.warning(
                        "redis_consumer_group_missing_recreating",
                        stream=self._stream,
                        consumer_group=self._group,
                        error=str(exc),
                    )
                    continue
                raise
            break
        if not entries:
            return None
        _, records = entries[0]
        if not records:
            return None
        record_id, values = records[0]
        raw_payload = values["raw_payload"]
        payload_dict = json.loads(raw_payload)
        BoundJobPayload(**payload_dict)
        attempt = int(values.get("attempt", "1"))
        return Job(
            job_id=values["job_id"],
            raw_payload=raw_payload,
            attempt=attempt,
            receipt=record_id,
        )

    async def ack_job(self, receipt: str) -> None:
        await self._redis.xack(self._stream, self._group, receipt)

    async def dead_letter(self, job: Job, reason: str) -> None:
        await self._redis.xadd(
            self._dead_stream,
            {
                "job_id": job.job_id,
                "attempt": str(job.attempt),
                "reason": reason[:2048],
                "raw_payload": job.raw_payload,
                "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=10000,
            approximate=True,
        )
        if job.receipt:
            await self.ack_job(job.receipt)
        self._logger.error("job_dead_lettered", job_id=job.job_id, attempt=job.attempt, reason=reason)

    async def metrics(self) -> dict[str, int]:
        await self._ensure_group()
        try:
            pending = await self._redis.xpending(self._stream, self._group)
            if isinstance(pending, dict):
                claimed_messages = int(pending.get("pending", 0))
            elif isinstance(pending, (list, tuple)) and pending:
                claimed_messages = int(pending[0] or 0)
            else:
                claimed_messages = 0
        except Exception:
            claimed_messages = 0

        return {
            "queued_messages": int(await self._redis.xlen(self._stream)),
            "claimed_messages": claimed_messages,
            "dead_letter_messages": int(await self._redis.xlen(self._dead_stream)),
        }

    def task_done(self) -> None:
        # Redis streams don't need task_done semantics.
        return

    def size(self) -> int:
        # best effort synchronous read by returning cached/zero if unavailable
        return 0

    def empty(self) -> bool:
        return False
