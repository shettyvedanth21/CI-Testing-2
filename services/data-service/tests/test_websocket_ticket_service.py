from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = PROJECT_ROOT / "services"
for path in (PROJECT_ROOT, SERVICES_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from src.services.websocket_ticket_service import WebSocketTicketService


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int, nx: bool) -> bool:
        assert ex >= 5
        assert nx is True
        if key in self.store:
            return False
        self.store[key] = value
        return True

    async def eval(self, script: str, key_count: int, key: str):  # noqa: ANN001
        assert "redis.call('GET'" in script
        assert key_count == 1
        value = self.store.get(key)
        if value is not None:
            del self.store[key]
        return value

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_websocket_ticket_is_single_use() -> None:
    service = WebSocketTicketService(redis_client=_FakeRedis())

    issued = await service.issue_ticket(
        user_id="user-1",
        role="viewer",
        tenant_id="tenant-a",
        device_id="DEVICE-1",
    )

    assert issued["ticket"]
    payload = await service.consume_ticket(issued["ticket"])
    assert payload == {
        "user_id": "user-1",
        "role": "viewer",
        "tenant_id": "tenant-a",
        "device_id": "DEVICE-1",
    }
    assert await service.consume_ticket(issued["ticket"]) is None
