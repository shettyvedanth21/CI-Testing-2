from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.api.v1.router import api_router
from app.api.v1 import devices as devices_api
from app.monitoring import FleetStreamBroadcaster
from services.shared.tenant_context import TenantContext


class _LiveDashboardStub:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def device_name_matches_search(device_name: str | None, search: str | None) -> bool:
        if not search:
            return True
        return search.casefold() in str(device_name or "").casefold()

    async def get_fleet_snapshot(
        self,
        page: int = 1,
        page_size: int = 50,
        sort: str = "device_name",
        tenant_id: str | None = None,
        runtime_filter: str | None = None,
        operational_status_filter: str | None = None,
        accessible_plant_ids: list[str] | None = None,
        search: str | None = None,
    ) -> dict:
        devices = [
            {
                "device_id": f"{tenant_id}-device",
                "device_name": f"{tenant_id} device",
                "device_type": "compressor",
                "runtime_status": "running",
                "version": 1,
                "plant_id": "PLANT-A",
            }
        ]
        if accessible_plant_ids is not None:
            devices = [device for device in devices if device["plant_id"] in accessible_plant_ids]
        if search:
            lowered_search = search.casefold()
            devices = [device for device in devices if lowered_search in str(device["device_name"]).casefold()]
        return {
            "success": True,
            "generated_at": "2026-04-03T00:00:00+00:00",
            "stale": False,
            "warnings": [],
            "devices": devices,
        }

    @staticmethod
    def observe_stream_emit_lag(created_at):
        return None


class _FakeRedisBroker:
    def __init__(self):
        self.channels: dict[str, set[_FakePubSub]] = {}
        self.counters: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []
        self.subscriptions: list[str] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))
        for pubsub in list(self.channels.get(channel, set())):
            await pubsub.messages.put({"data": payload})

    def register(self, channel: str, pubsub: "_FakePubSub") -> None:
        self.channels.setdefault(channel, set()).add(pubsub)
        self.subscriptions.append(channel)

    def unregister(self, pubsub: "_FakePubSub") -> None:
        for subscribers in self.channels.values():
            subscribers.discard(pubsub)


