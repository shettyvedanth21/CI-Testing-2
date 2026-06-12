import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.mqtt_handler import MQTTHandler

GENERATED_DEVICE_ID = "AD00000042"


class _FakeClient:
    def __init__(self, handler: MQTTHandler, succeed_after: int = 1):
        self._handler = handler
        self._succeed_after = succeed_after
        self.calls = 0
        self.subscriptions = []
        self.connected = False

    def connect(self, host: str, port: int, keepalive: int) -> None:
        self.calls += 1
        self.connected = True
        if self.calls >= self._succeed_after:
            self._handler._on_connect(self, None, {}, 0)
            return
        raise RuntimeError("temporary reconnect failure")

    def loop_start(self) -> None:
        return None

    def loop_stop(self) -> None:
        self.connected = False

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(self, topic):
        self.subscriptions.append(topic)
        return 0, 1


@pytest.mark.asyncio
async def test_schedule_reconnect_single_flight():
    handler = MQTTHandler(telemetry_service=None)
    handler._loop = asyncio.get_running_loop()

    async def slow_reconnect():
        await asyncio.sleep(0.05)

    handler._reconnect_loop = slow_reconnect  # type: ignore[assignment]

    handler._schedule_reconnect()
    handler._schedule_reconnect()
    await asyncio.sleep(0.01)
    first_task = handler._reconnect_task
    assert first_task is not None

    handler._schedule_reconnect()
    await asyncio.sleep(0.01)
    assert handler._reconnect_task is first_task

    await first_task


@pytest.mark.asyncio
async def test_reconnect_loop_retries_until_connected(monkeypatch):
    handler = MQTTHandler(telemetry_service=None)
    handler._connected = False
    handler._shutdown_requested = False
    handler._reconnect_interval = 0
    fake_client = _FakeClient(handler, succeed_after=3)
    monkeypatch.setattr(handler, "_build_client", lambda: setattr(handler, "client", fake_client))
    monkeypatch.setattr("src.handlers.mqtt_handler.random.uniform", lambda _a, _b: 0.0)

    await asyncio.wait_for(handler._reconnect_loop(), timeout=3)
    assert handler._connected is True
    assert isinstance(handler.client, _FakeClient)
    assert handler.client.calls == 3


def test_on_message_rejects_topic_payload_mismatch():
    handler = MQTTHandler(telemetry_service=None)
    msg = SimpleNamespace(
        topic="devices/DEVICE-1/telemetry",
        qos=1,
        payload=b'{"device_id":"DEVICE-2","power":12.3}',
    )

    # Should return without raising and without scheduling work.
    handler._on_message(client=None, userdata=None, msg=msg)  # type: ignore[arg-type]


def test_extract_device_id_from_new_topic_format():
    tenant_id, device_id = MQTTHandler._extract_device_id_from_topic(
        "SH00000001/devices/DEVICE-1/telemetry"
    )
    assert tenant_id == "SH00000001"
    assert device_id == "DEVICE-1"


def test_extract_device_id_from_legacy_topic_format():
    tenant_id, device_id = MQTTHandler._extract_device_id_from_topic(
        "devices/DEVICE-1/telemetry"
    )
    assert tenant_id is None
    assert device_id == "DEVICE-1"


def test_extract_device_id_from_topic_accepts_generated_prefixed_device_id():
    tenant_id, device_id = MQTTHandler._extract_device_id_from_topic(
        f"SH00000001/devices/{GENERATED_DEVICE_ID}/telemetry"
    )
    assert tenant_id == "SH00000001"
    assert device_id == GENERATED_DEVICE_ID


def test_on_message_passes_canonical_tenant_id_through(monkeypatch):
    telemetry_service = MagicMock()
    telemetry_service.process_telemetry_message = AsyncMock(return_value=True)
    handler = MQTTHandler(telemetry_service=telemetry_service)
    captured = {}

    def fake_run_coroutine_threadsafe(coro, loop):
        captured["coro"] = coro

        class _DummyFuture:
            def add_done_callback(self, callback):
                self._callback = callback

        return _DummyFuture()

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe)

    msg = SimpleNamespace(
        topic="devices/DEVICE-1/telemetry",
        qos=1,
        payload=b'{"device_id":"DEVICE-1","tenant_id":"SH00000001","power":12.3}',
    )

    handler._loop = object()  # type: ignore[assignment]
    handler._on_message(client=None, userdata=None, msg=msg)  # type: ignore[arg-type]

    coro = captured["coro"]
    asyncio.run(coro)

    assert telemetry_service.process_telemetry_message.await_count == 1
    call_kwargs = telemetry_service.process_telemetry_message.await_args.kwargs
    assert call_kwargs["raw_payload"]["tenant_id"] == "SH00000001"


def test_on_message_accepts_legacy_topic_without_tenant_and_defers_resolution(monkeypatch):
    telemetry_service = MagicMock()
    telemetry_service.process_telemetry_message = AsyncMock(return_value=True)
    handler = MQTTHandler(telemetry_service=telemetry_service)
    captured = {}

    def fake_run_coroutine_threadsafe(coro, loop):
        captured["coro"] = coro

        class _DummyFuture:
            def add_done_callback(self, callback):
                self._callback = callback

        return _DummyFuture()

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe)

    msg = SimpleNamespace(
        topic="devices/DEVICE-1/telemetry",
        qos=1,
        payload=b'{"device_id":"DEVICE-1","power":12.3}',
    )

    handler._loop = object()  # type: ignore[assignment]
    handler._on_message(client=None, userdata=None, msg=msg)  # type: ignore[arg-type]

    coro = captured["coro"]
    asyncio.run(coro)

    assert telemetry_service.process_telemetry_message.await_count == 1
    call_kwargs = telemetry_service.process_telemetry_message.await_args.kwargs
    assert call_kwargs["raw_payload"]["device_id"] == "DEVICE-1"
    assert "tenant_id" not in call_kwargs["raw_payload"]


def test_subscription_includes_legacy_and_tenant_topics(monkeypatch):
    handler = MQTTHandler(telemetry_service=None)
    fake_client = _FakeClient(handler)
    handler.client = fake_client  # type: ignore[assignment]

    handler._on_connect(client=fake_client, userdata=None, flags={}, rc=0)  # type: ignore[arg-type]

    assert fake_client.subscriptions
    assert set(fake_client.subscriptions[0]) == {
        ("devices/+/telemetry", 1),
        ("+/devices/+/telemetry", 1),
    }
