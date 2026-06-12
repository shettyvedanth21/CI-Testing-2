from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys
from unittest.mock import AsyncMock

import pytest

from tests._bootstrap import bootstrap_paths

bootstrap_paths()

from src.config import settings
from src.models import EnrichmentStatus, TelemetryPayload
from src.queue import QueueMessage, TelemetryStage
from src.services.telemetry_service import TelemetryService
from src.workers.telemetry_pipeline import TelemetryPipelineWorker


class _FakeInfluxRepository:
    def __init__(self) -> None:
        self.writes: list[TelemetryPayload] = []

    def write_telemetry(self, payload) -> bool:
        self.writes.append(payload)
        return True

    def close(self) -> None:
        return None


class _FakeDlqRepository:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def send(self, **kwargs) -> None:
        self.messages.append(kwargs)
        return None

    def get_operational_stats(self) -> dict[str, Any]:
        return {}

    def close(self) -> None:
        return None


class _FakeOutboxRepository:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    async def ensure_schema(self) -> None:
        return None

    async def enqueue_telemetry(self, **kwargs) -> None:
        self.enqueued.append(kwargs)

    async def enqueue_telemetry_batch(self, *, entries, max_retries=None, session=None) -> None:
        del max_retries, session
        self.enqueued.append({"batch": list(entries)})

    async def close(self) -> None:
        return None


class _FakeEnrichmentService:
    async def enrich_telemetry(self, payload: TelemetryPayload) -> TelemetryPayload:
        payload.tenant_id = payload.tenant_id or "TENANT-A"
        payload.enrichment_status = EnrichmentStatus.SUCCESS
        return payload

    async def close(self) -> None:
        return None


class _FakeProjectionClient:
    def __init__(self, order: list[str], *, fail: bool = False, batch_results: list[dict[str, Any]] | None = None) -> None:
        self.order = order
        self.fail = fail
        self.batch_results = batch_results

    @staticmethod
    def _resolve_tenant_id(payload: TelemetryPayload) -> str:
        return str(payload.tenant_id or "TENANT-A")

    async def sync_projection_batch(self, payloads: list[TelemetryPayload]) -> list[dict[str, Any]]:
        self.order.append("projection")
        if self.batch_results is not None:
            return self.batch_results
        if self.fail:
            return [
                {
                    "success": False,
                    "error": "projection unavailable",
                    "retryable": True,
                }
                for _ in payloads
            ]
        return [
            {
                "success": True,
                "device": {
                    "load_state": "idle",
                    "idle_streak_started_at": payload.timestamp.isoformat(),
                    "idle_streak_duration_sec": 40 * 60,
                },
                "retryable": False,
            }
            for payload in payloads
        ]

    async def close(self) -> None:
        return None


class _FakeRuleEngineClient:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[dict[str, Any]] = []

    async def evaluate_rules(self, payload: TelemetryPayload, projection_state=None) -> None:
        self.order.append("rules")
        self.calls.append(
            {
                "device_id": payload.device_id,
                "projection_state": projection_state,
            }
        )

    async def close(self) -> None:
        return None


class _FakeStageQueue:
    def __init__(self) -> None:
        self.messages: dict[TelemetryStage, list[QueueMessage]] = {
            TelemetryStage.INGEST: [],
            TelemetryStage.PROJECTION: [],
            TelemetryStage.BROADCAST: [],
            TelemetryStage.ENERGY: [],
            TelemetryStage.RULES: [],
        }
        self._counter = 0
        self.dead_lettered: list[dict[str, Any]] = []

    async def ensure_groups(self) -> None:
        return None

    async def publish(self, *, stage, payload, correlation_id: str, attempt: int = 1) -> str:
        self._counter += 1
        payload_dict = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        message = QueueMessage(
            stage=stage,
            message_id=f"{self._counter}-0",
            attempt=attempt,
            correlation_id=correlation_id,
            enqueued_at=datetime.now(timezone.utc),
            payload=payload_dict,
        )
        self.messages[stage].append(message)
        return message.message_id

    async def ack(self, *, stage: TelemetryStage, message_ids: list[str]) -> None:
        ids = set(message_ids)
        self.messages[stage] = [message for message in self.messages[stage] if message.message_id not in ids]

    async def dead_letter(self, *, stage: TelemetryStage, message: QueueMessage, reason: str, terminal_payload=None) -> None:
        self.dead_lettered.append(
            {
                "stage": stage,
                "message_id": message.message_id,
                "reason": reason,
                "terminal_payload": terminal_payload or message.payload,
            }
        )
        await self.ack(stage=stage, message_ids=[message.message_id])

    async def read_batch(self, *, stage: TelemetryStage, consumer_name: str, batch_size: int, block_ms: int):
        del consumer_name, batch_size, block_ms
        return []

    async def reclaim_stale(self, *, stage: TelemetryStage, consumer_name: str, min_idle_ms: int, batch_size: int):
        del consumer_name, min_idle_ms, batch_size
        return []

    async def metrics(self) -> dict[str, Any]:
        return {
            "dead_letter_depth": 0,
            "stages": {
                stage.value: {"backlog_depth": len(items), "oldest_age_seconds": 0.0}
                for stage, items in self.messages.items()
            },
            "workers": {},
        }

    async def incr_counter(self, name: str, amount: int = 1) -> None:
        del name, amount
        return None

    async def record_worker_health(self, **kwargs) -> None:
        return None

    async def close(self) -> None:
        return None


