"""Short-lived single-use tickets for telemetry WebSocket handshakes."""

from __future__ import annotations

import json
import secrets
from typing import Any

from redis.asyncio import Redis

from src.config import settings
from src.utils import get_logger

logger = get_logger(__name__)

_TICKET_REDIS_CLIENT: Redis | None = None
_TICKET_SERVICE: "WebSocketTicketService | None" = None


class WebSocketTicketServiceError(RuntimeError):
    """Raised when a WebSocket ticket cannot be issued or consumed safely."""


class WebSocketTicketService:
    """Issue and consume short-lived, single-use WebSocket tickets."""

    _KEY_PREFIX = "ws:ticket"
    _CONSUME_SCRIPT = """
    local value = redis.call('GET', KEYS[1])
    if value then
        redis.call('DEL', KEYS[1])
    end
    return value
    """

    def __init__(self, redis_client: Redis | None = None) -> None:
        if redis_client is not None:
            self._redis = redis_client
        else:
            redis_url = settings.redis_url
            if not redis_url:
                raise WebSocketTicketServiceError("REDIS_URL is required for WebSocket ticket issuance")
            self._redis = Redis.from_url(
                redis_url,
                decode_responses=True,
                max_connections=max(4, min(settings.redis_max_connections, 32)),
            )

    def _key(self, ticket: str) -> str:
        return f"{self._KEY_PREFIX}:{ticket}"

    async def issue_ticket(
        self,
        *,
        user_id: str,
        role: str,
        tenant_id: str,
        device_id: str,
    ) -> dict[str, Any]:
        ttl_seconds = max(5, int(settings.ws_ticket_ttl_seconds))
        ticket = secrets.token_urlsafe(32)
        payload = {
            "user_id": str(user_id).strip(),
            "role": str(role).strip(),
            "tenant_id": str(tenant_id).strip(),
            "device_id": str(device_id).strip(),
        }
        if not all(payload.values()):
            raise WebSocketTicketServiceError("Ticket payload is incomplete")

        stored = await self._redis.set(
            self._key(ticket),
            json.dumps(payload),
            ex=ttl_seconds,
            nx=True,
        )
        if not stored:
            raise WebSocketTicketServiceError("Failed to reserve WebSocket ticket")

        return {
            "ticket": ticket,
            "expires_in_seconds": ttl_seconds,
        }

    async def consume_ticket(self, ticket: str) -> dict[str, Any] | None:
        normalized_ticket = str(ticket or "").strip()
        if not normalized_ticket:
            return None
        raw = await self._redis.eval(self._CONSUME_SCRIPT, 1, self._key(normalized_ticket))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid WebSocket ticket payload in Redis", error=str(exc))
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    async def close(self) -> None:
        await self._redis.close()


def get_websocket_ticket_service() -> WebSocketTicketService:
    global _TICKET_REDIS_CLIENT, _TICKET_SERVICE
    if _TICKET_SERVICE is not None:
        return _TICKET_SERVICE
    if _TICKET_REDIS_CLIENT is None:
        redis_url = settings.redis_url
        if not redis_url:
            raise WebSocketTicketServiceError("REDIS_URL is required for WebSocket ticket issuance")
        _TICKET_REDIS_CLIENT = Redis.from_url(
            redis_url,
            decode_responses=True,
            max_connections=max(4, min(settings.redis_max_connections, 32)),
        )
    _TICKET_SERVICE = WebSocketTicketService(redis_client=_TICKET_REDIS_CLIENT)
    return _TICKET_SERVICE


async def close_websocket_ticket_service() -> None:
    global _TICKET_REDIS_CLIENT, _TICKET_SERVICE
    if _TICKET_SERVICE is not None:
        await _TICKET_SERVICE.close()
    _TICKET_SERVICE = None
    _TICKET_REDIS_CLIENT = None
