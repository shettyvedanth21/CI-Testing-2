import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request


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

from app.api.v1.auth import router as auth_router
from app.api.v1.admin import router as admin_router
from app.api.v1.orgs import router as orgs_router
from app.api.v1 import admin as admin_api
from app.dependencies import get_token_claims
from app.api.v1 import orgs as orgs_api
from app.database import get_db
from app.models.auth import Plant, User, UserRole
from app.rate_limit import configure_rate_limiting
from app.services import auth_service as auth_service_module
from app.services.auth_service import AuthService
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

    def delete(self, key):
        self._ops.append(lambda: self._redis.delete(key))
        return self

    def srem(self, key, value):
        self._ops.append(lambda: self._redis.srem(key, value))
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

    def delete(self, key):
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return 1

    def srem(self, key, value):
        self.sets.setdefault(key, set()).discard(value)
        return 1

    def pipeline(self):
        return FakeRedisPipeline(self)


class FakeDBSession:
    def __init__(self):
        self.sync_session = object()

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def flush(self):
        return None


def _make_user(*, user_id: str, tenant_id: str | None, role: UserRole, is_active: bool = True) -> User:
    now = datetime.now(UTC).replace(tzinfo=None)
    return User(
        id=user_id,
        tenant_id=tenant_id,
        email=f"{user_id}@example.com",
        hashed_password="hashed",
        full_name="Test User",
        role=role,
        permissions_version=0,
        is_active=is_active,
        activated_at=now,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )


def _make_plant(*, plant_id: str, tenant_id: str, is_active: bool = True) -> Plant:
    now = datetime.now(UTC).replace(tzinfo=None)
    return Plant(
        id=plant_id,
        tenant_id=tenant_id,
        name=f"Plant {plant_id}",
        location="Test",
        timezone="Asia/Kolkata",
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


def _make_org(*, tenant_id: str, is_active: bool = True):
    now = datetime.now(UTC).replace(tzinfo=None)
    return SimpleNamespace(
        id=tenant_id,
        name=f"Org {tenant_id}",
        slug=f"org-{tenant_id}",
        is_active=is_active,
        created_at=now,
        premium_feature_grants_json=[],
        role_feature_matrix_json={"plant_manager": [], "operator": [], "viewer": []},
        entitlements_version=0,
    )


@pytest_asyncio.fixture
async def client(monkeypatch):
    app = FastAPI()
    configure_rate_limiting(app)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(orgs_router)

    async def _override_get_db():
        yield FakeDBSession()

    async def _override_get_token_claims(request: Request):
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail={"code": "MISSING_AUTH_TOKEN", "message": "Authentication token missing"})
        return TokenService().decode_access_token(auth_header.split(" ", 1)[1].strip())

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_token_claims] = _override_get_token_claims

    fake_redis = FakeRedis()
    monkeypatch.setattr(TokenService, "_get_redis_client", lambda self: fake_redis)
    monkeypatch.setattr(auth_service_module, "org_repo", orgs_api.org_repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as async_client:
        yield async_client

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_suspended_org_login_is_blocked(monkeypatch):
    db = FakeDBSession()
    user = _make_user(user_id="user-1", tenant_id="org-a", role=UserRole.ORG_ADMIN)

    monkeypatch.setattr("app.services.auth_service.user_repo.get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr("app.services.auth_service.org_repo.get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr("app.services.auth_service.pwd_ctx.verify", lambda password, hashed: True)

    with pytest.raises(HTTPException) as exc:
        await AuthService().login(db, user.email, "Password123!")

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ORG_SUSPENDED"


@pytest.mark.asyncio
async def test_suspended_org_refresh_is_blocked(monkeypatch):
    db = FakeDBSession()
    user = _make_user(user_id="user-1", tenant_id="org-a", role=UserRole.ORG_ADMIN)

    monkeypatch.setattr("app.services.auth_service.token_svc.validate_refresh_token", AsyncMock(return_value=SimpleNamespace(user_id=user.id)))
    monkeypatch.setattr("app.services.auth_service.user_repo.get_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr("app.services.auth_service.org_repo.get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))

    with pytest.raises(HTTPException) as exc:
        await AuthService().refresh(db, "refresh-token")

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ORG_SUSPENDED"


@pytest.mark.asyncio
async def test_suspended_org_create_user_is_blocked(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))

    response = await client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": "viewer@example.com",
            "password": "Viewer1234!",
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a"],
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_SUSPENDED"