def _payload() -> dict[str, Any]:
    return {
        "device_id": "DEVICE-ORDER-1",
        "tenant_id": "TENANT-A",
        "timestamp": datetime(2026, 4, 12, 10, 40, 0, tzinfo=timezone.utc).isoformat(),
        "schema_version": "v1",
        "current": 2.0,
        "voltage": 230.0,
        "power": 460.0,
    }


def _build_service(*, projection_fail: bool = False):
    order: list[str] = []
    outbox = _FakeOutboxRepository()
    rule_engine = _FakeRuleEngineClient(order)
    stage_queue = _FakeStageQueue()
    service = TelemetryService(
        influx_repository=_FakeInfluxRepository(),
        dlq_repository=_FakeDlqRepository(),
        outbox_repository=outbox,
        enrichment_service=_FakeEnrichmentService(),
        rule_engine_client=rule_engine,
        device_projection_client=_FakeProjectionClient(order, fail=projection_fail),
        stage_queue=stage_queue,
    )
    service._fetch_tenant_owned_device_ids = AsyncMock(return_value={"DEVICE-ORDER-1"})
    worker = TelemetryPipelineWorker(service, stage_queue=stage_queue, consumer_name="test-worker")
    return service, worker, stage_queue, rule_engine, outbox, order


def _build_service_with_projection_results(batch_results: list[dict[str, Any]]):
    order: list[str] = []
    outbox = _FakeOutboxRepository()
    rule_engine = _FakeRuleEngineClient(order)
    stage_queue = _FakeStageQueue()
    service = TelemetryService(
        influx_repository=_FakeInfluxRepository(),
        dlq_repository=_FakeDlqRepository(),
        outbox_repository=outbox,
        enrichment_service=_FakeEnrichmentService(),
        rule_engine_client=rule_engine,
        device_projection_client=_FakeProjectionClient(order, batch_results=batch_results),
        stage_queue=stage_queue,
    )
    service._fetch_tenant_owned_device_ids = AsyncMock(return_value={"DEVICE-ORDER-1", "DEVICE-ORDER-2", "DEVICE-ORDER-3"})
    worker = TelemetryPipelineWorker(service, stage_queue=stage_queue, consumer_name="test-worker")
    return service, worker, stage_queue, rule_engine, outbox, order


@pytest.mark.asyncio
async def test_rule_evaluation_runs_after_projection_sync(monkeypatch):
    monkeypatch.setattr(settings, "device_sync_enabled", True)
    monkeypatch.setattr(settings, "energy_sync_enabled", False)
    service, worker, stage_queue, rule_engine, outbox, order = _build_service()

    async def _noop_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr("src.api.websocket.broadcast_telemetry", _noop_broadcast)
    accepted = await service.process_telemetry_message(_payload())
    assert accepted is True

    ingest = stage_queue.messages[TelemetryStage.INGEST].pop(0)
    await worker._handle_message(stage=TelemetryStage.INGEST, message=ingest)
    await worker._handle_projection_batch(list(stage_queue.messages[TelemetryStage.PROJECTION]))
    await worker._handle_broadcast_batch(list(stage_queue.messages[TelemetryStage.BROADCAST]))
    await worker._handle_rules_batch(list(stage_queue.messages[TelemetryStage.RULES]))

    assert order == ["projection", "rules"]
    assert len(rule_engine.calls) == 1
    assert rule_engine.calls[0]["projection_state"]["idle_streak_duration_sec"] == 40 * 60
    assert len(outbox.enqueued) == 0


