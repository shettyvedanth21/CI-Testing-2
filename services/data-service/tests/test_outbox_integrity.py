from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from types import SimpleNamespace

import httpx
import pymysql
import pytest
import pytest_asyncio
from sqlalchemy import text
from unittest.mock import AsyncMock
from fastapi.encoders import jsonable_encoder

from tests._bootstrap import bootstrap_paths

bootstrap_paths()
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")

from src.config import settings
from src.models import EnrichmentStatus, OutboxMessage, OutboxStatus, OutboxTarget, TelemetryPayload, TelemetryPoint
from src.repositories import DLQRepository, OutboxRepository
from src.repositories import outbox_repository as outbox_repo_module
from src.utils.circuit_breaker import _BREAKERS
from src.services.outbox_relay import OutboxRelayService
from src.services.reconciliation import ReconciliationService
from src.services.retention_cleanup import RetentionCleanupService
from src.services.telemetry_service import TelemetryService

_SCHEMA_READY = False


class FakeInfluxRepository:
    def __init__(self, latest_map: dict[str, TelemetryPoint] | None = None):
        self.latest_map = latest_map or {}
        self.writes: list[dict[str, Any]] = []

    def write_telemetry(self, payload) -> bool:
        self.writes.append(payload.model_dump(mode="json"))
        return True

    def get_latest_telemetry_batch(
        self,
        tenant_id: str,
        device_ids: list[str],
    ) -> dict[str, TelemetryPoint | None]:
        return {device_id: self.latest_map.get(device_id) for device_id in device_ids}

    async def async_get_latest_telemetry_batch(
        self,
        tenant_id: str,
        device_ids: list[str],
    ) -> dict[str, TelemetryPoint | None]:
        return self.get_latest_telemetry_batch(tenant_id, device_ids)

    def close(self) -> None:
        return None


class FakeEnrichmentService:
    async def enrich_telemetry(self, payload):
        return payload

    async def close(self) -> None:
        return None


class FakeRuleEngineClient:
    async def evaluate_rules(self, payload, projection_state=None) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeDeviceProjectionClient:
    async def sync_projection(self, payload) -> dict[str, Any]:
        return {
            "load_state": "idle",
            "idle_streak_started_at": payload.timestamp.isoformat(),
            "idle_streak_duration_sec": 0,
        }

    async def close(self) -> None:
        return None


def _mysql_conn():
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _reset_mysql_tables() -> None:
    with _mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reconciliation_log")
            cur.execute("DELETE FROM telemetry_outbox")
            cur.execute("DELETE FROM dlq_messages")


def _telemetry_payload(device_id: str = "DEVICE-OUTBOX-1", *, ts: datetime | None = None) -> dict[str, Any]:
    stamp = ts or datetime.now(timezone.utc)
    return {
        "device_id": device_id,
        "tenant_id": "SH00000001",
        "timestamp": stamp.isoformat(),
        "schema_version": "v1",
        "power": 120.5,
        "current": 0.8,
        "voltage": 229.7,
        "energy_kwh": 12.34,
    }


def _normalize_target(value: str) -> str:
    return value.lower().replace("_", "-")


async def _wait_for_queue(service: TelemetryService) -> None:
    await asyncio.wait_for(service._processing_queue.join(), timeout=5)
    await asyncio.sleep(0.05)


async def _wait_for_mysql_count(
    repository: OutboxRepository,
    *,
    sql: str,
    expected: int,
    timeout_seconds: float = 5.0,
) -> int:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    latest = -1
    while True:
        async with repository.session_factory() as session:
            result = await session.execute(text(sql))
            latest = int(result.mappings().one()["count"])
        if latest == expected:
            return latest
        if asyncio.get_running_loop().time() >= deadline:
            return latest
        await asyncio.sleep(0.05)


async def _run_relay_until(
    relay: OutboxRelayService,
    *,
    assertion,
    attempts: int = 20,
    pause_seconds: float = 0.1,
) -> int:
    processed_total = 0
    last_error: AssertionError | None = None
    for _ in range(attempts):
        processed_total += await relay.run_once()
        try:
            await assertion()
            return processed_total
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(pause_seconds)
    if last_error is not None:
        raise last_error
    return processed_total