@pytest.mark.asyncio
async def test_suspended_org_create_plant_is_blocked(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    create_mock = AsyncMock()
    monkeypatch.setattr(orgs_api.plant_repo, "create", create_mock)

    response = await client.post(
        "/api/v1/tenants/org-a/plants",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "Plant A", "location": "Pune", "timezone": "Asia/Kolkata"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_SUSPENDED"
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_suspended_org_resend_invite_is_blocked(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    pending_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    pending_user.is_active = False
    pending_user.activated_at = None
    access_token = TokenService().create_access_token(caller, [])
    resend_invitation = AsyncMock()

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=pending_user))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(orgs_api.auth_svc, "resend_invitation", resend_invitation)

    response = await client.post(
        "/api/v1/tenants/org-a/users/user-a/resend-invite",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_SUSPENDED"
    resend_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_suspended_org_update_user_is_blocked(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(caller, [])
    update_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(orgs_api.user_repo, "update", update_mock)

    response = await client.patch(
        "/api/v1/tenants/org-a/users/user-a",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"full_name": "Changed Viewer"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_SUSPENDED"
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_suspended_org_deactivate_user_is_blocked(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(caller, [])
    increment_permissions_version = AsyncMock()

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(orgs_api.user_repo, "increment_permissions_version", increment_permissions_version)

    response = await client.patch(
        "/api/v1/tenants/org-a/users/user-a/deactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_SUSPENDED"
    increment_permissions_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_suspended_org_reactivate_user_is_blocked(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER, is_active=False)
    access_token = TokenService().create_access_token(caller, [])
    target_user.deactivated_at = datetime.now(UTC).replace(tzinfo=None)
    increment_permissions_version = AsyncMock()

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(orgs_api.user_repo, "increment_permissions_version", increment_permissions_version)

    response = await client.patch(
        "/api/v1/tenants/org-a/users/user-a/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_SUSPENDED"
    increment_permissions_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_suspended_org_deactivate_plant_is_blocked(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    update_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(orgs_api.plant_repo, "update", update_mock)

    response = await client.patch(
        "/api/v1/tenants/org-a/plants/plant-a/deactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_SUSPENDED"
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_suspended_org_reactivate_plant_is_blocked(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    update_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=False)))
    monkeypatch.setattr(orgs_api.plant_repo, "update", update_mock)

    response = await client.patch(
        "/api/v1/tenants/org-a/plants/plant-a/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_SUSPENDED"
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_plant_cannot_be_used_for_new_invites(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=True)))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=_make_org(tenant_id="org-a", is_active=True)))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a", is_active=False)]),
    )
    monkeypatch.setattr(
        auth_service_module.plant_repo,
        "list_by_ids_for_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a", is_active=False)]),
    )

    response = await client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": "viewer@example.com",
            "password": "Viewer1234!",
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANT_INACTIVE"


