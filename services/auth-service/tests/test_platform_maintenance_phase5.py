from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "auth-service"
SERVICES_ROOT = ROOT / "services"
for path in (ROOT, SERVICES_ROOT, SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.api.v1.admin import router as admin_router
from app.api.v1 import admin as admin_api
from app.database import get_db
from app.dependencies import get_token_claims
from app.models.auth import PlatformMaintenanceSeverity, PlatformMaintenanceStatus, User, UserRole
from app.rate_limit import configure_rate_limiting
from app.services.token_service import TokenService

UTC = timezone.utc


class FakeRedisPipeline:
    def __init__(self, redis_client):
        self._redis = redis_client
        self._ops = []

    def set(self, key, value, ex=None):
        self._ops.append(lambda: self._redis.set(key, value, ex=ex))
        return self

    def sadd(self, key, value):
        self._ops.append(lambda: self._redis.sadd(key, value))
        return self

    def expire(self, key, ttl):
        self._ops.append(lambda: self._redis.expire(key, ttl))
        return self

    def execute(self):
        return [op() for op in self._ops]


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.sets = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)
        return 1

    def expire(self, key, ttl):
        self.ttls[key] = int(ttl)
        return True

    def pipeline(self):
        return FakeRedisPipeline(self)


class FakeDBSession:
    pass


def _make_user(*, user_id: str, tenant_id: str | None = None, role: UserRole) -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id,
        tenant_id=tenant_id,
        email=f"{user_id}@example.com",
        hashed_password="hashed",
        full_name="Test User",
        role=role,
        permissions_version=0,
        is_active=True,
        activated_at=now,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )


def _make_announcement(
    *,
    announcement_id: str,
    tenant_ids: list[str],
    broadcast_all_tenants: bool = False,
    starts_at: datetime | None = None,
    duration_minutes: int = 60,
):
    starts = starts_at or datetime.now(UTC) + timedelta(hours=1)
    return SimpleNamespace(
        id=announcement_id,
        title="Scheduled maintenance",
        severity=PlatformMaintenanceSeverity.WARNING,
        message="Planned platform maintenance window.",
        starts_at=starts,
        estimated_duration_minutes=duration_minutes,
        ends_at=starts + timedelta(minutes=duration_minutes),
        status=PlatformMaintenanceStatus.SCHEDULED,
        broadcast_all_tenants=broadcast_all_tenants,
        targets=[SimpleNamespace(tenant_id=tenant_id) for tenant_id in tenant_ids],
        created_by_user_id="super-1",
        updated_by_user_id="super-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def client(monkeypatch):
    app = FastAPI()
    configure_rate_limiting(app)
    app.include_router(admin_router)

    async def _override_get_db():
        yield FakeDBSession()

    async def _override_get_token_claims(request: Request):
        auth_header = request.headers.get("authorization")
        assert auth_header and auth_header.lower().startswith("bearer ")
        token = auth_header.split(" ", 1)[1].strip()
        return TokenService().decode_access_token(token)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_token_claims] = _override_get_token_claims

    fake_redis = FakeRedis()
    monkeypatch.setattr(TokenService, "_get_redis_client", lambda self: fake_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as async_client:
        yield async_client

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_platform_maintenance_create_rejects_overlapping_targeted_window(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    future_start = (datetime.now(UTC) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    monkeypatch.setattr(
        admin_api.org_repo,
        "list_by_ids",
        AsyncMock(return_value=[SimpleNamespace(id="SH00000001", is_active=True)]),
    )
    monkeypatch.setattr(
        admin_api.platform_maintenance_repo,
        "list_overlapping_announcements",
        AsyncMock(return_value=[_make_announcement(announcement_id="pm-existing", tenant_ids=["SH00000001"])]),
    )
    create_mock = AsyncMock()
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "create", create_mock)

    response = await client.post(
        "/api/admin/platform-maintenance",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "title": "Planned maintenance",
            "severity": "warning",
            "message": "Maintenance message",
            "starts_at": future_start,
            "estimated_duration_minutes": 60,
            "status": "scheduled",
            "broadcast_all_tenants": False,
            "target_tenant_ids": ["SH00000001"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLATFORM_MAINTENANCE_OVERLAP"
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_maintenance_update_rejects_overlapping_window(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    future_start = (datetime.now(UTC) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    existing = _make_announcement(announcement_id="pm-current", tenant_ids=["SH00000001"])

    monkeypatch.setattr(admin_api.platform_maintenance_repo, "get_by_id", AsyncMock(return_value=existing))
    monkeypatch.setattr(
        admin_api.org_repo,
        "list_by_ids",
        AsyncMock(return_value=[SimpleNamespace(id="SH00000001", is_active=True)]),
    )
    monkeypatch.setattr(
        admin_api.platform_maintenance_repo,
        "list_overlapping_announcements",
        AsyncMock(return_value=[_make_announcement(announcement_id="pm-other", tenant_ids=["SH00000001"])]),
    )
    update_mock = AsyncMock()
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "update", update_mock)

    response = await client.patch(
        "/api/admin/platform-maintenance/pm-current",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "starts_at": future_start,
            "estimated_duration_minutes": 90,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLATFORM_MAINTENANCE_OVERLAP"
    update_mock.assert_not_awaited()