@pytest.fixture
def configure_outbox_settings(monkeypatch):
    monkeypatch.setattr(settings, "device_sync_enabled", True)
    monkeypatch.setattr(settings, "energy_sync_enabled", True)
    monkeypatch.setattr(settings, "outbox_poll_interval_sec", 0.1)
    monkeypatch.setattr(settings, "outbox_batch_size", 10)
    monkeypatch.setattr(settings, "outbox_max_retries", 5)
    monkeypatch.setattr(settings, "reconciliation_drift_warn_minutes", 10)
    monkeypatch.setattr(settings, "reconciliation_drift_resync_minutes", 30)


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    _BREAKERS.clear()
    yield
    _BREAKERS.clear()


@pytest.fixture(autouse=True)
def reset_outbox_repository_singletons():
    outbox_repo_module._ENGINE = None
    outbox_repo_module._SESSION_FACTORY = None
    yield
    outbox_repo_module._ENGINE = None
    outbox_repo_module._SESSION_FACTORY = None


@pytest_asyncio.fixture
async def repositories(configure_outbox_settings):
    global _SCHEMA_READY
    outbox_repository = OutboxRepository()
    dlq_repository = DLQRepository()
    if not _SCHEMA_READY:
        await outbox_repository.ensure_schema()
        _SCHEMA_READY = True
    _reset_mysql_tables()
    try:
        yield outbox_repository, dlq_repository
    finally:
        dlq_repository.close()
        await outbox_repository.close()


@pytest.mark.asyncio
async def test_outbox_row_created_on_telemetry(repositories, monkeypatch):
    outbox_repository, dlq_repository = repositories
    influx_repository = FakeInfluxRepository()
    telemetry_service = TelemetryService(
        influx_repository=influx_repository,
        dlq_repository=dlq_repository,
        outbox_repository=outbox_repository,
        enrichment_service=FakeEnrichmentService(),
        rule_engine_client=FakeRuleEngineClient(),
        device_projection_client=FakeDeviceProjectionClient(),
    )

    async def _noop_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr("src.api.websocket.broadcast_telemetry", _noop_broadcast)
    telemetry_service._fetch_tenant_owned_device_ids = AsyncMock(return_value={"DEVICE-OUTBOX-1"})
    try:
        payload = _telemetry_payload()
        await telemetry_service._process_telemetry_async(  # noqa: SLF001
            payload=TelemetryPayload(**payload),
            correlation_id="test-outbox-row-created",
            raw_payload=payload,
        )
        await telemetry_service._process_derived_async(  # noqa: SLF001
            payload=TelemetryPayload(**payload),
            correlation_id="test-outbox-row-created",
            outbox_payload=jsonable_encoder(payload),
            projection_synced=True,
            projection_state={"load_state": "idle"},
            projection_error=None,
        )
        async with outbox_repository.session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT target FROM telemetry_outbox "
                    "WHERE status = 'pending' AND device_id = 'DEVICE-OUTBOX-1' "
                    "ORDER BY id ASC"
                )
            )
            rows = [_normalize_target(row["target"]) for row in result.mappings().all()]
        assert len(rows) == 1
        assert set(rows) == {OutboxTarget.ENERGY_SERVICE.value}
    finally:
        await telemetry_service.close()


