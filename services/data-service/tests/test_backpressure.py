from __future__ import annotations

from collections import defaultdict
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._bootstrap import bootstrap_paths

bootstrap_paths()
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
from src.services.telemetry_service import (
    QUEUE_OVERFLOW_COUNTER,
    QUEUE_OVERFLOW_TOTAL,
    TelemetryService,
)
import src.main as main_module


class _FakeStageQueue:
    def __init__(self, *, fail_publish: bool = False) -> None:
        self.fail_publish = fail_publish
        self.published: list[dict[str, object]] = []
        self.counters: dict[str, int] = defaultdict(int)

    async def ensure_groups(self) -> None:
        return None

    async def publish(self, *, stage, payload, correlation_id: str, attempt: int = 1) -> str:
        if self.fail_publish:
            raise RuntimeError("ingest stream backlog threshold reached")
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

    async def metrics(self) -> dict[str, object]:
        return {
            "dead_letter_depth": 0,
            "stages": {
                "ingest": {"backlog_depth": len(self.published), "oldest_age_seconds": 0.0},
                "projection": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
                "derived": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
            },
        }

    async def incr_counter(self, key: str, amount: int = 1) -> int:
        self.counters[key] += amount
        return self.counters[key]

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def reset_queue_overflow_counter():
    QUEUE_OVERFLOW_COUNTER.clear()
    yield
    QUEUE_OVERFLOW_COUNTER.clear()


def _telemetry_payload(device_id: str, *, timestamp: datetime | None = None) -> dict[str, object]:
    stamp = timestamp or datetime.now(timezone.utc)
    return {
        "device_id": device_id,
        "timestamp": stamp.isoformat(),
        "schema_version": "v1",
        "power": 123.4,
        "current": 1.2,
        "voltage": 229.8,
    }


def _build_service(*, fail_publish: bool = False) -> tuple[TelemetryService, MagicMock, _FakeStageQueue]:
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

    stage_queue = _FakeStageQueue(fail_publish=fail_publish)
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


@pytest.mark.asyncio
async def test_acceptance_appends_to_durable_ingest_stream():
    service, _, stage_queue = _build_service()

    accepted = await service.process_telemetry_message(_telemetry_payload("DEVICE-BP-1"))

    assert accepted is True
    assert len(stage_queue.published) == 1
    published = stage_queue.published[0]
    assert published["stage"].value == "ingest"
    assert published["payload"]["raw_payload"]["device_id"] == "DEVICE-BP-1"


@pytest.mark.asyncio
async def test_overflow_writes_to_dlq():
    service, dlq_repository, _ = _build_service(fail_publish=True)

    accepted = await service.process_telemetry_message(_telemetry_payload("DEVICE-BP-2"))

    assert accepted is False
    dlq_repository.send.assert_called_once()
    call_kwargs = dlq_repository.send.call_args.kwargs
    assert call_kwargs["error_type"] == "QUEUE_OVERFLOW"
    assert call_kwargs["original_payload"]["device_id"] == "DEVICE-BP-2"


@pytest.mark.asyncio
async def test_overflow_increments_counter():
    service, dlq_repository, _ = _build_service(fail_publish=True)

    for _ in range(5):
        accepted = await service.process_telemetry_message(_telemetry_payload("DEVICE-BP-3"))
        assert accepted is False

    assert QUEUE_OVERFLOW_COUNTER["ingest"] == 5
    assert dlq_repository.send.call_count == 5
    metric_value = QUEUE_OVERFLOW_TOTAL.labels(
        queue_name="ingest",
        device_id="DEVICE-BP-3",
    )._value.get()
    assert metric_value == 5


@pytest.mark.asyncio
async def test_invalid_payload_is_rejected_before_stream_append():
    service, dlq_repository, stage_queue = _build_service()

    accepted = await service.process_telemetry_message({"device_id": "DEVICE-BP-4"})

    assert accepted is False
    assert stage_queue.published == []
    dlq_repository.send.assert_called_once()


@pytest.mark.asyncio
async def test_health_reports_overloaded_when_energy_backlog_exceeds_threshold(monkeypatch):
    fake_service = MagicMock()
    fake_service._refresh_stage_metrics = AsyncMock(return_value=None)
    fake_service.outbox_repository.get_status_counts = AsyncMock(
        return_value={"pending": 10, "failed": 0, "delivered": 0, "dead": 0}
    )
    fake_service.get_operational_stats.return_value = {
        "stages": {
            "projection": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
            "energy": {"backlog_depth": 6000, "oldest_age_seconds": 10.0},
            "rules": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
        },
        "workers": {"worker-1": {"ready": True}},
    }
    monkeypatch.setattr(main_module.app_state, "telemetry_service", fake_service)
    monkeypatch.setattr(main_module.app_state, "dlq_retry_service", None)
    monkeypatch.setattr(main_module.settings, "telemetry_energy_overload_threshold", 5000)

    payload = await main_module.health()

    assert payload["status"] == "overloaded"
    assert payload["telemetry_policy"]["state"] == "overloaded"
    assert "energy_backlog_exceeded" in payload["telemetry_policy"]["reasons"]


@pytest.mark.asyncio
async def test_health_reports_degraded_when_dlq_pending_warning_exceeded(monkeypatch):
    fake_service = MagicMock()
    fake_service._refresh_stage_metrics = AsyncMock(return_value=None)
    fake_service.outbox_repository.get_status_counts = AsyncMock(
        return_value={"pending": 0, "failed": 0, "delivered": 0, "dead": 0}
    )
    fake_service.get_operational_stats.return_value = {
        "stages": {
            "projection": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
            "energy": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
            "rules": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
        },
        "workers": {"worker-1": {"ready": True}},
        "dlq": {"backlog_count": 25, "pending_non_retryable_count": 0},
    }
    monkeypatch.setattr(main_module.app_state, "telemetry_service", fake_service)
    monkeypatch.setattr(main_module.app_state, "dlq_retry_service", None)
    monkeypatch.setattr(main_module.settings, "dlq_pending_warn_threshold", 20)
    monkeypatch.setattr(main_module.settings, "dlq_pending_overload_threshold", 50)

    payload = await main_module.health()

    assert payload["status"] == "degraded"
    assert payload["telemetry_policy"]["state"] == "degraded"
    assert "dlq_pending_warning" in payload["telemetry_policy"]["reasons"]


@pytest.mark.asyncio
async def test_health_reports_degraded_when_non_retryable_pending_rows_present(monkeypatch):
    fake_service = MagicMock()
    fake_service._refresh_stage_metrics = AsyncMock(return_value=None)
    fake_service.outbox_repository.get_status_counts = AsyncMock(
        return_value={"pending": 0, "failed": 0, "delivered": 0, "dead": 0}
    )
    fake_service.get_operational_stats.return_value = {
        "stages": {
            "projection": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
            "energy": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
            "rules": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
        },
        "workers": {"worker-1": {"ready": True}},
        "dlq": {"backlog_count": 0, "pending_non_retryable_count": 7},
    }
    monkeypatch.setattr(main_module.app_state, "telemetry_service", fake_service)
    monkeypatch.setattr(main_module.app_state, "dlq_retry_service", None)
    monkeypatch.setattr(main_module.settings, "dlq_non_retryable_pending_warn_threshold", 5)

    payload = await main_module.health()

    assert payload["status"] == "degraded"
    assert payload["telemetry_policy"]["state"] == "degraded"
    assert "dlq_non_retryable_pending_present" in payload["telemetry_policy"]["reasons"]
