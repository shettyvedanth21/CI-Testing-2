from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path = [p for p in sys.path if p not in {str(REPO_ROOT), str(SERVICES_ROOT), str(AUTH_SERVICE_ROOT)}]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SERVICES_ROOT))
sys.path.insert(0, str(AUTH_SERVICE_ROOT))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.models.auth import User, UserRole
from app.services import auth_service


class FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, pending_invite_id=None):
        self.pending_invite_id = pending_invite_id
        self.execute_calls = 0
        self.executed = []
        self.added = []
        self.flushed = 0

    async def execute(self, statement, *args, **kwargs):
        self.execute_calls += 1
        self.executed.append(statement)
        if self.execute_calls == 1:
            return FakeResult(self.pending_invite_id)
        return FakeResult()

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        self.flushed += 1


class FakeTokenService:
    def generate_refresh_token_pair(self):
        return "raw-refresh-token", "hashed-refresh-token"

    def create_access_token(self, user, plant_ids, *, tenant_entitlements_version=0):  # noqa: ANN001
        return "access-token"

    async def create_access_token_async(self, user, plant_ids, *, tenant_entitlements_version=0):  # noqa: ANN001
        return self.create_access_token(
            user,
            plant_ids,
            tenant_entitlements_version=tenant_entitlements_version,
        )

    async def store_refresh_token(self, db, user_id, token_hash):  # noqa: ANN001
        db.add(SimpleNamespace(user_id=user_id, token_hash=token_hash))
        await db.flush()


def _make_user() -> User:
    now = datetime.now(timezone.utc)
    return User(
        id="USER-1",
        tenant_id=None,
        email="manash.ray@cittagent.com",
        hashed_password="hashed-password",
        full_name="Shivex Super-Admin",
        role=UserRole.SUPER_ADMIN,
        permissions_version=0,
        is_active=True,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )


@pytest.mark.asyncio
async def test_successful_login_updates_last_login_at(monkeypatch):
    fake_db = FakeDB()
    user = _make_user()
    fake_token_service = FakeTokenService()
    password = "Shivex@2706"

    monkeypatch.setattr(auth_service.user_repo, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(auth_service.pwd_ctx, "verify", lambda secret, hashed: True)
    monkeypatch.setattr(auth_service, "token_svc", fake_token_service)

    returned_user, token_response = await auth_service.AuthService().login(fake_db, user.email, password)

    assert returned_user.id == user.id
    assert token_response.access_token == "access-token"
    assert token_response.refresh_token == "raw-refresh-token"
    assert user.last_login_at is not None
    assert fake_db.flushed == 2


@pytest.mark.asyncio
async def test_failed_login_does_not_update_last_login_at(monkeypatch):
    fake_db = FakeDB()
    user = _make_user()

    monkeypatch.setattr(auth_service.user_repo, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(auth_service.pwd_ctx, "verify", lambda secret, hashed: False)

    with pytest.raises(Exception) as exc:
        await auth_service.AuthService().login(fake_db, user.email, "wrong-password")

    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "INVALID_CREDENTIALS"
    assert user.last_login_at is None
    assert fake_db.flushed == 0