class _FakePubSub:
    def __init__(self, broker: _FakeRedisBroker):
        self._broker = broker
        self.messages: asyncio.Queue[dict] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self._broker.register(channel, self)

    async def get_message(self, timeout: float = 1.0):
        try:
            return await asyncio.wait_for(self.messages.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        self._broker.unregister(self)


class _FakeRedis:
    broker = _FakeRedisBroker()

    @classmethod
    def from_url(cls, url: str, decode_responses: bool = True):
        return cls()

    async def ping(self) -> bool:
        return True

    def pubsub(self, ignore_subscribe_messages: bool = True) -> _FakePubSub:
        return _FakePubSub(self.broker)

    async def incr(self, key: str) -> int:
        next_value = self.broker.counters.get(key, 0) + 1
        self.broker.counters[key] = next_value
        return next_value

    async def publish(self, channel: str, payload: str) -> None:
        await self.broker.publish(channel, payload)

    async def close(self) -> None:
        return None


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _fake_session_local():
    return _SessionContext()


def _parse_sse_chunk(chunk: str) -> dict:
    lines = [line for line in chunk.splitlines() if line]
    event: dict[str, str] = {}
    for line in lines:
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        event[key] = value
    if "data" in event:
        event["data"] = json.loads(event["data"])
    return event


class _RequestStub:
    def __init__(self, tenant_id: str | None, auth: dict | None = None):
        self.headers = {"X-Tenant-Id": tenant_id} if tenant_id is not None else {}
        self.query_params = {}
        default_auth = {
            "role": "org_admin",
            "plant_ids": [],
            "tenant_id": tenant_id,
            "user_id": "test-user",
            "is_super_admin": False,
        }
        resolved_auth = auth or default_auth
        self.state = SimpleNamespace(
            auth=resolved_auth,
            tenant_context=TenantContext(
                tenant_id=resolved_auth["tenant_id"],
                user_id=resolved_auth["user_id"],
                role=resolved_auth["role"],
                plant_ids=list(resolved_auth["plant_ids"]),
                is_super_admin=resolved_auth["is_super_admin"],
            ),
        )
        self._disconnected = False

    async def is_disconnected(self) -> bool:
        return self._disconnected


@pytest.mark.asyncio
async def test_broadcaster_fanout_is_scoped_per_tenant():
    broadcaster = FleetStreamBroadcaster(queue_size=2)
    subscriber_a, queue_a = await broadcaster.subscribe("tenant-a")
    subscriber_b, queue_b = await broadcaster.subscribe("tenant-b")

    try:
        await broadcaster.publish("tenant-a", "fleet_update", {"devices": [{"device_id": "A-1"}]})
        message_a = await asyncio.wait_for(queue_a.get(), timeout=1.0)

        assert message_a.data["devices"][0]["device_id"] == "A-1"
        assert queue_b.empty()
        assert broadcaster.latest_event_id("tenant-a") == "1"
        assert broadcaster.latest_event_id("tenant-b") == "0"
    finally:
        await broadcaster.unsubscribe("tenant-a", subscriber_a, reason="test_cleanup")
        await broadcaster.unsubscribe("tenant-b", subscriber_b, reason="test_cleanup")
        await broadcaster.stop()


@pytest.mark.asyncio
async def test_sse_reconnect_stays_bound_to_original_tenant(monkeypatch):
    broadcaster = FleetStreamBroadcaster(queue_size=4)
    monkeypatch.setattr("app.services.live_dashboard.LiveDashboardService", _LiveDashboardStub)
    monkeypatch.setattr(devices_api, "fleet_stream_broadcaster", broadcaster)
    monkeypatch.setattr(devices_api, "AsyncSessionLocal", _fake_session_local)
    monkeypatch.setattr(devices_api.settings, "DASHBOARD_STREAM_HEARTBEAT_SECONDS", 10)
    monkeypatch.setattr(devices_api.settings, "DASHBOARD_STREAM_SEND_TIMEOUT_SECONDS", 10)

    try:
        request_one = _RequestStub("tenant-a")
        response_one = await devices_api.fleet_snapshot_stream(
            request=request_one,
            page_size=200,
            plant_id=None,
            search=None,
            runtime_status=None,
            operational_status=None,
            last_event_id_query=None,
            last_event_id=None,
        )
        stream_one = response_one.body_iterator
        snapshot_event = _parse_sse_chunk(await asyncio.wait_for(anext(stream_one), timeout=1.0))
        assert snapshot_event["event"] == "fleet_update"
        assert snapshot_event["data"]["devices"][0]["device_id"] == "tenant-a-device"

        request_one._disconnected = True
        await stream_one.aclose()
        last_event_id = snapshot_event["id"]

        request_two = _RequestStub("tenant-a")
        response_two = await devices_api.fleet_snapshot_stream(
            request=request_two,
            page_size=200,
            plant_id=None,
            search=None,
            runtime_status=None,
            operational_status=None,
            last_event_id_query=last_event_id,
            last_event_id=last_event_id,
        )
        stream_two = response_two.body_iterator
        reconnect_snapshot = _parse_sse_chunk(await asyncio.wait_for(anext(stream_two), timeout=1.0))
        assert reconnect_snapshot["data"]["devices"][0]["device_id"] == "tenant-a-device"

        await broadcaster.publish(
            "tenant-b",
            "fleet_update",
            {
                "generated_at": "2026-04-03T00:00:01+00:00",
                "devices": [
                    {
                        "device_id": "tenant-b-device",
                        "device_name": "tenant-b device",
                        "device_type": "compressor",
                        "runtime_status": "running",
                    }
                ],
                "warnings": [],
                "stale": False,
                "partial": False,
                "version": 2,
            },
        )

        await broadcaster.publish(
            "tenant-a",
            "fleet_update",
            {
                "generated_at": "2026-04-03T00:00:02+00:00",
                "devices": [
                    {
                        "device_id": "tenant-a-live",
                        "device_name": "tenant-a live",
                        "device_type": "compressor",
                        "runtime_status": "running",
                    }
                ],
                "warnings": [],
                "stale": False,
                "partial": False,
                "version": 3,
            },
        )
        tenant_event = _parse_sse_chunk(await asyncio.wait_for(anext(stream_two), timeout=1.0))
        assert tenant_event["event"] == "fleet_update"
        assert tenant_event["data"]["devices"][0]["device_id"] == "tenant-a-live"
        request_two._disconnected = True
        await stream_two.aclose()
    finally:
        await broadcaster.stop()


@pytest.mark.asyncio
async def test_fleet_stream_filters_updates_to_assigned_plants(monkeypatch):
    broadcaster = FleetStreamBroadcaster(queue_size=4)
    monkeypatch.setattr("app.services.live_dashboard.LiveDashboardService", _LiveDashboardStub)
    monkeypatch.setattr(devices_api, "fleet_stream_broadcaster", broadcaster)
    monkeypatch.setattr(devices_api, "AsyncSessionLocal", _fake_session_local)
    monkeypatch.setattr(devices_api.settings, "DASHBOARD_STREAM_HEARTBEAT_SECONDS", 10)
    monkeypatch.setattr(devices_api.settings, "DASHBOARD_STREAM_SEND_TIMEOUT_SECONDS", 10)

    try:
        request = _RequestStub(
            "tenant-a",
            auth={
                "role": "plant_manager",
                "plant_ids": ["PLANT-A"],
                "tenant_id": "tenant-a",
                "user_id": "pm-1",
                "is_super_admin": False,
            },
        )
        response = await devices_api.fleet_snapshot_stream(
            request=request,
            page_size=200,
            plant_id=None,
            search=None,
            runtime_status=None,
            operational_status=None,
            last_event_id_query=None,
            last_event_id=None,
        )
        stream = response.body_iterator
        snapshot_event = _parse_sse_chunk(await asyncio.wait_for(anext(stream), timeout=1.0))
        assert snapshot_event["data"]["devices"][0]["device_id"] == "tenant-a-device"

        await broadcaster.publish(
            "tenant-a",
            "fleet_update",
            {
                "generated_at": "2026-04-03T00:00:02+00:00",
                "devices": [
                    {
                        "device_id": "tenant-a-allowed",
                        "device_name": "tenant-a allowed",
                        "device_type": "compressor",
                        "runtime_status": "running",
                        "plant_id": "PLANT-A",
                    },
                    {
                        "device_id": "tenant-a-blocked",
                        "device_name": "tenant-a blocked",
                        "device_type": "compressor",
                        "runtime_status": "running",
                        "plant_id": "PLANT-B",
                    },
                ],
                "warnings": [],
                "stale": False,
                "partial": True,
                "version": 3,
            },
        )
        tenant_event = _parse_sse_chunk(await asyncio.wait_for(anext(stream), timeout=1.0))
        assert [device["device_id"] for device in tenant_event["data"]["devices"]] == ["tenant-a-allowed"]
        request._disconnected = True
        await stream.aclose()
    finally:
        await broadcaster.stop()


@pytest.mark.asyncio
async def test_fleet_stream_filters_snapshot_and_updates_by_device_name_search(monkeypatch):
    broadcaster = FleetStreamBroadcaster(queue_size=4)
    monkeypatch.setattr("app.services.live_dashboard.LiveDashboardService", _LiveDashboardStub)
    monkeypatch.setattr(devices_api, "fleet_stream_broadcaster", broadcaster)
    monkeypatch.setattr(devices_api, "AsyncSessionLocal", _fake_session_local)
    monkeypatch.setattr(devices_api.settings, "DASHBOARD_STREAM_HEARTBEAT_SECONDS", 10)
    monkeypatch.setattr(devices_api.settings, "DASHBOARD_STREAM_SEND_TIMEOUT_SECONDS", 10)

    try:
        request = _RequestStub("tenant-a")
        response = await devices_api.fleet_snapshot_stream(
            request=request,
            page_size=200,
            plant_id=None,
            search="device",
            runtime_status=None,
            operational_status=None,
            last_event_id_query=None,
            last_event_id=None,
        )
        stream = response.body_iterator
        snapshot_event = _parse_sse_chunk(await asyncio.wait_for(anext(stream), timeout=1.0))
        assert snapshot_event["data"]["devices"][0]["device_id"] == "tenant-a-device"

        await broadcaster.publish(
            "tenant-a",
            "fleet_update",
            {
                "generated_at": "2026-04-03T00:00:02+00:00",
                "devices": [
                    {
                        "device_id": "tenant-a-match",
                        "device_name": "Assembly Device",
                        "device_type": "compressor",
                        "runtime_status": "running",
                        "operational_status": "running",
                        "plant_id": "PLANT-A",
                    },
                    {
                        "device_id": "tenant-a-skip",
                        "device_name": "Mixer Station",
                        "device_type": "compressor",
                        "runtime_status": "running",
                        "operational_status": "running",
                        "plant_id": "PLANT-A",
                    },
                ],
                "warnings": [],
                "stale": False,
                "partial": True,
                "version": 3,
            },
        )
        tenant_event = _parse_sse_chunk(await asyncio.wait_for(anext(stream), timeout=1.0))
        assert [device["device_id"] for device in tenant_event["data"]["devices"]] == ["tenant-a-match"]
        request._disconnected = True
        await stream.aclose()
    finally:
        await broadcaster.stop()


@pytest.mark.asyncio
async def test_redis_pubsub_is_isolated_per_tenant_channel(monkeypatch):
    _FakeRedis.broker = _FakeRedisBroker()
    monkeypatch.setattr("app.monitoring.Redis", _FakeRedis)

    broadcaster_one = FleetStreamBroadcaster(queue_size=4)
    broadcaster_two = FleetStreamBroadcaster(queue_size=4)

    await broadcaster_one.start("redis://fake", "factoryops:fleet_stream:{tenant_id}:v1")
    await broadcaster_two.start("redis://fake", "factoryops:fleet_stream:{tenant_id}:v1")

    subscriber_a1, queue_a1 = await broadcaster_one.subscribe("tenant-a")
    subscriber_a2, queue_a2 = await broadcaster_two.subscribe("tenant-a")
    subscriber_b2, queue_b2 = await broadcaster_two.subscribe("tenant-b")

    try:
        await broadcaster_one.publish(
            "tenant-a",
            "fleet_update",
            {"generated_at": "2026-04-03T00:00:03+00:00", "devices": [{"device_id": "tenant-a-shared"}], "warnings": [], "stale": False, "partial": False, "version": 4},
        )

        message_a1 = await asyncio.wait_for(queue_a1.get(), timeout=1.0)
        message_a2 = await asyncio.wait_for(queue_a2.get(), timeout=1.0)

        assert message_a1.data["devices"][0]["device_id"] == "tenant-a-shared"
        assert message_a2.data["devices"][0]["device_id"] == "tenant-a-shared"
        assert queue_b2.empty()
        assert "factoryops:fleet_stream:tenant-a:v1" in _FakeRedis.broker.subscriptions
        assert "factoryops:fleet_stream:tenant-b:v1" in _FakeRedis.broker.subscriptions
        assert all(":v1" in channel for channel, _ in _FakeRedis.broker.published)
        assert not any(channel == "factoryops:fleet_stream:v1" for channel, _ in _FakeRedis.broker.published)
    finally:
        await broadcaster_one.unsubscribe("tenant-a", subscriber_a1, reason="test_cleanup")
        await broadcaster_two.unsubscribe("tenant-a", subscriber_a2, reason="test_cleanup")
        await broadcaster_two.unsubscribe("tenant-b", subscriber_b2, reason="test_cleanup")
        await broadcaster_one.stop()
        await broadcaster_two.stop()