@pytest.mark.asyncio
async def test_rule_evaluation_is_skipped_when_projection_sync_fails(monkeypatch):
    monkeypatch.setattr(settings, "device_sync_enabled", True)
    monkeypatch.setattr(settings, "energy_sync_enabled", False)
    service, worker, stage_queue, rule_engine, outbox, order = _build_service(projection_fail=True)

    async def _noop_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr("src.api.websocket.broadcast_telemetry", _noop_broadcast)
    accepted = await service.process_telemetry_message(_payload())
    assert accepted is True

    ingest = stage_queue.messages[TelemetryStage.INGEST].pop(0)
    await worker._handle_message(stage=TelemetryStage.INGEST, message=ingest)
    await worker._handle_projection_batch(list(stage_queue.messages[TelemetryStage.PROJECTION]))
    await worker._handle_energy_batch(list(stage_queue.messages[TelemetryStage.ENERGY]))
    await worker._handle_rules_batch(list(stage_queue.messages[TelemetryStage.RULES]))

    assert order == ["projection"]
    assert rule_engine.calls == []
    assert outbox.enqueued == []
    assert len(stage_queue.messages[TelemetryStage.PROJECTION]) == 1


@pytest.mark.asyncio
async def test_nonexistent_device_is_rejected_before_influx_and_outbox(monkeypatch):
    monkeypatch.setattr(settings, "device_sync_enabled", True)
    monkeypatch.setattr(settings, "energy_sync_enabled", True)
    influx = _FakeInfluxRepository()
    dlq = _FakeDlqRepository()
    outbox = _FakeOutboxRepository()
    service = TelemetryService(
        influx_repository=influx,
        dlq_repository=dlq,
        outbox_repository=outbox,
        enrichment_service=_FakeEnrichmentService(),
        rule_engine_client=_FakeRuleEngineClient([]),
        device_projection_client=_FakeProjectionClient([]),
        stage_queue=_FakeStageQueue(),
    )
    service._fetch_tenant_owned_device_ids = AsyncMock(return_value=set())

    await service._process_telemetry_async(
        payload=TelemetryPayload(**_payload()),
        correlation_id="ownership-missing",
        raw_payload=_payload(),
    )

    assert influx.writes == []
    assert outbox.enqueued == []
    assert dlq.messages[-1]["error_type"] == "device_ownership_error"


@pytest.mark.asyncio
async def test_wrong_tenant_device_is_rejected_before_downstream_churn(monkeypatch):
    monkeypatch.setattr(settings, "device_sync_enabled", True)
    monkeypatch.setattr(settings, "energy_sync_enabled", True)
    influx = _FakeInfluxRepository()
    dlq = _FakeDlqRepository()
    outbox = _FakeOutboxRepository()
    service = TelemetryService(
        influx_repository=influx,
        dlq_repository=dlq,
        outbox_repository=outbox,
        enrichment_service=_FakeEnrichmentService(),
        rule_engine_client=_FakeRuleEngineClient([]),
        device_projection_client=_FakeProjectionClient([]),
        stage_queue=_FakeStageQueue(),
    )
    service._fetch_tenant_owned_device_ids = AsyncMock(return_value={"OTHER-DEVICE"})

    await service._process_telemetry_async(
        payload=TelemetryPayload(**_payload()),
        correlation_id="ownership-wrong-tenant",
        raw_payload=_payload(),
    )

    assert influx.writes == []
    assert outbox.enqueued == []
    assert dlq.messages[-1]["error_type"] == "device_ownership_error"


@pytest.mark.asyncio
async def test_projection_batch_isolates_nonretryable_and_retryable_item_failures(monkeypatch):
    monkeypatch.setattr(settings, "device_sync_enabled", True)
    monkeypatch.setattr(settings, "energy_sync_enabled", True)
    batch_results = [
        {
            "success": True,
            "device": {"load_state": "running", "version": 1},
            "retryable": False,
        },
        {
            "success": False,
            "error": "phase_type must be 'single', 'three', or null",
            "error_code": "INVALID_DEVICE_METADATA",
            "error_category": "invalid_device_metadata",
            "retryable": False,
        },
        {
            "success": False,
            "error": "projection overloaded",
            "error_code": "DEVICE_PROJECTION_OVERLOADED",
            "error_category": "downstream_overload",
            "retryable": True,
        },
    ]
    service, worker, stage_queue, rule_engine, outbox, order = _build_service_with_projection_results(batch_results)

    async def _noop_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr("src.api.websocket.broadcast_telemetry", _noop_broadcast)

    for idx in range(1, 4):
        payload = _payload() | {"device_id": f"DEVICE-ORDER-{idx}"}
        accepted = await service.process_telemetry_message(payload)
        assert accepted is True
        ingest = stage_queue.messages[TelemetryStage.INGEST].pop(0)
        await worker._handle_message(stage=TelemetryStage.INGEST, message=ingest)

    projection_messages = list(stage_queue.messages[TelemetryStage.PROJECTION])
    assert len(projection_messages) == 3

    await worker._handle_projection_batch(projection_messages)

    assert order == ["projection"]
    assert len(stage_queue.messages[TelemetryStage.PROJECTION]) == 1
    assert len(stage_queue.messages[TelemetryStage.BROADCAST]) == 2
    assert len(stage_queue.messages[TelemetryStage.ENERGY]) == 2
    assert len(stage_queue.messages[TelemetryStage.RULES]) == 2

    await worker._handle_energy_batch(list(stage_queue.messages[TelemetryStage.ENERGY]))
    await worker._handle_rules_batch(list(stage_queue.messages[TelemetryStage.RULES]))

    assert len(rule_engine.calls) == 1
    assert rule_engine.calls[0]["device_id"] == "DEVICE-ORDER-1"
    assert len(outbox.enqueued) == 1
    batch = outbox.enqueued[0]["batch"]
    assert {entry[0] for entry in batch} == {"DEVICE-ORDER-1", "DEVICE-ORDER-2"}


