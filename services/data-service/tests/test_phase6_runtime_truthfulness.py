from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tests._bootstrap import bootstrap_paths

bootstrap_paths()
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from src.main import health as health_endpoint
from src.main import app_state, settings
from src.models import Base, OutboxStatus, OutboxTarget
from src.repositories.outbox_repository import OutboxRepository
from src.repositories import outbox_repository as repo_module


def _raise_influx_ping() -> bool:
    raise RuntimeError("influx connection detail")


@pytest.mark.asyncio
async def test_health_reports_degraded_when_redis_influx_and_mqtt_are_unavailable(monkeypatch):
    fake_service = MagicMock()
    fake_service._refresh_stage_metrics = AsyncMock(side_effect=RuntimeError("redis queue unavailable"))
    fake_service.outbox_repository.get_status_counts = AsyncMock(side_effect=RuntimeError("redis outbox unavailable"))
    fake_service.get_operational_stats.return_value = {
        "stages": {
            "projection": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
            "energy": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
            "rules": {"backlog_depth": 0, "oldest_age_seconds": 0.0},
        },
        "workers": {"worker-1": {"ready": True}},
        "dlq": {"backlog_count": 0, "pending_non_retryable_count": 0},
    }
    fake_service.influx_repository = SimpleNamespace(client=SimpleNamespace(ping=_raise_influx_ping))

    monkeypatch.setattr(app_state, "telemetry_service", fake_service)
    monkeypatch.setattr(app_state, "dlq_retry_service", None)
    monkeypatch.setattr(app_state, "mqtt_handler", SimpleNamespace(is_connected=False))
    monkeypatch.setattr(settings, "app_role", "api")

    payload = await health_endpoint()

    assert payload["status"] == "degraded"
    assert payload["dependencies"]["redis"]["status"] == "unavailable"
    assert payload["dependencies"]["influxdb"]["status"] == "unavailable"
    assert payload["dependencies"]["mqtt"]["status"] == "disconnected"
    assert payload["dependencies"]["redis"]["error"] == "dependency_check_failed"
    assert payload["dependencies"]["influxdb"]["error"] == "dependency_check_failed"
    assert set(payload["dependency_reasons"]) == {
        "redis_unavailable",
        "influx_unavailable",
        "mqtt_disconnected",
    }
    assert "influx_unavailable" in payload["telemetry_policy"]["reasons"]
    assert "mqtt_disconnected" in payload["telemetry_policy"]["reasons"]


@pytest.mark.asyncio
async def test_health_reports_healthy_when_dependencies_are_connected(monkeypatch):
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
        "dlq": {"backlog_count": 0, "pending_non_retryable_count": 0},
    }
    fake_service.influx_repository = SimpleNamespace(client=SimpleNamespace(ping=lambda: True))

    monkeypatch.setattr(app_state, "telemetry_service", fake_service)
    monkeypatch.setattr(app_state, "dlq_retry_service", None)
    monkeypatch.setattr(app_state, "mqtt_handler", SimpleNamespace(is_connected=True))
    monkeypatch.setattr(settings, "app_role", "api")

    payload = await health_endpoint()

    assert payload["status"] == "healthy"
    assert payload["dependencies"]["redis"]["status"] == "connected"
    assert payload["dependencies"]["influxdb"]["status"] == "connected"
    assert payload["dependencies"]["mqtt"]["status"] == "connected"
    assert payload["dependency_reasons"] == []


class _FailingSession:
    def __init__(self) -> None:
        self.begin_called = False
        self.rollback_called = False
        self.close_called = False

    async def begin(self) -> None:
        self.begin_called = True

    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("insert failed")

    async def rollback(self) -> None:
        self.rollback_called = True

    async def close(self) -> None:
        self.close_called = True


class _SessionFactory:
    def __init__(self, session: _FailingSession) -> None:
        self._session = session

    def __call__(self) -> _FailingSession:
        return self._session


@pytest_asyncio.fixture
async def sqlite_outbox_repo(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(repo_module, "get_async_engine", lambda: engine)
    repo = OutboxRepository(session_factory=session_factory)
    try:
        yield repo
    finally:
        await repo.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_telemetry_batch_rolls_back_and_closes_session_on_failure(monkeypatch):
    failing_session = _FailingSession()
    monkeypatch.setattr(repo_module, "get_async_engine", lambda: SimpleNamespace())
    repo = OutboxRepository(session_factory=_SessionFactory(failing_session))

    with pytest.raises(RuntimeError, match="insert failed"):
        await repo.enqueue_telemetry_batch(
            entries=[
                (
                    "DEVICE-PHASE6-1",
                    {
                        "device_id": "DEVICE-PHASE6-1",
                        "tenant_id": "SH00000001",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    [OutboxTarget.ENERGY_SERVICE],
                )
            ],
            max_retries=5,
        )

    assert failing_session.begin_called is True
    assert failing_session.rollback_called is True
    assert failing_session.close_called is True


@pytest.mark.asyncio
async def test_enqueue_telemetry_batch_persists_all_rows_with_pending_status(sqlite_outbox_repo):
    await sqlite_outbox_repo.enqueue_telemetry_batch(
        entries=[
            (
                "DEVICE-BATCH-1",
                {
                    "device_id": "DEVICE-BATCH-1",
                    "tenant_id": "SH00000001",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                [OutboxTarget.ENERGY_SERVICE],
            ),
            (
                "DEVICE-BATCH-2",
                {
                    "device_id": "DEVICE-BATCH-2",
                    "tenant_id": "SH00000001",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                [OutboxTarget.DEVICE_SERVICE, OutboxTarget.ENERGY_SERVICE],
            ),
        ],
        max_retries=7,
    )

    rows = await sqlite_outbox_repo.list_messages()
    counts = await sqlite_outbox_repo.get_status_counts()

    assert len(rows) == 3
    assert {row.status for row in rows} == {OutboxStatus.PENDING}
    assert [row.device_id for row in rows] == ["DEVICE-BATCH-1", "DEVICE-BATCH-2", "DEVICE-BATCH-2"]
    assert all(int(row.max_retries) == 7 for row in rows)
    assert counts == {
        "pending": 3,
        "failed": 0,
        "delivered": 0,
        "dead": 0,
    }
