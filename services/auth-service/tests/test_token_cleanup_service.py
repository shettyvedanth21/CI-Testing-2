import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, SERVICES_ROOT, AUTH_SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(AUTH_SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app import main as app_main
from app.rate_limit import configure_rate_limiting
from app.services.token_cleanup_service import TokenCleanupService


class FakeSelectResult:
    def __init__(self, ids):
        self._ids = list(ids)

    def scalars(self):
        return self

    def all(self):
        return list(self._ids)


class FakeDeleteResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class FakeDBSession:
    def __init__(self, batches):
        self._batches = [list(batch) for batch in batches]
        self.statements = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self._last_batch_size = 0

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        visit_name = getattr(statement, "__visit_name__", "")
        if visit_name == "select":
            batch = self._batches.pop(0) if self._batches else []
            self._last_batch_size = len(batch)
            return FakeSelectResult(batch)
        if visit_name == "delete":
            return FakeDeleteResult(self._last_batch_size)
        raise AssertionError(f"Unexpected statement type: {visit_name}")

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class FakeSessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def eval(self, script, numkeys, key, value, *args):
        if self.values.get(key) == value:
            del self.values[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_purge_refresh_tokens_batches_expired_and_revoked_rows(monkeypatch):
    session = FakeDBSession(
        batches=[
            ["expired-1", "revoked-1", "expired-2"],
            ["revoked-2"],
        ]
    )
    service = TokenCleanupService(session_factory=FakeSessionFactory(session))

    deleted = await service.purge_until_empty(session, batch_size=3)

    assert deleted == 4
    assert session.commit_calls == 2
    compiled_select = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "refresh_tokens.expires_at <" in compiled_select
    assert "refresh_tokens.revoked_at IS NOT NULL" in compiled_select


@pytest.mark.asyncio
async def test_purge_action_tokens_removes_only_rows_older_than_retention(monkeypatch):
    session = FakeDBSession(
        batches=[
            ["refresh-expired"],
            ["action-used-old", "action-expired-old"],
        ]
    )
    service = TokenCleanupService(session_factory=FakeSessionFactory(session))

    deleted = await service.purge_until_empty(session, batch_size=5)

    assert deleted == 3
    assert session.commit_calls == 2
    compiled_action_select = str(session.statements[2].compile(compile_kwargs={"literal_binds": True}))
    assert "auth_action_tokens.used_at <" in compiled_action_select
    assert "auth_action_tokens.used_at IS NULL" in compiled_action_select
    assert "auth_action_tokens.expires_at <" in compiled_action_select


@pytest.mark.asyncio
async def test_purge_action_tokens_once_skips_currently_valid_rows():
    session = FakeDBSession(batches=[[]])
    service = TokenCleanupService(session_factory=FakeSessionFactory(session))

    deleted = await service.purge_action_tokens_once(session, batch_size=10)

    assert deleted == 0
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_cleanup_lifespan_starts_background_task(monkeypatch):
    started = asyncio.Event()

    async def fake_run_forever():
        started.set()

    monkeypatch.setattr(app_main, "refresh_token_cleanup_svc", SimpleNamespace(run_forever=fake_run_forever))
    monkeypatch.setattr(app_main, "validate_startup_contract", lambda: None)
    monkeypatch.setattr(app_main, "validate_auth_email_contract", lambda: None)

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, *args, **kwargs):
            return None

    class _Engine:
        def connect(self):
            return _Conn()

        async def dispose(self):
            return None

    monkeypatch.setattr(app_main, "engine", _Engine())

    app = FastAPI()
    configure_rate_limiting(app)

    async with app_main.lifespan(app):
        await asyncio.wait_for(started.wait(), timeout=1)