@pytest.mark.asyncio
async def test_projection_overload_is_deferred_before_dead_letter(monkeypatch):
    monkeypatch.setattr(settings, "device_sync_enabled", True)
    monkeypatch.setattr(settings, "energy_sync_enabled", False)
    monkeypatch.setattr(settings, "telemetry_projection_overload_max_defers", 3)
    monkeypatch.setattr(settings, "telemetry_projection_defer_base_seconds", 0.01)
    monkeypatch.setattr(settings, "telemetry_projection_defer_max_seconds", 0.02)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("src.workers.telemetry_pipeline.asyncio.sleep", _no_sleep)
    batch_results = [
        {
            "success": False,
            "error": "projection overloaded",
            "error_code": "DEVICE_PROJECTION_OVERLOADED",
            "error_category": "downstream_overload",
            "retryable": True,
        },
    ]
    _service, worker, stage_queue, _rule_engine, _outbox, _order = _build_service_with_projection_results(batch_results)
    message = QueueMessage(
        stage=TelemetryStage.PROJECTION,
        message_id="seed-0",
        attempt=1,
        correlation_id="corr-1",
        enqueued_at=datetime.now(timezone.utc),
        payload={
            "payload": _payload() | {"device_id": "DEVICE-ORDER-9"},
            "raw_payload": _payload() | {"device_id": "DEVICE-ORDER-9"},
            "outbox_payload": _payload() | {"device_id": "DEVICE-ORDER-9"},
            "correlation_id": "corr-1",
        },
    )
    stage_queue.messages[TelemetryStage.PROJECTION].append(message)

    await worker._handle_projection_batch([message])

    assert stage_queue.dead_lettered == []
    assert len(stage_queue.messages[TelemetryStage.PROJECTION]) == 1
    deferred = stage_queue.messages[TelemetryStage.PROJECTION][0]
    assert deferred.attempt == 1
    assert int(deferred.payload["_projection_defer_count"]) == 1


@pytest.mark.asyncio
async def test_projection_overload_hits_dlq_after_defer_limit(monkeypatch):
    monkeypatch.setattr(settings, "device_sync_enabled", True)
    monkeypatch.setattr(settings, "energy_sync_enabled", False)
    monkeypatch.setattr(settings, "telemetry_projection_overload_max_defers", 1)
    monkeypatch.setattr(settings, "telemetry_projection_defer_base_seconds", 0.01)
    monkeypatch.setattr(settings, "telemetry_projection_defer_max_seconds", 0.02)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("src.workers.telemetry_pipeline.asyncio.sleep", _no_sleep)
    batch_results = [
        {
            "success": False,
            "error": "transport timeout",
            "error_code": "DEVICE_PROJECTION_TRANSPORT_ERROR",
            "error_category": "transient_dependency_failure",
            "retryable": True,
        },
    ]
    _service, worker, stage_queue, _rule_engine, _outbox, _order = _build_service_with_projection_results(batch_results)
    message = QueueMessage(
        stage=TelemetryStage.PROJECTION,
        message_id="seed-1",
        attempt=1,
        correlation_id="corr-2",
        enqueued_at=datetime.now(timezone.utc),
        payload={
            "payload": _payload() | {"device_id": "DEVICE-ORDER-10"},
            "raw_payload": _payload() | {"device_id": "DEVICE-ORDER-10"},
            "outbox_payload": _payload() | {"device_id": "DEVICE-ORDER-10"},
            "correlation_id": "corr-2",
            "_projection_defer_count": 1,
        },
    )
    stage_queue.messages[TelemetryStage.PROJECTION].append(message)

    await worker._handle_projection_batch([message])

    assert len(stage_queue.dead_lettered) == 1
    assert stage_queue.dead_lettered[0]["stage"] == TelemetryStage.PROJECTION
    assert "projection_defer_limit_exceeded" in stage_queue.dead_lettered[0]["reason"]