@pytest.mark.asyncio
async def test_outbox_delivered_on_success(repositories):
    _, dlq_repository = repositories
    message = OutboxMessage(
        id=1,
        device_id="DEVICE-SUCCESS-1",
        telemetry_json=_telemetry_payload("DEVICE-SUCCESS-1"),
        target=OutboxTarget.DEVICE_SERVICE,
        status=OutboxStatus.PENDING,
        retry_count=0,
        max_retries=5,
    )

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def begin(self):
            return self

        async def flush(self) -> None:
            return None

    async def _claim_pending_batch(*, session, batch_size, backoff_base_seconds):
        del session, batch_size, backoff_base_seconds
        return [message] if message.status == OutboxStatus.PENDING else []

    async def _mark_delivered(*, session, message, delivered_at=None, flush=True):
        del session, flush
        message.status = OutboxStatus.DELIVERED
        message.delivered_at = delivered_at

    fake_repository = SimpleNamespace(
        session_factory=lambda: _FakeSession(),
        claim_pending_batch=_claim_pending_batch,
        mark_delivered=_mark_delivered,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json={"success": True})

    relay = OutboxRelayService(
        outbox_repository=fake_repository,
        dlq_repository=dlq_repository,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0),
    )
    relay._build_normalized_fields = AsyncMock(return_value=None)  # type: ignore[method-assign]
    try:
        processed = await relay.run_once()
        assert processed == 1
        assert message.status == OutboxStatus.DELIVERED
        assert message.delivered_at is not None
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_outbox_retries_on_failure(repositories):
    _, dlq_repository = repositories
    message = OutboxMessage(
        id=1,
        device_id="DEVICE-RETRY-1",
        telemetry_json=_telemetry_payload("DEVICE-RETRY-1"),
        target=OutboxTarget.DEVICE_SERVICE,
        status=OutboxStatus.PENDING,
        retry_count=0,
        max_retries=5,
    )

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def begin(self):
            return self

        async def flush(self) -> None:
            return None

    async def _claim_pending_batch(*, session, batch_size, backoff_base_seconds):
        del session, batch_size, backoff_base_seconds
        return [message] if message.status == OutboxStatus.PENDING else []

    async def _mark_retryable_failure(*, session, message, error_message, attempted_at=None, flush=True):
        del session, attempted_at, flush
        message.retry_count += 1
        message.status = OutboxStatus.FAILED
        message.error_message = error_message

    fake_repository = SimpleNamespace(
        session_factory=lambda: _FakeSession(),
        claim_pending_batch=_claim_pending_batch,
        mark_retryable_failure=_mark_retryable_failure,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=503, json={"success": False})

    relay = OutboxRelayService(
        outbox_repository=fake_repository,
        dlq_repository=dlq_repository,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0),
    )
    relay._build_normalized_fields = AsyncMock(return_value=None)  # type: ignore[method-assign]
    try:
        processed = await relay.run_once()
        assert processed == 1
        assert message.status == OutboxStatus.FAILED
        assert message.retry_count == 1
        assert message.error_message is not None
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_outbox_run_once_drains_multiple_claim_batches(repositories, monkeypatch):
    _, dlq_repository = repositories
    monkeypatch.setattr(settings, "outbox_batch_size", 10)
    monkeypatch.setattr(settings, "outbox_max_batches_per_run", 3)

    messages = [
        OutboxMessage(
            id=idx + 1,
            device_id=f"DEVICE-MULTI-{idx:03d}",
            telemetry_json=_telemetry_payload(f"DEVICE-MULTI-{idx:03d}"),
            target=OutboxTarget.DEVICE_SERVICE,
            status=OutboxStatus.PENDING,
            retry_count=0,
            max_retries=5,
        )
        for idx in range(25)
    ]
    remaining = list(messages)
    claim_sizes: list[int] = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def begin(self):
            return self

        async def flush(self) -> None:
            return None

    async def _claim_pending_batch(*, session, batch_size, backoff_base_seconds):
        del session, backoff_base_seconds
        claim_sizes.append(batch_size)
        batch = remaining[:batch_size]
        del remaining[:batch_size]
        return batch

    async def _mark_delivered(*, session, message, delivered_at=None, flush=True):
        del session, delivered_at, flush
        message.status = OutboxStatus.DELIVERED

    fake_repository = SimpleNamespace(
        session_factory=lambda: _FakeSession(),
        claim_pending_batch=_claim_pending_batch,
        mark_delivered=_mark_delivered,
    )

    relay = OutboxRelayService(
        outbox_repository=fake_repository,
        dlq_repository=dlq_repository,
    )
    relay._post_to_target = AsyncMock(  # type: ignore[method-assign]
        return_value=httpx.Response(status_code=200, json={"success": True})
    )
    try:
        processed = await relay.run_once()
        assert processed == 25
        assert claim_sizes == [10, 10, 10]
        assert all(message.status == OutboxStatus.DELIVERED for message in messages)
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_outbox_dead_after_max_retries(repositories):
    _, dlq_repository = repositories
    message = OutboxMessage(
        id=1,
        device_id="DEVICE-DEAD-1",
        telemetry_json=_telemetry_payload("DEVICE-DEAD-1"),
        target=OutboxTarget.DEVICE_SERVICE,
        status=OutboxStatus.PENDING,
        retry_count=0,
        max_retries=1,
    )
    dlq_entries: list[tuple[str, str]] = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def begin(self):
            return self

        async def flush(self) -> None:
            return None

    async def _claim_pending_batch(*, session, batch_size, backoff_base_seconds):
        del session, batch_size, backoff_base_seconds
        return [message] if message.status == OutboxStatus.PENDING else []

    async def _mark_dead(*, session, message, error_message, attempted_at=None, flush=True):
        del session, attempted_at, flush
        message.retry_count += 1
        message.status = OutboxStatus.DEAD
        message.error_message = error_message

    async def _mark_dead_without_retry_increment(*, session, message, error_message, attempted_at=None, flush=True):
        del session, attempted_at, flush
        message.status = OutboxStatus.DEAD
        message.error_message = error_message

    fake_repository = SimpleNamespace(
        session_factory=lambda: _FakeSession(),
        claim_pending_batch=_claim_pending_batch,
        mark_dead=_mark_dead,
        mark_dead_without_retry_increment=_mark_dead_without_retry_increment,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=503, text="downstream unavailable")

    relay = OutboxRelayService(
        outbox_repository=fake_repository,
        dlq_repository=dlq_repository,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0),
    )
    relay._build_normalized_fields = AsyncMock(return_value=None)  # type: ignore[method-assign]
    relay._write_dead_letter = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda *, message, error_message: dlq_entries.append((message.device_id, error_message))
    )
    try:
        processed = await relay.run_once()
        assert processed == 1
        assert message.status == OutboxStatus.DEAD
        assert message.retry_count == 1
        assert len(dlq_entries) == 1
        assert dlq_entries[0][0] == "DEVICE-DEAD-1"
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_energy_outbox_is_delivered_via_batched_request(repositories, monkeypatch):
    outbox_repository, dlq_repository = repositories
    await outbox_repository.enqueue_telemetry_batch(
        entries=[
            ("DEVICE-ENERGY-B1", _telemetry_payload("DEVICE-ENERGY-B1"), [OutboxTarget.ENERGY_SERVICE]),
            ("DEVICE-ENERGY-B2", _telemetry_payload("DEVICE-ENERGY-B2"), [OutboxTarget.ENERGY_SERVICE]),
        ],
        max_retries=5,
    )
    monkeypatch.setattr(settings, "outbox_energy_delivery_batch_size", 10)
    seen_posts: list[str] = []
    seen_update_counts: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen_posts.append(request.url.path)
            payload = json.loads(request.content.decode("utf-8"))
            seen_update_counts.append(len(payload.get("updates", [])))
            results = []
            for update in payload.get("updates", []):
                telemetry = update.get("telemetry", {})
                results.append(
                    {
                        "success": True,
                        "device_id": telemetry.get("device_id"),
                        "retryable": False,
                    }
                )
            return httpx.Response(
                status_code=200,
                json={
                    "success": True,
                    "results": results,
                },
            )
        return httpx.Response(status_code=404)

    relay = OutboxRelayService(
        outbox_repository=outbox_repository,
        dlq_repository=dlq_repository,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0),
    )
    try:
        async def _assert_energy_delivered() -> None:
            rows = [row for row in await outbox_repository.list_messages() if row.device_id.startswith("DEVICE-ENERGY-B")]
            assert len(rows) == 2
            assert {row.status for row in rows} == {OutboxStatus.DELIVERED}

        processed = await _run_relay_until(relay, assertion=_assert_energy_delivered)
        assert processed >= 2
        assert seen_posts == ["/api/v1/energy/live-update/batch"]
        assert seen_update_counts == [2]
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_energy_batch_partial_failure_isolated_per_row(repositories, monkeypatch):
    outbox_repository, dlq_repository = repositories
    await outbox_repository.enqueue_telemetry_batch(
        entries=[
            ("DEVICE-ENERGY-P1", _telemetry_payload("DEVICE-ENERGY-P1"), [OutboxTarget.ENERGY_SERVICE]),
            ("DEVICE-ENERGY-P2", _telemetry_payload("DEVICE-ENERGY-P2"), [OutboxTarget.ENERGY_SERVICE]),
        ],
        max_retries=5,
    )
    monkeypatch.setattr(settings, "outbox_energy_delivery_batch_size", 10)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = json.loads(request.content.decode("utf-8"))
            results = []
            for update in payload.get("updates", []):
                telemetry = update.get("telemetry", {})
                device_id = telemetry.get("device_id")
                if device_id == "DEVICE-ENERGY-P1":
                    results.append(
                        {
                            "success": True,
                            "device_id": device_id,
                            "retryable": False,
                        }
                    )
                else:
                    results.append(
                        {
                            "success": False,
                            "device_id": device_id,
                            "error": "bad input",
                            "error_code": "INVALID_ENERGY_PAYLOAD",
                            "retryable": False,
                        }
                    )
            return httpx.Response(
                status_code=200,
                json={
                    "success": True,
                    "results": results,
                },
            )
        return httpx.Response(status_code=404)

    relay = OutboxRelayService(
        outbox_repository=outbox_repository,
        dlq_repository=dlq_repository,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0),
    )
    try:
        async def _assert_partial_energy_outcome() -> None:
            rows = {
                row.device_id: row
                for row in await outbox_repository.list_messages()
                if row.device_id.startswith("DEVICE-ENERGY-P")
            }
            assert rows["DEVICE-ENERGY-P1"].status == OutboxStatus.DELIVERED
            assert rows["DEVICE-ENERGY-P2"].status == OutboxStatus.DEAD
            assert rows["DEVICE-ENERGY-P2"].retry_count == 0

        await _run_relay_until(relay, assertion=_assert_partial_energy_outcome)
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_reconciliation_detects_drift(repositories):
    outbox_repository, _ = repositories
    now = datetime.now(timezone.utc)
    latest_point = TelemetryPoint(
        timestamp=now,
        device_id="DEVICE-DRIFT-1",
        schema_version="v1",
        enrichment_status=EnrichmentStatus.SUCCESS,
        power=50.0,
    )
    influx_repository = FakeInfluxRepository(latest_map={"DEVICE-DRIFT-1": latest_point})

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/internal/active-tenant-ids"):
            return httpx.Response(status_code=200, json={"tenant_ids": ["SH00000001"]})
        assert request.url.path.endswith("/api/v1/devices/dashboard/fleet-snapshot")
        assert request.headers["x-internal-service"] == "data-service"
        assert request.headers["x-tenant-id"] == "SH00000001"
        assert request.url.params["page_size"] == "200"
        assert request.url.params["sort"] == "device_name"
        payload = {
            "success": True,
            "page": 1,
            "page_size": 200,
            "total_pages": 1,
            "devices": [
                {
                    "device_id": "DEVICE-DRIFT-1",
                    "last_seen_timestamp": (now - timedelta(minutes=40)).isoformat(),
                }
            ],
        }
        return httpx.Response(status_code=200, json=payload)

    service = ReconciliationService(
        influx_repository=influx_repository,
        outbox_repository=outbox_repository,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0),
    )
    try:
        await service.run_once()
        rows = [
            row
            for row in await outbox_repository.list_messages(status=OutboxStatus.PENDING)
            if row.device_id == "DEVICE-DRIFT-1"
        ]
        assert len(rows) == 2
        async with outbox_repository.session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT drift_seconds, action_taken FROM reconciliation_log "
                    "WHERE device_id = 'DEVICE-DRIFT-1' ORDER BY id DESC LIMIT 1"
                )
            )
            record = result.mappings().one()
        assert record["action_taken"] == "resync_enqueued"
        assert int(record["drift_seconds"]) >= 1800
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_no_double_delivery(repositories, monkeypatch):
    _, dlq_repository = repositories
    monkeypatch.setattr(settings, "outbox_batch_size", 2)
    monkeypatch.setattr(settings, "outbox_max_batches_per_run", 1)
    monkeypatch.setattr(settings, "outbox_poll_interval_sec", 0.01)
    delivery_counts: Counter[str] = Counter()

    messages = [
        OutboxMessage(
            id=index + 1,
            device_id=f"DEVICE-LOCK-{index}",
            telemetry_json=_telemetry_payload(f"DEVICE-LOCK-{index}"),
            target=OutboxTarget.DEVICE_SERVICE,
            status=OutboxStatus.PENDING,
            retry_count=0,
            max_retries=5,
        )
        for index in range(6)
    ]
    remaining = list(messages)
    delivered: list[OutboxMessage] = []
    claim_lock = asyncio.Lock()

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def begin(self):
            return self

        async def flush(self) -> None:
            return None

    async def _claim_pending_batch(*, session, batch_size, backoff_base_seconds):
        del session, backoff_base_seconds
        async with claim_lock:
            batch = remaining[:batch_size]
            del remaining[:batch_size]
            return batch

    async def _mark_delivered(*, session, message, delivered_at=None, flush=True):
        del session, delivered_at, flush
        async with claim_lock:
            message.status = OutboxStatus.DELIVERED
            delivered.append(message)

    fake_repository = SimpleNamespace(
        session_factory=lambda: _FakeSession(),
        ensure_schema=AsyncMock(return_value=None),
        claim_pending_batch=_claim_pending_batch,
        mark_delivered=_mark_delivered,
    )

    async def deliver_once(*, message, tenant_id):  # type: ignore[no-untyped-def]
        del tenant_id
        delivery_counts[message.device_id] += 1
        await asyncio.sleep(0.05)
        return httpx.Response(status_code=200, json={"success": True})
    relay_one = OutboxRelayService(
        outbox_repository=fake_repository,
        dlq_repository=dlq_repository,
    )
    relay_two = OutboxRelayService(
        outbox_repository=fake_repository,
        dlq_repository=dlq_repository,
    )
    relay_one._post_to_target = AsyncMock(side_effect=deliver_once)  # type: ignore[method-assign]
    relay_two._post_to_target = AsyncMock(side_effect=deliver_once)  # type: ignore[method-assign]

    try:
        await relay_one.start()
        await relay_two.start()
        async def _wait_for_deliveries() -> int:
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                async with claim_lock:
                    delivered_count = len(delivered)
                if delivered_count == 6:
                    return delivered_count
                if asyncio.get_running_loop().time() >= deadline:
                    return delivered_count
                await asyncio.sleep(0.05)

        delivered_count = await _wait_for_deliveries()
        assert delivered_count == 6
        relevant_counts = {
            device_id: count
            for device_id, count in delivery_counts.items()
            if device_id.startswith("DEVICE-LOCK-")
        }
        assert all(count == 1 for count in relevant_counts.values())
        assert len(relevant_counts) == 6
    finally:
        await relay_one.stop()
        await relay_two.stop()


