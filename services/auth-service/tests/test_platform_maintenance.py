import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
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

from app.api.v1.admin import router as admin_router
from app.api.v1 import admin as admin_api
from app.api.v1.platform_maintenance import router as platform_maintenance_router
from app.api.v1 import platform_maintenance as platform_maintenance_api
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
    announcement_id: str = "pm-1",
    title: str = "Scheduled database maintenance",
    tenant_ids: list[str] | None = None,
    broadcast_all_tenants: bool = False,
    status: PlatformMaintenanceStatus = PlatformMaintenanceStatus.SCHEDULED,
    starts_at: datetime | None = None,
    duration_minutes: int = 90,
):
    starts = starts_at or datetime.now(UTC) + timedelta(hours=1)
    return SimpleNamespace(
        id=announcement_id,
        title=title,
        severity=PlatformMaintenanceSeverity.WARNING,
        message="Planned platform maintenance window.",
        starts_at=starts,
        estimated_duration_minutes=duration_minutes,
        ends_at=starts + timedelta(minutes=duration_minutes),
        status=status,
        broadcast_all_tenants=broadcast_all_tenants,
        targets=[SimpleNamespace(tenant_id=tenant_id) for tenant_id in (tenant_ids or [])],
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
    app.include_router(platform_maintenance_router)

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
async def test_platform_maintenance_create_requires_super_admin(client):
    tenant_admin = _make_user(user_id="org-admin-1", tenant_id="SH00000001", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(tenant_admin, [])

    response = await client.post(
        "/api/admin/platform-maintenance",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "title": "Planned maintenance",
            "severity": "warning",
            "message": "Maintenance message",
            "starts_at": "2026-05-01T12:00:00Z",
            "estimated_duration_minutes": 60,
            "status": "scheduled",
            "broadcast_all_tenants": True,
            "target_tenant_ids": [],
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_platform_maintenance_update_requires_super_admin(client):
    tenant_admin = _make_user(user_id="org-admin-2", tenant_id="SH00000001", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(tenant_admin, [])

    response = await client.patch(
        "/api/admin/platform-maintenance/pm-123",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"status": "cancelled"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_platform_maintenance_create_validates_target_tenants(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    future_start = (datetime.now(UTC) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    monkeypatch.setattr(admin_api.org_repo, "list_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "list_overlapping_announcements", AsyncMock(return_value=[]))
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
            "target_tenant_ids": ["SH00000009"],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TARGET_TENANT_NOT_FOUND"
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_maintenance_create_rejects_inactive_target_tenants(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    future_start = (datetime.now(UTC) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    monkeypatch.setattr(
        admin_api.org_repo,
        "list_by_ids",
        AsyncMock(return_value=[SimpleNamespace(id="SH00000009", is_active=False)]),
    )
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "list_overlapping_announcements", AsyncMock(return_value=[]))
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
            "target_tenant_ids": ["SH00000009"],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TARGET_TENANT_INACTIVE"
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_maintenance_create_persists_relational_targets(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    created = _make_announcement(tenant_ids=["SH00000002", "SH00000001"])
    future_start = (datetime.now(UTC) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    monkeypatch.setattr(
        admin_api.org_repo,
        "list_by_ids",
        AsyncMock(
            return_value=[
                SimpleNamespace(id="SH00000001", is_active=True),
                SimpleNamespace(id="SH00000002", is_active=True),
            ]
        ),
    )
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "list_overlapping_announcements", AsyncMock(return_value=[]))
    create_mock = AsyncMock(return_value=created)
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
            "target_tenant_ids": ["SH00000002", "SH00000001", "SH00000001"],
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["target_tenant_ids"] == ["SH00000001", "SH00000002"]
    assert payload["effective_status"] == "scheduled"
    create_mock.assert_awaited_once_with(
        ANY,
        title="Planned maintenance",
        severity=PlatformMaintenanceSeverity.WARNING,
        message="Maintenance message",
        starts_at=ANY,
        estimated_duration_minutes=60,
        status=PlatformMaintenanceStatus.SCHEDULED,
        broadcast_all_tenants=False,
        target_tenant_ids=["SH00000002", "SH00000001"],
        created_by_user_id="super-1",
    )


@pytest.mark.asyncio
async def test_platform_maintenance_create_rejects_scheduled_notice_with_ended_window(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    create_mock = AsyncMock()
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "create", create_mock)

    response = await client.post(
        "/api/admin/platform-maintenance",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "title": "Old maintenance",
            "severity": "warning",
            "message": "Already over",
            "starts_at": "2026-01-01T10:00:00Z",
            "estimated_duration_minutes": 30,
            "status": "scheduled",
            "broadcast_all_tenants": True,
            "target_tenant_ids": [],
        },
    )

    assert response.status_code == 422
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_maintenance_update_returns_422_for_ended_window_validation(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    existing = _make_announcement(
        announcement_id="pm-ended-update",
        starts_at=datetime.now(UTC) - timedelta(hours=2),
        status=PlatformMaintenanceStatus.SCHEDULED,
        duration_minutes=30,
    )
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "get_by_id", AsyncMock(return_value=existing))
    update_mock = AsyncMock()
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "update", update_mock)

    response = await client.patch(
        "/api/admin/platform-maintenance/pm-ended-update",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"message": "Updated message for an already ended window"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["code"] == "VALIDATION_ERROR"
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_maintenance_detail_normalizes_naive_datetimes_to_utc(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    naive = datetime(2026, 4, 26, 10, 54, 0)
    announcement = _make_announcement(
        announcement_id="pm-naive-detail",
        starts_at=naive,
        status=PlatformMaintenanceStatus.SCHEDULED,
        duration_minutes=60,
    )
    announcement.ends_at = datetime(2026, 4, 26, 11, 54, 0)
    announcement.created_at = datetime(2026, 4, 26, 10, 54, 43)
    announcement.updated_at = datetime(2026, 4, 26, 10, 54, 47)
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "get_by_id", AsyncMock(return_value=announcement))

    response = await client.get(
        "/api/admin/platform-maintenance/pm-naive-detail",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["starts_at"].endswith("Z")
    assert payload["ends_at"].endswith("Z")
    assert payload["created_at"].endswith("Z")
    assert payload["updated_at"].endswith("Z")


@pytest.mark.asyncio
async def test_platform_maintenance_list_returns_effective_statuses(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    announcements = [
        _make_announcement(
            announcement_id="pm-list-active",
            tenant_ids=["SH00000001"],
            starts_at=datetime.now(UTC) - timedelta(minutes=10),
            status=PlatformMaintenanceStatus.SCHEDULED,
            duration_minutes=60,
        ),
        _make_announcement(
            announcement_id="pm-list-future",
            tenant_ids=["SH00000002"],
            starts_at=datetime.now(UTC) + timedelta(hours=2),
            status=PlatformMaintenanceStatus.ACTIVE,
            duration_minutes=60,
        ),
    ]
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "list_all", AsyncMock(return_value=announcements))

    response = await client.get(
        "/api/admin/platform-maintenance",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["pm-list-active", "pm-list-future"]
    assert payload[0]["effective_status"] == "active"
    assert payload[1]["effective_status"] == "scheduled"


@pytest.mark.asyncio
async def test_platform_maintenance_update_can_switch_to_broadcast_all(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    existing = _make_announcement(
        announcement_id="pm-update-broadcast",
        tenant_ids=["SH00000001", "SH00000002"],
        broadcast_all_tenants=False,
    )
    updated = _make_announcement(
        announcement_id="pm-update-broadcast",
        tenant_ids=[],
        broadcast_all_tenants=True,
    )
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "get_by_id", AsyncMock(return_value=existing))
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "list_overlapping_announcements", AsyncMock(return_value=[]))
    update_mock = AsyncMock(return_value=updated)
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "update", update_mock)

    response = await client.patch(
        "/api/admin/platform-maintenance/pm-update-broadcast",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "broadcast_all_tenants": True,
            "target_tenant_ids": [],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["broadcast_all_tenants"] is True
    assert payload["target_tenant_ids"] == []
    update_mock.assert_awaited_once_with(
        ANY,
        existing,
        title=existing.title,
        severity=existing.severity,
        message=existing.message,
        starts_at=existing.starts_at,
        estimated_duration_minutes=existing.estimated_duration_minutes,
        status=existing.status,
        broadcast_all_tenants=True,
        target_tenant_ids=[],
        updated_by_user_id="super-1",
    )


@pytest.mark.asyncio
async def test_platform_maintenance_delete_requires_super_admin(client):
    tenant_admin = _make_user(user_id="org-admin-3", tenant_id="SH00000001", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(tenant_admin, [])

    response = await client.delete(
        "/api/admin/platform-maintenance/pm-delete",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_platform_maintenance_delete_removes_notice(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    existing = _make_announcement(announcement_id="pm-delete-ok")
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "get_by_id", AsyncMock(return_value=existing))
    delete_mock = AsyncMock()
    monkeypatch.setattr(admin_api.platform_maintenance_repo, "delete", delete_mock)

    response = await client.delete(
        "/api/admin/platform-maintenance/pm-delete-ok",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 204
    delete_mock.assert_awaited_once_with(ANY, existing)


@pytest.mark.asyncio
async def test_current_platform_maintenance_uses_authenticated_tenant_scope(client, monkeypatch):
    viewer = _make_user(user_id="viewer-1", tenant_id="SH00000003", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(viewer, [])
    announcement = _make_announcement(
        announcement_id="pm-current",
        tenant_ids=["SH00000003"],
        starts_at=datetime.now(UTC) - timedelta(minutes=5),
        status=PlatformMaintenanceStatus.SCHEDULED,
        duration_minutes=45,
    )
    list_mock = AsyncMock(return_value=[announcement])
    monkeypatch.setattr(platform_maintenance_api.platform_maintenance_repo, "list_current_for_tenant", list_mock)

    response = await client.get(
        "/api/v1/platform-maintenance/current",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_id"] == "SH00000003"
    assert payload["announcements"][0]["id"] == "pm-current"
    assert payload["announcements"][0]["effective_status"] == "active"
    list_mock.assert_awaited_once_with(ANY, "SH00000003", now=ANY)


@pytest.mark.asyncio
async def test_current_platform_maintenance_downgrades_future_active_notice_to_scheduled(client, monkeypatch):
    viewer = _make_user(user_id="viewer-2", tenant_id="SH00000004", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(viewer, [])
    announcement = _make_announcement(
        announcement_id="pm-future-active",
        tenant_ids=["SH00000004"],
        starts_at=datetime.now(UTC) + timedelta(hours=2),
        status=PlatformMaintenanceStatus.ACTIVE,
        duration_minutes=45,
    )
    list_mock = AsyncMock(return_value=[announcement])
    monkeypatch.setattr(platform_maintenance_api.platform_maintenance_repo, "list_current_for_tenant", list_mock)

    response = await client.get(
        "/api/v1/platform-maintenance/current",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["announcements"][0]["effective_status"] == "scheduled"


@pytest.mark.asyncio
async def test_current_platform_maintenance_marks_expired_scheduled_notice_completed(client, monkeypatch):
    viewer = _make_user(user_id="viewer-3", tenant_id="SH00000005", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(viewer, [])
    announcement = _make_announcement(
        announcement_id="pm-expired",
        tenant_ids=["SH00000005"],
        starts_at=datetime.now(UTC) - timedelta(hours=2),
        status=PlatformMaintenanceStatus.SCHEDULED,
        duration_minutes=30,
    )
    list_mock = AsyncMock(return_value=[announcement])
    monkeypatch.setattr(platform_maintenance_api.platform_maintenance_repo, "list_current_for_tenant", list_mock)

    response = await client.get(
        "/api/v1/platform-maintenance/current",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["announcements"][0]["effective_status"] == "completed"


@pytest.mark.asyncio
async def test_current_platform_maintenance_handles_naive_database_datetimes(client, monkeypatch):
    viewer = _make_user(user_id="viewer-4", tenant_id="SH00000006", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(viewer, [])
    starts_at = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    announcement = _make_announcement(
        announcement_id="pm-naive-db",
        tenant_ids=["SH00000006"],
        starts_at=starts_at,
        status=PlatformMaintenanceStatus.SCHEDULED,
        duration_minutes=45,
    )
    announcement.ends_at = announcement.ends_at.replace(tzinfo=None)
    list_mock = AsyncMock(return_value=[announcement])
    monkeypatch.setattr(platform_maintenance_api.platform_maintenance_repo, "list_current_for_tenant", list_mock)

    response = await client.get(
        "/api/v1/platform-maintenance/current",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["announcements"][0]["effective_status"] == "active"
    assert payload["announcements"][0]["starts_at"].endswith("Z")
    assert payload["announcements"][0]["ends_at"].endswith("Z")


@pytest.mark.asyncio
async def test_current_platform_maintenance_requires_super_admin_target_scope(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    list_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(platform_maintenance_api.platform_maintenance_repo, "list_current_for_tenant", list_mock)

    response = await client.get(
        "/api/v1/platform-maintenance/current",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TENANT_SCOPE_REQUIRED"
    list_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_platform_maintenance_allows_super_admin_tenant_scope_header(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    announcement = _make_announcement(
        announcement_id="pm-super-scope",
        tenant_ids=["SH00000007"],
        starts_at=datetime.now(UTC) - timedelta(minutes=5),
        status=PlatformMaintenanceStatus.SCHEDULED,
        duration_minutes=45,
    )
    list_mock = AsyncMock(return_value=[announcement])
    monkeypatch.setattr(platform_maintenance_api.platform_maintenance_repo, "list_current_for_tenant", list_mock)

    response = await client.get(
        "/api/v1/platform-maintenance/current",
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Target-Tenant-Id": "SH00000007",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_id"] == "SH00000007"
    assert payload["announcements"][0]["id"] == "pm-super-scope"
    list_mock.assert_awaited_once_with(ANY, "SH00000007", now=ANY)