@pytest.mark.asyncio
async def test_reactivation_restores_org_and_plant_status(client, monkeypatch):
    super_admin = _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])
    org = _make_org(tenant_id="org-a", is_active=False)
    plant = _make_plant(plant_id="plant-a", tenant_id="org-a", is_active=False)

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=super_admin))
    async def update_org(_db, tenant_id, updates):
        assert tenant_id == "org-a"
        org.is_active = bool(updates["is_active"])
        return org

    async def update_plant(_db, plant_id, updates):
        assert plant_id == "plant-a"
        plant.is_active = bool(updates["is_active"])
        return plant

    monkeypatch.setattr(admin_api.org_repo, "get_by_id", AsyncMock(return_value=org))
    monkeypatch.setattr(admin_api.org_repo, "update", update_org)
    monkeypatch.setattr(orgs_api.auth_svc, "assert_org_active_for_write", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.plant_repo, "get_by_id_for_tenant", AsyncMock(return_value=plant))
    monkeypatch.setattr(orgs_api.plant_repo, "update", update_plant)

    org_response = await client.patch(
        "/api/admin/tenants/org-a/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    plant_response = await client.patch(
        "/api/v1/tenants/org-a/plants/plant-a/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert org_response.status_code == 200
    assert org_response.json()["is_active"] is True
    assert plant_response.status_code == 200
    assert plant_response.json()["is_active"] is True


@pytest.mark.asyncio
async def test_plant_delete_guard_blocks_when_devices_exist(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.plant_repo, "get_by_id_for_tenant", AsyncMock(return_value=_make_plant(plant_id="plant-a", tenant_id="org-a")))
    monkeypatch.setattr(orgs_api, "_get_plant_device_count", AsyncMock(return_value=3))

    response = await client.get(
        "/api/v1/tenants/org-a/plants/plant-a/delete-guard",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_delete"] is False
    assert payload["code"] == "PLANT_DELETE_BLOCKED_DEVICES_EXIST"
    assert payload["device_count"] == 3


@pytest.mark.asyncio
async def test_plant_delete_guard_allows_delete_when_no_devices_exist(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.plant_repo, "get_by_id_for_tenant", AsyncMock(return_value=_make_plant(plant_id="plant-a", tenant_id="org-a")))
    monkeypatch.setattr(orgs_api, "_get_plant_device_count", AsyncMock(return_value=0))

    response = await client.get(
        "/api/v1/tenants/org-a/plants/plant-a/delete-guard",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_delete"] is True
    assert payload["device_count"] == 0
    assert "no attached devices" in payload["message"]


@pytest.mark.asyncio
async def test_plant_lifecycle_endpoints_preserve_tenant_isolation(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.auth_svc, "get_user_by_token_claims", AsyncMock(return_value=caller))
    monkeypatch.setattr(orgs_api.auth_svc, "assert_org_active_for_write", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.plant_repo, "get_by_id_for_tenant", AsyncMock(return_value=None))

    response = await client.patch(
        "/api/v1/tenants/org-a/plants/plant-b/deactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PLANT_NOT_FOUND"


@pytest.mark.asyncio
async def test_deactivate_plant_rejects_already_inactive(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    update_mock = AsyncMock()

    monkeypatch.setattr(
        orgs_api.auth_svc,
        "get_user_by_token_claims",
        AsyncMock(return_value=caller),
    )
    monkeypatch.setattr(orgs_api.auth_svc, "assert_org_active_for_write", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "get_by_id_for_tenant",
        AsyncMock(return_value=_make_plant(plant_id="plant-a", tenant_id="org-a", is_active=False)),
    )
    monkeypatch.setattr(orgs_api.plant_repo, "update", update_mock)

    response = await client.patch(
        "/api/v1/tenants/org-a/plants/plant-a/deactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANT_ALREADY_INACTIVE"
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reactivate_plant_rejects_already_active(client, monkeypatch):
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    update_mock = AsyncMock()

    monkeypatch.setattr(
        orgs_api.auth_svc,
        "get_user_by_token_claims",
        AsyncMock(return_value=caller),
    )
    monkeypatch.setattr(orgs_api.auth_svc, "assert_org_active_for_write", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "get_by_id_for_tenant",
        AsyncMock(return_value=_make_plant(plant_id="plant-a", tenant_id="org-a", is_active=True)),
    )
    monkeypatch.setattr(orgs_api.plant_repo, "update", update_mock)

    response = await client.patch(
        "/api/v1/tenants/org-a/plants/plant-a/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANT_ALREADY_ACTIVE"
    update_mock.assert_not_awaited()
