from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._bootstrap import bootstrap_paths

bootstrap_paths()

from src.handlers.mqtt_handler import MQTTHandler
from src.services.telemetry_service import TelemetryService
from src.utils.validation import TelemetryValidator


class _FakeStageQueue:
    def __init__(self) -> None:
        self.published: list[dict[str, object]] = []
        self.counters: dict[str, int] = defaultdict(int)

    async def ensure_groups(self) -> None:
        return None

    async def publish(self, *, stage, payload, correlation_id: str, attempt: int = 1) -> str:
        payload_dict = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        self.published.append(
            {
                "stage": stage,
                "payload": payload_dict,
                "correlation_id": correlation_id,
                "attempt": attempt,
            }
        )
        return "1-0"

    async def incr_counter(self, key: str, amount: int = 1) -> int:
        self.counters[key] += amount
        return self.counters[key]

    async def metrics(self) -> dict[str, object]:
        return {}

    async def close(self) -> None:
        return None


def _build_service() -> tuple[TelemetryService, MagicMock, _FakeStageQueue]:
    influx_repository = MagicMock()
    influx_repository.close = MagicMock()

    dlq_repository = MagicMock()
    dlq_repository.get_operational_stats = MagicMock(return_value={})

    outbox_repository = MagicMock()
    outbox_repository.ensure_schema = AsyncMock(return_value=None)
    outbox_repository.close = AsyncMock(return_value=None)

    enrichment_service = MagicMock()
    enrichment_service.close = AsyncMock(return_value=None)

    rule_engine_client = MagicMock()
    rule_engine_client.close = AsyncMock(return_value=None)

    device_projection_client = MagicMock()
    device_projection_client.close = AsyncMock(return_value=None)

    stage_queue = _FakeStageQueue()
    service = TelemetryService(
        influx_repository=influx_repository,
        dlq_repository=dlq_repository,
        outbox_repository=outbox_repository,
        enrichment_service=enrichment_service,
        rule_engine_client=rule_engine_client,
        device_projection_client=device_projection_client,
        stage_queue=stage_queue,
    )
    return service, dlq_repository, stage_queue


def _future_payload(*, seconds_ahead: int = 301) -> dict[str, object]:
    return {
        "device_id": "DEVICE-FUTURE-1",
        "tenant_id": "tenant-a",
        "timestamp": (datetime.now(timezone.utc) + timedelta(seconds=seconds_ahead)).isoformat(),
        "power": 120.0,
        "current": 1.0,
        "voltage": 230.0,
    }


def test_validator_rejects_timestamp_that_is_too_far_in_future(monkeypatch):
    monkeypatch.setattr("src.utils.validation.settings.telemetry_max_future_skew_seconds", 60)

    is_valid, error_type, error_message = TelemetryValidator.validate_payload(_future_payload(seconds_ahead=120))

    assert is_valid is False
    assert error_type == "invalid_timestamp"
    assert error_message == "Timestamp is too far in the future"


@pytest.mark.asyncio
async def test_process_telemetry_message_rejects_future_timestamp_before_stream_append(monkeypatch):
    monkeypatch.setattr("src.utils.validation.settings.telemetry_max_future_skew_seconds", 60)
    service, dlq_repository, stage_queue = _build_service()

    accepted = await service.process_telemetry_message(_future_payload(seconds_ahead=120))

    assert accepted is False
    assert stage_queue.published == []
    dlq_repository.send.assert_called_once()
    assert dlq_repository.send.call_args.kwargs["error_type"] == "invalid_timestamp"


def test_mqtt_handler_drops_malformed_json_without_scheduling_processing():
    telemetry_service = MagicMock()
    telemetry_service.process_telemetry_message = AsyncMock(return_value=True)
    handler = MQTTHandler(telemetry_service=telemetry_service)
    handler._loop = object()  # type: ignore[assignment]
    msg = SimpleNamespace(
        topic="tenant-a/devices/DEVICE-1/telemetry",
        qos=1,
        payload=b"{bad-json",
    )

    handler._on_message(client=None, userdata=None, msg=msg)  # type: ignore[arg-type]

    assert telemetry_service.process_telemetry_message.await_count == 0
