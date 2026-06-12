"""Per-tenant serialization for projection batch processing."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncIterator

try:
    from prometheus_client import Counter, Gauge, Histogram
except ImportError:

    class _StubMetric:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def labels(self, **_kwargs: object) -> "_StubMetric":
            return self

        def inc(self, _amount: int = 1) -> None:
            pass

        def set(self, _value: int | float) -> None:
            pass

        def observe(self, _value: float) -> None:
            pass

    Counter = Gauge = Histogram = _StubMetric  # type: ignore[assignment]

PROJECTION_TENANT_LOCK_ACQUIRE_TOTAL = Counter(
    "projection_tenant_lock_acquire_total",
    "Times a tenant projection lock was acquired",
    ["contention"],
)
PROJECTION_TENANT_LOCK_ACTIVE = Gauge(
    "projection_tenant_lock_active",
    "Number of active tenant lock entries",
)
PROJECTION_TENANT_LOCK_REDIS_ACQUIRE_DURATION_SECONDS = Histogram(
    "projection_tenant_lock_redis_acquire_duration_seconds",
    "Time spent waiting for Redis tenant lock acquisition",
    ["outcome"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0],
)

_LOCK_KEY_PREFIX = "shivex:tenant_lock:projection:"
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class TenantLockTimeoutError(Exception):
    """Raised when a tenant lock cannot be acquired within the configured timeout."""


class TenantLockProvider(ABC):
    """Contract for per-tenant serialization of projection batches."""

    @abstractmethod
    @asynccontextmanager
    async def acquire(self, tenant_id: str) -> AsyncIterator[None]:
        ...

    @abstractmethod
    def cleanup_inactive(self) -> int:
        """Remove internal state for tenants with no active or waiting lock. Returns count removed."""
        ...

    @property
    @abstractmethod
    def active_lock_count(self) -> int:
        """Number of tenant lock entries currently tracked."""
        ...


class InProcessTenantLock(TenantLockProvider):
    """Layer 1: asyncio.Lock per tenant within a single process."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, tenant_id: str) -> asyncio.Lock:
        if tenant_id not in self._locks:
            self._locks[tenant_id] = asyncio.Lock()
        return self._locks[tenant_id]

    @asynccontextmanager
    async def acquire(self, tenant_id: str) -> AsyncIterator[None]:
        lock = self._get_lock(tenant_id)
        contention = "contended" if lock.locked() else "uncontended"
        PROJECTION_TENANT_LOCK_ACQUIRE_TOTAL.labels(contention=contention).inc()
        async with lock:
            yield

    def cleanup_inactive(self) -> int:
        stale = [
            tid
            for tid, lock in self._locks.items()
            if not lock.locked()
        ]
        for tid in stale:
            del self._locks[tid]
        PROJECTION_TENANT_LOCK_ACTIVE.set(len(self._locks))
        return len(stale)

    @property
    def active_lock_count(self) -> int:
        return len(self._locks)


class RedisTenantLock(TenantLockProvider):
    """Layer 2: Redis-backed per-tenant lock for cross-process serialization."""

    _POLL_BASE_SECONDS = 0.05
    _POLL_MAX_SECONDS = 0.5
    _POLL_BACKOFF_FACTOR = 2.0

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        lock_ttl_seconds: int = 30,
        acquire_timeout_seconds: float = 15.0,
    ) -> None:
        from redis.asyncio import Redis as AsyncRedis

        self._redis: AsyncRedis = AsyncRedis.from_url(
            redis_url or "",
            decode_responses=True,
        )
        self._lock_ttl_ms = max(1000, lock_ttl_seconds * 1000)
        self._acquire_timeout_seconds = max(1.0, acquire_timeout_seconds)
        self._identity = f"{os.uname().nodename[:32]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._active_count = 0

    async def _try_acquire(self, key: str, value: str) -> bool:
        return bool(await self._redis.set(key, value, nx=True, px=self._lock_ttl_ms))

    async def _release(self, key: str, value: str) -> None:
        result = await self._redis.eval(_RELEASE_LUA, 1, key, value)
        if not result:
            from src.utils import get_logger as _get_logger
            _get_logger(__name__).warning(
                "Redis tenant lock release skipped: lock expired or re-acquired by another holder",
                lock_key=key,
            )

    @asynccontextmanager
    async def acquire(self, tenant_id: str) -> AsyncIterator[None]:
        key = f"{_LOCK_KEY_PREFIX}{tenant_id}"
        value = f"{self._identity}:{uuid.uuid4().hex[:8]}"
        start = time.monotonic()
        acquired = await self._try_acquire(key, value)
        contention = "contended" if not acquired else "uncontended"
        PROJECTION_TENANT_LOCK_ACQUIRE_TOTAL.labels(contention=contention).inc()

        if not acquired:
            poll_interval = self._POLL_BASE_SECONDS
            deadline = start + self._acquire_timeout_seconds
            while time.monotonic() < deadline:
                await asyncio.sleep(poll_interval)
                acquired = await self._try_acquire(key, value)
                if acquired:
                    break
                poll_interval = min(self._POLL_MAX_SECONDS, poll_interval * self._POLL_BACKOFF_FACTOR)

        wait_seconds = time.monotonic() - start

        if not acquired:
            PROJECTION_TENANT_LOCK_REDIS_ACQUIRE_DURATION_SECONDS.labels(outcome="timeout").observe(wait_seconds)
            raise TenantLockTimeoutError(
                f"Could not acquire Redis tenant lock for tenant {tenant_id} within {self._acquire_timeout_seconds}s"
            )

        PROJECTION_TENANT_LOCK_REDIS_ACQUIRE_DURATION_SECONDS.labels(outcome="acquired").observe(wait_seconds)
        if wait_seconds > 0.01:
            from src.utils import get_logger as _get_logger
            _get_logger(__name__).info(
                "Redis tenant lock acquired after wait",
                tenant_id=tenant_id,
                wait_seconds=round(wait_seconds, 3),
            )
        self._active_count += 1
        PROJECTION_TENANT_LOCK_ACTIVE.set(self._active_count)
        try:
            yield
        finally:
            self._active_count -= 1
            PROJECTION_TENANT_LOCK_ACTIVE.set(self._active_count)
            await self._release(key, value)

    def cleanup_inactive(self) -> int:
        return 0

    @property
    def active_lock_count(self) -> int:
        return self._active_count

    async def close(self) -> None:
        await self._redis.close()


def create_tenant_lock(
    provider: str | None = None,
    *,
    redis_url: str | None = None,
    lock_ttl_seconds: int = 30,
    acquire_timeout_seconds: float = 15.0,
) -> TenantLockProvider:
    if (provider or "").strip().lower() == "redis":
        return RedisTenantLock(
            redis_url=redis_url,
            lock_ttl_seconds=lock_ttl_seconds,
            acquire_timeout_seconds=acquire_timeout_seconds,
        )
    return InProcessTenantLock()
