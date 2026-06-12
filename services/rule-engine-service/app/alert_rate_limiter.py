from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_RATE_LIMIT_KEY_PREFIX = "alert_rate_limit:"
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_ALERTS = 50
_KEY_TTL_SECONDS = 120


class AlertRateLimiter:
    """Per-device alert storm protection with Redis-backed sliding window.

    When Redis is available, uses a sorted-set sliding window that is shared
    across all API replicas. When Redis is unavailable, falls back to a
    per-process in-memory deque — identical semantics to the original
    implementation but explicitly weaker in multi-instance deployments.
    """

    def __init__(self) -> None:
        self._max_alerts = _RATE_LIMIT_MAX_ALERTS
        self._window = timedelta(seconds=_RATE_LIMIT_WINDOW_SECONDS)
        self._in_memory: defaultdict[str, deque[datetime]] = defaultdict(deque)
        self._redis: Optional[object] = None

    async def _ensure_redis(self) -> Optional[object]:
        if self._redis is not None:
            return self._redis
        if not settings.REDIS_URL:
            return None
        try:
            from redis.asyncio import Redis as AIORedis
            self._redis = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
            return self._redis
        except Exception:
            logger.debug("alert_rate_limiter_redis_unavailable")
            return None

    async def is_rate_limited(self, device_id: str, now: datetime) -> bool:
        redis = await self._ensure_redis()
        if redis is not None:
            return await self._is_rate_limited_redis(redis, device_id, now)
        return self._is_rate_limited_memory(device_id, now)

    async def record_alert(self, device_id: str, when: datetime) -> None:
        redis = await self._ensure_redis()
        if redis is not None:
            await self._record_alert_redis(redis, device_id, when)
        else:
            self._record_alert_memory(device_id, when)

    async def _is_rate_limited_redis(self, redis: object, device_id: str, now: datetime) -> bool:
        key = f"{_RATE_LIMIT_KEY_PREFIX}{device_id}"
        window_start = now - self._window
        try:
            await redis.zremrangebyscore(key, "-inf", window_start.timestamp())
            count = await redis.zcard(key)
            return count >= self._max_alerts
        except Exception:
            logger.debug("alert_rate_limiter_redis_check_failed", exc_info=True)
            return self._is_rate_limited_memory(device_id, now)

    async def _record_alert_redis(self, redis: object, device_id: str, when: datetime) -> None:
        key = f"{_RATE_LIMIT_KEY_PREFIX}{device_id}"
        try:
            ts = when.timestamp()
            member = f"{when.isoformat()}:{ts}"
            await redis.zadd(key, {member: ts})
            await redis.expire(key, _KEY_TTL_SECONDS)
        except Exception:
            logger.debug("alert_rate_limiter_redis_record_failed", exc_info=True)
            self._record_alert_memory(device_id, when)

    def _is_rate_limited_memory(self, device_id: str, now: datetime) -> bool:
        timestamps = self._prune_memory(device_id, now)
        return len(timestamps) >= self._max_alerts

    def _record_alert_memory(self, device_id: str, when: datetime) -> None:
        timestamps = self._prune_memory(device_id, when)
        timestamps.append(when)

    def _prune_memory(self, device_id: str, now: datetime) -> deque[datetime]:
        window_start = now - self._window
        timestamps = self._in_memory[device_id]
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()
        return timestamps

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None


_limiter: Optional[AlertRateLimiter] = None


def get_alert_rate_limiter() -> AlertRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = AlertRateLimiter()
    return _limiter


async def close_alert_rate_limiter() -> None:
    global _limiter
    if _limiter is not None:
        await _limiter.close()
        _limiter = None