@pytest.mark.asyncio
async def test_retention_cleanup_purges_old_operational_rows(repositories, monkeypatch):
    outbox_repository, dlq_repository = repositories
    old_ts = datetime.utcnow() - timedelta(days=30)
    recent_ts = datetime.utcnow()
    monkeypatch.setattr(settings, "outbox_delivered_retention_days", 7)
    monkeypatch.setattr(settings, "outbox_dead_retention_days", 14)
    monkeypatch.setattr(settings, "reconciliation_log_retention_days", 14)
    monkeypatch.setattr(settings, "dlq_retention_days", 14)

    async with outbox_repository.session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO telemetry_outbox
                      (device_id, telemetry_json, target, status, retry_count, max_retries, created_at, delivered_at, last_attempted_at)
                    VALUES
                      ('OLD-DELIVERED', '{}', 'device-service', 'delivered', 0, 5, :old_ts, :old_ts, :old_ts),
                      ('OLD-DEAD', '{}', 'device-service', 'dead', 5, 5, :old_ts, NULL, :old_ts),
                      ('RECENT-DELIVERED', '{}', 'device-service', 'delivered', 0, 5, :recent_ts, :recent_ts, :recent_ts),
                      ('OLD-PENDING', '{}', 'device-service', 'pending', 0, 5, :old_ts, NULL, NULL)
                    """
                ),
                {"old_ts": old_ts, "recent_ts": recent_ts},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO reconciliation_log
                      (device_id, checked_at, action_taken)
                    VALUES ('OLD-RECON', :old_ts, 'noop')
                    """
                ),
                {"old_ts": old_ts},
            )

    with _mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dlq_messages
                  (timestamp, error_type, error_message, retry_count, original_payload, status, created_at)
                VALUES
                  (%s, 'parse_error', 'old', 0, JSON_OBJECT('device_id', 'OLD-DLQ'), 'pending', %s),
                  (%s, 'parse_error', 'recent', 0, JSON_OBJECT('device_id', 'RECENT-DLQ'), 'pending', %s)
                """,
                (old_ts, old_ts, recent_ts, recent_ts),
            )

    cleanup = RetentionCleanupService(
        outbox_repository=outbox_repository,
        dlq_repository=dlq_repository,
        interval_seconds=3600,
        batch_size=100,
    )
    counts = await cleanup.run_once()

    assert counts["telemetry_outbox_delivered"] == 1
    assert counts["telemetry_outbox_dead"] == 1
    assert counts["reconciliation_log"] == 1
    assert counts["dlq_messages"] == 1

    async with outbox_repository.session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT device_id FROM telemetry_outbox
                WHERE device_id IN ('OLD-DEAD', 'OLD-DELIVERED', 'RECENT-DELIVERED', 'OLD-PENDING')
                ORDER BY device_id ASC
                """
            )
        )
        remaining_outbox = [row["device_id"] for row in result.mappings().all()]
    assert remaining_outbox == ["OLD-PENDING", "RECENT-DELIVERED"]

    with _mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT JSON_UNQUOTE(JSON_EXTRACT(original_payload, '$.device_id')) AS device_id
                FROM dlq_messages
                WHERE JSON_UNQUOTE(JSON_EXTRACT(original_payload, '$.device_id')) IN ('OLD-DLQ', 'RECENT-DLQ')
                """
            )
            remaining_dlq = [row["device_id"] for row in cur.fetchall()]
    assert remaining_dlq == ["RECENT-DLQ"]


@pytest.mark.asyncio
async def test_retention_cleanup_reclassifies_non_retryable_pending_dlq_rows(repositories, monkeypatch):
    outbox_repository, dlq_repository = repositories
    now = datetime.utcnow()
    monkeypatch.setattr(settings, "dlq_retryable_error_types", ["parse_error"])

    with _mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dlq_messages
                  (timestamp, error_type, error_message, retry_count, original_payload, status, created_at)
                VALUES
                  (%s, 'rule_engine_circuit_open', 'circuit open', 0, JSON_OBJECT('device_id', 'NONRETRY-DLQ'), 'pending', %s),
                  (%s, 'parse_error', 'retryable', 0, JSON_OBJECT('device_id', 'RETRYABLE-DLQ'), 'pending', %s)
                """,
                (now, now, now, now),
            )

    cleanup = RetentionCleanupService(
        outbox_repository=outbox_repository,
        dlq_repository=dlq_repository,
        interval_seconds=3600,
        batch_size=100,
    )
    counts = await cleanup.run_once()

    assert counts["dlq_reclassified_non_retryable"] == 1

    with _mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  JSON_UNQUOTE(JSON_EXTRACT(original_payload, '$.device_id')) AS device_id,
                  status
                FROM dlq_messages
                WHERE JSON_UNQUOTE(JSON_EXTRACT(original_payload, '$.device_id')) IN ('NONRETRY-DLQ', 'RETRYABLE-DLQ')
                ORDER BY device_id ASC
                """
            )
            rows = cur.fetchall()

    by_device = {row["device_id"]: row["status"] for row in rows}
    assert by_device["NONRETRY-DLQ"] == "dead"
    assert by_device["RETRYABLE-DLQ"] == "pending"
