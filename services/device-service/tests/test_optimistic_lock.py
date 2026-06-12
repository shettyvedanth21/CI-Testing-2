from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


DEVICE_SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(DEVICE_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(DEVICE_SERVICE_DIR))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://energy:energy@127.0.0.1:3306/ai_factoryops")
os.environ.setdefault("DATA_SERVICE_BASE_URL", "http://127.0.0.1:8081")
os.environ.setdefault("RULE_ENGINE_SERVICE_BASE_URL", "http://127.0.0.1:8002")
os.environ.setdefault("REPORTING_SERVICE_BASE_URL", "http://127.0.0.1:8085")
os.environ.setdefault("ENERGY_SERVICE_BASE_URL", "http://127.0.0.1:8010")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")

from app.services.live_projection import (
    update_live_state_with_expected_version,
    update_live_state_with_lock,
)


class FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeUpdateResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class SharedRowStore:
    def __init__(self, *, device_id: str = "DEVICE-1", tenant_id: str = "ORG-1", version: int = 0):
        self.device_id = device_id
        self.tenant_id = tenant_id
        self.rows = {
            (tenant_id, device_id): {
                "version": version,
                "health_score": 0.0,
            }
        }
        self.history: list[int] = []
        self.lock = asyncio.Lock()


class FakeAsyncSession:
    def __init__(
        self,
        store: SharedRowStore,
        *,
        forced_conflicts: int = 0,
        always_conflict: bool = False,
    ):
        self.store = store
        self.forced_conflicts = forced_conflicts
        self.always_conflict = always_conflict
        self.rollback = AsyncMock()

    async def execute(self, statement):
        sql = str(statement)
        params = statement.compile().params
        device_id = params.get("device_id_1", self.store.device_id)
        tenant_id = params.get("tenant_id_1", self.store.tenant_id)
        key = (tenant_id, device_id)

        if sql.lstrip().upper().startswith("SELECT"):
            row = self.store.rows.get(key)
            return FakeScalarResult(None if row is None else row["version"])

        if sql.lstrip().upper().startswith("UPDATE"):
            expected_version = params.get("version_2", params.get("version_1"))
            async with self.store.lock:
                row = self.store.rows.get(key)
                if row is None:
                    return FakeUpdateResult(0)
                if self.always_conflict:
                    return FakeUpdateResult(0)
                if self.forced_conflicts > 0:
                    self.forced_conflicts -= 1
                    return FakeUpdateResult(0)
                if row["version"] != expected_version:
                    return FakeUpdateResult(0)

                for key, value in params.items():
                    if key in {"device_id_1", "version_1", "version_2", "updated_at"}:
                        continue
                    row[key] = value
                row["version"] += 1
                self.store.history.append(row["version"])
                return FakeUpdateResult(1)

        raise AssertionError(f"Unexpected SQL statement: {sql}")


@pytest.mark.asyncio
async def test_concurrent_writers_no_lost_update():
    store = SharedRowStore(version=0)

    async def worker(index: int) -> bool:
        session = FakeAsyncSession(store)
        return await update_live_state_with_lock(
            session,
            store.device_id,
            store.tenant_id,
            {"health_score": float(index)},
            max_retries=20,
            retry_delay_ms=0,
        )

    with patch("app.services.live_projection.asyncio.sleep", new=AsyncMock()):
        results = await asyncio.gather(*(worker(i) for i in range(10)))

    assert all(results)
    assert store.rows[(store.tenant_id, store.device_id)]["version"] == 10
    assert store.history == list(range(1, 11))


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt():
    store = SharedRowStore(version=0)
    session = FakeAsyncSession(store, forced_conflicts=1)

    with (
        patch("app.services.live_projection.asyncio.sleep", new=AsyncMock()) as sleep_mock,
        patch("app.services.live_projection.logger.warning") as warning_mock,
    ):
        result = await update_live_state_with_lock(
            session,
            store.device_id,
            store.tenant_id,
            {"health_score": 42.0},
            max_retries=3,
            retry_delay_ms=50,
        )

    assert result is True
    assert store.rows[(store.tenant_id, store.device_id)]["version"] == 1
    assert store.rows[(store.tenant_id, store.device_id)]["health_score"] == 42.0
    session.rollback.assert_awaited_once()
    sleep_mock.assert_awaited_once()
    warning_mock.assert_called_once()


@pytest.mark.asyncio
async def test_gives_up_after_max_retries():
    store = SharedRowStore(version=0)
    session = FakeAsyncSession(store, always_conflict=True)

    with patch("app.services.live_projection.asyncio.sleep", new=AsyncMock()), patch(
        "app.services.live_projection.logger"
    ) as mock_logger:
        result = await update_live_state_with_lock(
            session,
            store.device_id,
            store.tenant_id,
            {"health_score": 7.0},
            max_retries=3,
            retry_delay_ms=0,
        )

    assert result is False
    assert store.rows[(store.tenant_id, store.device_id)]["version"] == 0
    assert mock_logger.error.called


@pytest.mark.asyncio
async def test_version_increments_monotonically():
    store = SharedRowStore(version=0)

    for index in range(100):
        session = FakeAsyncSession(store)
        result = await update_live_state_with_lock(
            session,
            store.device_id,
            store.tenant_id,
            {"health_score": float(index)},
            max_retries=3,
            retry_delay_ms=0,
        )
        assert result is True

    assert store.rows[(store.tenant_id, store.device_id)]["version"] == 100
    assert store.history == list(range(1, 101))


@pytest.mark.asyncio
async def test_expected_version_update_succeeds_without_retry_cycle():
    store = SharedRowStore(version=4)
    session = FakeAsyncSession(store)

    result = await update_live_state_with_expected_version(
        session,
        device_id=store.device_id,
        tenant_id=store.tenant_id,
        expected_version=4,
        updates={"health_score": 99.0},
    )

    assert result is True
    assert store.rows[(store.tenant_id, store.device_id)]["version"] == 5
    assert store.rows[(store.tenant_id, store.device_id)]["health_score"] == 99.0
    session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_expected_version_update_detects_conflict_without_rollback():
    store = SharedRowStore(version=5)
    session = FakeAsyncSession(store)

    result = await update_live_state_with_expected_version(
        session,
        device_id=store.device_id,
        tenant_id=store.tenant_id,
        expected_version=4,
        updates={"health_score": 11.0},
    )

    assert result is False
    assert store.rows[(store.tenant_id, store.device_id)]["version"] == 5
    assert store.rows[(store.tenant_id, store.device_id)]["health_score"] == 0.0
    session.rollback.assert_not_called()
