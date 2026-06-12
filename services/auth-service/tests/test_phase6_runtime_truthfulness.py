from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, SERVICES_ROOT, AUTH_SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(AUTH_SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app import main as app_main
from app.database import Base
from app.models.auth import Organization, Plant, User, UserPlantAccess, UserRole
from app.repositories.user_repository import UserRepository
from app.services.token_cleanup_service import TokenCleanupService


class _FakeConnect:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *_args, **_kwargs):
        return None


class _BrokenConnect(_FakeConnect):
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("database down")


class _HealthyRedis:
    def ping(self) -> bool:
        return True


class _BrokenRedis:
    def ping(self) -> bool:
        raise RedisError("redis down")


class _LoopBreak(Exception):
    pass


@pytest.mark.asyncio
async def test_health_reports_degraded_when_database_and_redis_are_unavailable(monkeypatch):
    monkeypatch.setattr(app_main, "engine", SimpleNamespace(connect=lambda: _BrokenConnect()))
    monkeypatch.setattr(
        app_main.refresh_token_cleanup_svc,
        "_get_redis_client",
        lambda: _BrokenRedis(),
    )

    payload = await app_main.health()

    assert payload["status"] == "degraded"
    assert payload["dependencies"]["database"]["status"] == "unavailable"
    assert payload["dependencies"]["redis"]["status"] == "unavailable"
    assert payload["dependencies"]["database"]["error"] == "dependency_check_failed"
    assert payload["dependencies"]["redis"]["error"] == "dependency_check_failed"
    assert set(payload["reasons"]) == {"database_unavailable", "redis_unavailable"}


@pytest.mark.asyncio
async def test_health_reports_ok_when_database_and_redis_are_available(monkeypatch):
    monkeypatch.setattr(app_main, "engine", SimpleNamespace(connect=lambda: _FakeConnect()))
    monkeypatch.setattr(
        app_main.refresh_token_cleanup_svc,
        "_get_redis_client",
        lambda: _HealthyRedis(),
    )

    payload = await app_main.health()

    assert payload["status"] == "ok"
    assert payload["dependencies"]["database"]["status"] == "connected"
    assert payload["dependencies"]["redis"]["status"] == "connected"
    assert payload["reasons"] == []


@pytest.mark.asyncio
async def test_run_forever_skips_cycle_when_redis_lock_is_unavailable(monkeypatch):
    service = TokenCleanupService()
    run_cycle_called = False

    def _raise_redis():
        raise RedisError("redis unavailable")

    async def fake_sleep(_seconds: int) -> None:
        raise _LoopBreak()

    async def fake_run_cycle(*_args, **_kwargs):
        nonlocal run_cycle_called
        run_cycle_called = True
        return 1

    monkeypatch.setattr(service, "_acquire_lock", _raise_redis)
    monkeypatch.setattr(service, "run_cycle", fake_run_cycle)

    with pytest.raises(_LoopBreak):
        await service.run_forever(sleep=fake_sleep, batch_size=1)

    assert run_cycle_called is False


@pytest.mark.asyncio
async def test_concurrent_plant_access_updates_do_not_duplicate_rows(tmp_path):
    db_path = tmp_path / "auth-phase6-access.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        session.add(Organization(id="SH00000001", name="Tenant", slug="tenant"))
        session.add_all(
            [
                Plant(id="plant-a", tenant_id="SH00000001", name="Plant A", timezone="Asia/Kolkata"),
                Plant(id="plant-b", tenant_id="SH00000001", name="Plant B", timezone="Asia/Kolkata"),
                User(
                    id="user-1",
                    tenant_id="SH00000001",
                    email="user@example.com",
                    hashed_password="hashed",
                    role=UserRole.PLANT_MANAGER,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    repo = UserRepository()

    async def _assign(plant_ids: list[str]) -> None:
        async with session_factory() as session:
            await repo.set_plant_access(session, "user-1", plant_ids)
            await session.commit()

    await asyncio.gather(
        _assign(["plant-a", "plant-b", "plant-b"]),
        _assign(["plant-b", "plant-a"]),
    )

    async with session_factory() as session:
        result = await session.execute(
            select(UserPlantAccess.plant_id).where(UserPlantAccess.user_id == "user-1")
        )
        plant_ids = sorted(result.scalars().all())

    assert plant_ids == ["plant-a", "plant-b"]
    await engine.dispose()
