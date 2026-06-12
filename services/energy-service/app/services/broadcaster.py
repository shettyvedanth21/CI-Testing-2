from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

from redis.asyncio import Redis


class EnergyBroadcaster:
    def __init__(self) -> None:
        self._redis: Optional[Redis] = None
        self._channel: Optional[str] = None

    async def start(self, redis_url: Optional[str], channel: str) -> None:
        if not redis_url:
            return
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        await self._redis.ping()
        self._channel = channel

    async def stop(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        if not self._redis or not self._channel:
            return
        message = {
            "event": event,
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._redis.publish(self._channel, json.dumps(message, separators=(",", ":")))

    async def publish_many(self, event: str, payloads: list[dict[str, Any]]) -> None:
        if not payloads:
            return
        if not self._redis or not self._channel:
            return
        created_at = datetime.now(timezone.utc).isoformat()
        pipeline = self._redis.pipeline(transaction=False)
        for payload in payloads:
            message = {
                "event": event,
                "payload": payload,
                "created_at": created_at,
            }
            pipeline.publish(self._channel, json.dumps(message, separators=(",", ":")))
        await pipeline.execute()


energy_broadcaster = EnergyBroadcaster()
