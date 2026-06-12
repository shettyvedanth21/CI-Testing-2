import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import ANY, AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from starlette.requests import Request
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

from app.api.v1.auth import router as auth_router
from app.api.v1 import admin as admin_api
from app.api.v1 import auth as auth_api
from app.api.v1.admin import router as admin_router
from app.dependencies import get_token_claims
from app.api.v1.orgs import router as orgs_router
from app.api.v1 import orgs as orgs_api
from app.services import auth_service as auth_service_module
from app.database import get_db
from app.models.auth import Plant, User, UserRole
from app.rate_limit import configure_rate_limiting
from app.repositories.user_repository import UserRepository
from app.services.token_service import TokenService
from services.shared.feature_entitlements import build_feature_entitlement_state


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

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def expire(self, key, ttl):
        self.ttls[key] = int(ttl)
        return True

    def ttl(self, key):
        return self.ttls.get(key, -1)

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
    async def execute(self, *args, **kwargs):
        return None


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


def _make_plant(*, plant_id: str, tenant_id: str | None = None) -> Plant:
    now = datetime.now(UTC)
    return Plant(
        id=plant_id,
        tenant_id=tenant_id,
        name=f"Plant {plant_id}",
        location="Test",
        timezone="Asia/Kolkata",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _make_org(*, tenant_id: str, premium_feature_grants_json=None, role_feature_matrix_json=None, entitlements_version: int = 0):
    return type(
        "Org",
        (),
        {
            "id": tenant_id,
            "name": f"Org {tenant_id}",
            "slug": f"org-{tenant_id}",
            "is_active": True,
            "premium_feature_grants_json": premium_feature_grants_json or [],
            "role_feature_matrix_json": role_feature_matrix_json or {"plant_manager": [], "operator": [], "viewer": []},
            "entitlements_version": entitlements_version,
            "created_at": datetime.now(UTC),
        },
    )()


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
    monkeypatch.setattr(auth_service_module, "plant_repo", orgs_api.plant_repo)

    async def _list_by_ids_for_tenant(db, tenant_id, plant_ids):
        plants = await orgs_api.plant_repo.list_by_tenant(db, tenant_id)
        allowed_ids = set(plant_ids)
        return [plant for plant in plants if plant.id in allowed_ids]

    monkeypatch.setattr(auth_service_module.plant_repo, "list_by_ids_for_tenant", AsyncMock(side_effect=_list_by_ids_for_tenant))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as async_client:
        yield async_client, fake_redis

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_update_user_cross_org_returns_404(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    scoped_get = AsyncMock(return_value=None)
    update_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", scoped_get)
    monkeypatch.setattr(orgs_api.user_repo, "update", update_mock)

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/user-b",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"full_name": "Changed Name"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"
    scoped_get.assert_awaited_once()
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_rejects_foreign_plant_ids(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a")]),
    )
    create_mock = AsyncMock()
    set_access_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "create", create_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", set_access_mock)
    monkeypatch.setattr(orgs_api.pwd_ctx, "hash", lambda _: "hashed-password")

    response = await async_client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": "viewer@example.com",
            "password": "Viewer1234!",
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a", "plant-b"],
        },
    )

    assert response.status_code == 403
    body = response.json()
    assert body["detail"]["code"] == "INVALID_PLANT_IDS"
    assert body["detail"]["rejected_ids"] == ["plant-b"]
    create_mock.assert_not_awaited()
    set_access_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_accepts_canonical_tenant_id_field(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a")]),
    )
    created_user = _make_user(user_id="user-new", tenant_id="org-a", role=UserRole.VIEWER)
    create_mock = AsyncMock(return_value=created_user)
    monkeypatch.setattr(orgs_api.user_repo, "create", create_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", AsyncMock())
    monkeypatch.setattr(orgs_api.pwd_ctx, "hash", lambda _: "hashed-password")

    response = await async_client.post(
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

    assert response.status_code == 201
    create_mock.assert_awaited_once_with(
        ANY,
        email="viewer@example.com",
        hashed_password="hashed-password",
        role=UserRole.VIEWER,
        tenant_id="org-a",
        full_name="Viewer",
    )
    assert response.json()["tenant_id"] == "org-a"
    assert response.json()["tenant_id"] == "org-a"


@pytest.mark.asyncio
async def test_create_plant_uses_canonical_tenant_route(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    created_plant = _make_plant(plant_id="plant-a", tenant_id="org-a")

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    create_mock = AsyncMock(return_value=created_plant)
    monkeypatch.setattr(orgs_api.plant_repo, "create", create_mock)

    response = await async_client.post(
        "/api/v1/tenants/org-a/plants",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "Plant A", "location": "Line 1", "timezone": "Asia/Kolkata"},
    )

    assert response.status_code == 201
    create_mock.assert_awaited_once_with(ANY, "org-a", "Plant A", "Line 1", "Asia/Kolkata")
    assert response.json()["tenant_id"] == "org-a"
    assert response.json()["tenant_id"] == "org-a"


@pytest.mark.asyncio
async def test_plant_manager_cannot_invite_elevated_roles(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="pm-a", tenant_id="org-a", role=UserRole.PLANT_MANAGER)
    access_token = TokenService().create_access_token(caller, ["plant-a"])

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))
    create_mock = AsyncMock()
    set_access_mock = AsyncMock()
    monkeypatch.setattr(orgs_api.user_repo, "create", create_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", set_access_mock)

    response = await async_client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": "orgadmin@example.com",
            "password": "OrgAdmin123!",
            "full_name": "Org Admin",
            "role": "org_admin",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a"],
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ROLE_ESCALATION_FORBIDDEN"
    create_mock.assert_not_awaited()
    set_access_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_list_users_accepts_tenant_id_query(client, monkeypatch):
    async_client, _ = client
    users = [_make_user(user_id="user-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)]

    list_mock = AsyncMock(return_value=users)
    monkeypatch.setattr(admin_api.user_repo, "list_by_tenant", list_mock)

    super_admin = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])

    response = await async_client.get(
        "/api/admin/users?tenant_id=org-a",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    list_mock.assert_awaited_once_with(ANY, "org-a")
    payload = response.json()
    assert payload[0]["tenant_id"] == "org-a"
    assert payload[0]["tenant_id"] == "org-a"


@pytest.mark.asyncio
async def test_create_org_still_works_for_super_admin(client, monkeypatch):
    async_client, _ = client
    now = datetime.now(UTC)
    created_org = type(
        "Org",
        (),
        {
            "id": "SH00000001",
            "name": "Org A",
            "slug": "org-a",
            "is_active": True,
            "created_at": now,
        },
    )()
    monkeypatch.setattr(admin_api.org_repo, "get_by_slug", AsyncMock(return_value=None))
    monkeypatch.setattr(admin_api.org_repo, "create", AsyncMock(return_value=created_org))

    super_admin = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])

    response = await async_client.post(
        "/api/admin/tenants",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "Org A", "slug": "org-a"},
    )

    assert response.status_code == 201
    assert response.json()["id"] == "SH00000001"


@pytest.mark.asyncio
async def test_super_admin_summary_combines_org_count_and_active_device_metric(client, monkeypatch):
    async_client, _ = client
    super_admin = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])

    monkeypatch.setattr(
        admin_api.org_repo,
        "list_all",
        AsyncMock(
            return_value=[
                _make_org(tenant_id="org-a"),
                _make_org(tenant_id="org-b"),
                _make_org(tenant_id="org-c"),
            ]
        ),
    )
    monkeypatch.setattr(admin_api, "_get_total_active_devices", AsyncMock(return_value=7))

    response = await async_client.get(
        "/api/admin/summary",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "total_organisations": 3,
        "total_active_devices": 7,
    }


@pytest.mark.asyncio
async def test_super_admin_summary_propagates_device_dependency_failures(client, monkeypatch):
    async_client, _ = client
    super_admin = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(super_admin, [])

    monkeypatch.setattr(admin_api.org_repo, "list_all", AsyncMock(return_value=[_make_org(tenant_id="org-a")]))
    monkeypatch.setattr(
        admin_api,
        "_get_total_active_devices",
        AsyncMock(
            side_effect=HTTPException(
                status_code=503,
                detail={
                    "code": "SUPER_ADMIN_SUMMARY_UNAVAILABLE",
                    "message": "Unable to load active device summary right now. Please try again.",
                },
            )
        ),
    )

    response = await async_client.get(
        "/api/admin/summary",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_SUMMARY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_plant_manager_must_choose_exactly_one_plant(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="pm-a", tenant_id="org-a", role=UserRole.PLANT_MANAGER)
    access_token = TokenService().create_access_token(caller, ["plant-a", "plant-b"])

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[
            _make_plant(plant_id="plant-a", tenant_id="org-a"),
            _make_plant(plant_id="plant-b", tenant_id="org-a"),
        ]),
    )
    create_mock = AsyncMock()
    set_access_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "create", create_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", set_access_mock)
    monkeypatch.setattr(orgs_api.pwd_ctx, "hash", lambda _: "hashed-password")

    response = await async_client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": "viewer@example.com",
            "password": "Viewer1234!",
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a", "plant-b"],
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INVALID_PLANT_IDS"
    create_mock.assert_not_awaited()
    set_access_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_without_password_sends_invite(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    created_user = _make_user(user_id="user-new", tenant_id="org-a", role=UserRole.VIEWER)

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a")]),
    )
    create_mock = AsyncMock(return_value=created_user)
    set_access_mock = AsyncMock()
    send_invite_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "create", create_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", set_access_mock)
    monkeypatch.setattr(orgs_api.auth_svc, "send_invitation", send_invite_mock)
    monkeypatch.setattr(orgs_api.pwd_ctx, "hash", lambda *_args, **_kwargs: "hashed-password")

    response = await async_client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": "viewer@example.com",
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a"],
        },
    )

    assert response.status_code == 201
    create_mock.assert_awaited_once()
    set_access_mock.assert_awaited_once_with(ANY, "user-new", ["plant-a"])
    send_invite_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_user_with_password_activates_user_without_invite(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    created_user = _make_user(user_id="user-new", tenant_id="org-a", role=UserRole.VIEWER)

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a")]),
    )
    create_mock = AsyncMock(return_value=created_user)
    set_access_mock = AsyncMock()
    send_invite_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "create", create_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", set_access_mock)
    monkeypatch.setattr(orgs_api.auth_svc, "send_invitation", send_invite_mock)
    monkeypatch.setattr(orgs_api.pwd_ctx, "hash", lambda *_args, **_kwargs: "hashed-password")

    response = await async_client.post(
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

    assert response.status_code == 201
    assert response.json()["is_active"] is True
    create_mock.assert_awaited_once()
    set_access_mock.assert_awaited_once_with(ANY, "user-new", ["plant-a"])
    send_invite_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_succeeds_when_invite_email_delivery_fails(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    created_user = _make_user(user_id="user-new", tenant_id="org-a", role=UserRole.VIEWER)

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a")]),
    )
    create_mock = AsyncMock(return_value=created_user)
    set_access_mock = AsyncMock()
    invalidate_mock = AsyncMock()
    create_token_mock = AsyncMock(return_value="invite-token")
    send_invite_mock = AsyncMock(side_effect=RuntimeError("smtp down"))

    monkeypatch.setattr(orgs_api.user_repo, "create", create_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", set_access_mock)
    monkeypatch.setattr(orgs_api.pwd_ctx, "hash", lambda *_args, **_kwargs: "hashed-password")
    monkeypatch.setattr(auth_service_module.action_token_svc, "invalidate_open_tokens", invalidate_mock)
    monkeypatch.setattr(auth_service_module.action_token_svc, "create_token", create_token_mock)
    monkeypatch.setattr(auth_service_module.mailer_svc, "send_invite_email", send_invite_mock)

    response = await async_client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": "viewer@example.com",
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a"],
        },
    )

    assert response.status_code == 201
    create_mock.assert_awaited_once()
    set_access_mock.assert_awaited_once_with(ANY, "user-new", ["plant-a"])
    invalidate_mock.assert_awaited_once()
    create_token_mock.assert_awaited_once()
    send_invite_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_user_reuses_existing_never_activated_email_with_reinvite(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    existing_user = _make_user(user_id="existing-user", tenant_id="org-a", role=UserRole.VIEWER)
    existing_user.is_active = False
    existing_user.activated_at = None

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=existing_user))
    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a")]),
    )
    create_mock = AsyncMock()
    set_access_mock = AsyncMock()
    send_invite_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "create", create_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", set_access_mock)
    monkeypatch.setattr(orgs_api.auth_svc, "send_invitation", send_invite_mock)
    monkeypatch.setattr(orgs_api.pwd_ctx, "hash", lambda *_args, **_kwargs: "hashed-password")

    response = await async_client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": existing_user.email,
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a"],
        },
    )

    assert response.status_code == 201
    assert response.json()["id"] == existing_user.id
    create_mock.assert_not_awaited()
    set_access_mock.assert_awaited_once_with(ANY, existing_user.id, ["plant-a"])
    send_invite_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_user_blocks_reinvite_for_deactivated_activated_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    existing_user = _make_user(user_id="existing-user", tenant_id="org-a", role=UserRole.VIEWER)
    existing_user.is_active = False

    monkeypatch.setattr(orgs_api.user_repo, "get_by_email", AsyncMock(return_value=existing_user))
    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=type("Org", (), {"id": "org-a", "is_active": True})()),
    )
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[_make_plant(plant_id="plant-a", tenant_id="org-a")]),
    )
    monkeypatch.setattr(orgs_api.pwd_ctx, "hash", lambda *_args, **_kwargs: "hashed-password")

    response = await async_client.post(
        "/api/v1/tenants/org-a/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": existing_user.email,
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": "org-a",
            "plant_ids": ["plant-a"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USER_DEACTIVATED_USE_REACTIVATE"


@pytest.mark.asyncio
async def test_resend_invite_rejects_previously_activated_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    target_user.is_active = False

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))

    response = await async_client.post(
        "/api/v1/tenants/org-a/users/user-a/resend-invite",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USER_DEACTIVATED_USE_REACTIVATE"


@pytest.mark.asyncio
async def test_org_admin_cannot_update_existing_org_admin_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="org-admin-b", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    update_mock = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.user_repo, "update", update_mock)

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/org-admin-b",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"full_name": "Changed Name"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ROLE_ESCALATION_FORBIDDEN"
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_admin_cannot_deactivate_existing_org_admin_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="org-admin-b", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    increment_permissions_version = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.user_repo, "increment_permissions_version", increment_permissions_version)

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/org-admin-b/deactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ROLE_ESCALATION_FORBIDDEN"
    increment_permissions_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_admin_cannot_reactivate_existing_org_admin_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="org-admin-b", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user.is_active = False
    access_token = TokenService().create_access_token(caller, [])
    increment_permissions_version = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.user_repo, "increment_permissions_version", increment_permissions_version)

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/org-admin-b/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ROLE_ESCALATION_FORBIDDEN"
    increment_permissions_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_admin_cannot_resend_invite_for_existing_org_admin_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="org-admin-b", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user.is_active = False
    target_user.activated_at = None
    access_token = TokenService().create_access_token(caller, [])
    resend_invitation = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.auth_svc, "resend_invitation", resend_invitation)

    response = await async_client.post(
        "/api/v1/tenants/org-a/users/org-admin-b/resend-invite",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ROLE_ESCALATION_FORBIDDEN"
    resend_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_reactivate_user_succeeds_for_previously_activated_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    target_user.is_active = False

    monkeypatch.setattr(orgs_api.auth_svc, "assert_org_active_for_write", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.user_repo, "increment_permissions_version", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.token_svc, "revoke_all_user_tokens", AsyncMock())

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/user-a/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "User reactivated"
    assert target_user.is_active is True


@pytest.mark.asyncio
async def test_reactivate_user_rejects_never_activated_invited_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    target_user.is_active = False
    target_user.activated_at = None

    monkeypatch.setattr(orgs_api.auth_svc, "assert_org_active_for_write", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/user-a/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REACTIVATE_NOT_ALLOWED_PENDING_INVITE"


@pytest.mark.asyncio
async def test_reactivate_user_rejects_already_active_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    increment_permissions_version = AsyncMock()

    monkeypatch.setattr(orgs_api.auth_svc, "assert_org_active_for_write", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.user_repo, "increment_permissions_version", increment_permissions_version)

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/user-a/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USER_ALREADY_ACTIVE"
    increment_permissions_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_resend_invite_rejects_active_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(caller, [])
    resend_invitation = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.auth_svc, "resend_invitation", resend_invitation)

    response = await async_client.post(
        "/api/v1/tenants/org-a/users/user-a/resend-invite",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVITE_NOT_PENDING"
    resend_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_user_rejects_inactive_plant_assignment(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="user-a", tenant_id="org-a", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(caller, [])
    update_mock = AsyncMock()
    set_access_mock = AsyncMock()

    inactive_plant = _make_plant(plant_id="plant-a", tenant_id="org-a")
    inactive_plant.is_active = False

    monkeypatch.setattr(orgs_api.auth_svc, "assert_org_active_for_write", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(
        orgs_api.plant_repo,
        "list_by_tenant",
        AsyncMock(return_value=[inactive_plant]),
    )
    monkeypatch.setattr(
        auth_service_module.plant_repo,
        "list_by_ids_for_tenant",
        AsyncMock(return_value=[inactive_plant]),
    )
    monkeypatch.setattr(orgs_api.user_repo, "update", update_mock)
    monkeypatch.setattr(orgs_api.user_repo, "set_plant_access", set_access_mock)

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/user-a",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"plant_ids": ["plant-a"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANT_INACTIVE"
    update_mock.assert_not_awaited()
    set_access_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_admin_cannot_list_users_for_other_tenant(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="org-admin", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=0)
    monkeypatch.setattr(auth_service_module.AuthService, "get_user_by_token_claims", AsyncMock(return_value=caller))

    response = await async_client.get(
        "/api/v1/tenants/org-b/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_get_user_plant_access_cross_org_returns_404(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="org-admin", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=None))

    response = await async_client.get(
        "/api/v1/tenants/org-a/users/user-b/plant-access",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_org_admin_cannot_view_plant_access_for_existing_org_admin_user(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="org-admin-a", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    target_user = _make_user(user_id="org-admin-b", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    get_plant_ids = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=target_user))
    monkeypatch.setattr(orgs_api.user_repo, "get_plant_ids", get_plant_ids)

    response = await async_client.get(
        "/api/v1/tenants/org-a/users/org-admin-b/plant-access",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ROLE_ESCALATION_FORBIDDEN"
    get_plant_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_deactivate_user_cross_org_returns_404(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="org-admin", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    increment_permissions_version = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.user_repo, "increment_permissions_version", increment_permissions_version)

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/user-b/deactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"
    increment_permissions_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_reactivate_user_cross_org_returns_404(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="org-admin", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    increment_permissions_version = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.user_repo, "increment_permissions_version", increment_permissions_version)

    response = await async_client.patch(
        "/api/v1/tenants/org-a/users/user-b/reactivate",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"
    increment_permissions_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_resend_invite_cross_org_returns_404(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="org-admin", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [])
    resend_invitation = AsyncMock()

    monkeypatch.setattr(orgs_api.user_repo, "get_by_id_for_tenant", AsyncMock(return_value=None))
    monkeypatch.setattr(orgs_api.auth_svc, "resend_invitation", resend_invitation)

    response = await async_client.post(
        "/api/v1/tenants/org-a/users/user-b/resend-invite",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"
    resend_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_super_admin_can_read_org_entitlements(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(
            return_value=_make_org(
                tenant_id="org-a",
                premium_feature_grants_json=["analytics", "reports"],
                role_feature_matrix_json={"plant_manager": ["analytics"], "operator": [], "viewer": []},
                entitlements_version=0,
            )
        ),
    )

    response = await async_client.get(
        "/api/v1/tenants/org-a/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["premium_feature_grants"] == ["analytics", "reports"]
    assert payload["available_features"] == ["machines", "calendar", "rules", "settings", "analytics", "reports"]


@pytest.mark.asyncio
async def test_org_admin_cannot_delegate_ungranted_premium_features(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="fb441ba8-ee0b-49b3-9564-27cdfee43a93", tenant_id=tenant_id, role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=0)

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=_make_org(tenant_id=tenant_id)),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "role_feature_matrix": {
                "plant_manager": ["analytics"],
                "operator": [],
                "viewer": [],
            }
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_SCOPE_DENIED"


@pytest.mark.asyncio
async def test_org_admin_cannot_modify_organisation_premium_grants(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="org-admin", tenant_id=tenant_id, role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=0)

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=_make_org(tenant_id=tenant_id)),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "premium_feature_grants": ["notification_sms"],
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_SCOPE_DENIED"
    assert response.json()["detail"]["message"] == "Org admins cannot modify organisation-level premium grants."


@pytest.mark.asyncio
async def test_super_admin_cannot_modify_role_feature_matrix(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=_make_org(tenant_id=tenant_id)),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "role_feature_matrix": {
                "plant_manager": ["analytics"],
                "operator": [],
                "viewer": [],
            }
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_SCOPE_DENIED"
    assert response.json()["detail"]["message"] == "Super admins manage organisation grants only."


@pytest.mark.asyncio
async def test_super_admin_grant_then_revoke_premium_features_updates_available_features(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    initial_org = _make_org(tenant_id=tenant_id, entitlements_version=0)
    granted_org = _make_org(
        tenant_id=tenant_id,
        premium_feature_grants_json=["analytics", "copilot", "notification_sms"],
        entitlements_version=1,
    )
    revoked_org = _make_org(tenant_id=tenant_id, entitlements_version=2)

    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(side_effect=[initial_org, granted_org]))
    monkeypatch.setattr(orgs_api.org_repo, "update_entitlements", AsyncMock(side_effect=[granted_org, revoked_org]))
    monkeypatch.setattr(auth_service_module.AuthService, "get_user_by_token_claims", AsyncMock(return_value=caller))

    grant_response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"premium_feature_grants": ["analytics", "copilot", "notification_sms"]},
    )
    revoke_response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"premium_feature_grants": []},
    )

    assert grant_response.status_code == 200
    assert grant_response.json()["premium_feature_grants"] == ["analytics", "copilot", "notification_sms"]
    assert set(grant_response.json()["available_features"]) >= {"machines", "calendar", "rules", "settings", "analytics", "copilot", "notification_sms"}
    assert grant_response.json()["entitlements_version"] == 1

    assert revoke_response.status_code == 200
    assert revoke_response.json()["premium_feature_grants"] == []
    assert revoke_response.json()["available_features"] == ["machines", "calendar", "rules", "settings"]
    assert revoke_response.json()["entitlements_version"] == 2


@pytest.mark.asyncio
async def test_org_admin_delegate_then_revoke_plant_manager_premium_features_updates_effective_access(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="org-admin", tenant_id=tenant_id, role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=1)

    current_org = _make_org(
        tenant_id=tenant_id,
        premium_feature_grants_json=["analytics", "reports", "waste_analysis"],
        entitlements_version=1,
    )
    delegated_org = _make_org(
        tenant_id=tenant_id,
        premium_feature_grants_json=["analytics", "reports", "waste_analysis"],
        role_feature_matrix_json={"plant_manager": ["analytics", "waste_analysis"], "operator": [], "viewer": []},
        entitlements_version=2,
    )
    revoked_org = _make_org(
        tenant_id=tenant_id,
        premium_feature_grants_json=["analytics", "reports", "waste_analysis"],
        role_feature_matrix_json={"plant_manager": [], "operator": [], "viewer": []},
        entitlements_version=3,
    )

    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(side_effect=[current_org, current_org]))
    monkeypatch.setattr(orgs_api.org_repo, "update_entitlements", AsyncMock(side_effect=[delegated_org, revoked_org]))
    monkeypatch.setattr(auth_service_module.AuthService, "get_user_by_token_claims", AsyncMock(return_value=caller))

    delegate_response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"role_feature_matrix": {"plant_manager": ["analytics", "waste_analysis"], "operator": [], "viewer": []}},
    )
    revoke_response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"role_feature_matrix": {"plant_manager": [], "operator": [], "viewer": []}},
    )

    assert delegate_response.status_code == 200
    assert delegate_response.json()["role_feature_matrix"]["plant_manager"] == ["analytics", "waste_analysis"]
    assert delegate_response.json()["effective_features_by_role"]["plant_manager"] == ["machines", "rules", "settings", "analytics", "waste_analysis"]
    assert delegate_response.json()["entitlements_version"] == 2

    assert revoke_response.status_code == 200
    assert revoke_response.json()["role_feature_matrix"]["plant_manager"] == []
    assert revoke_response.json()["effective_features_by_role"]["plant_manager"] == ["machines", "rules", "settings"]
    assert revoke_response.json()["entitlements_version"] == 3


@pytest.mark.asyncio
async def test_org_admin_cannot_access_other_tenant_entitlements(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="org-admin", tenant_id="org-a", role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=0)
    monkeypatch.setattr(auth_service_module.AuthService, "get_user_by_token_claims", AsyncMock(return_value=caller))

    response = await async_client.get(
        "/api/v1/tenants/org-b/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ORG_ACCESS_DENIED"


def test_entitlement_loader_accepts_stringified_json():
    state = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants='["analytics", "reports"]',
        role_feature_matrix='{"plant_manager": ["analytics"], "operator": [], "viewer": []}',
        entitlements_version=2,
    )

    assert state.premium_feature_grants_list == ["analytics", "reports"]
    assert state.available_features == ("machines", "calendar", "rules", "settings", "analytics", "reports")
    assert state.effective_features_by_role["plant_manager"] == ("machines", "rules", "settings", "analytics")


def test_entitlement_loader_accepts_notification_channel_premium_grants():
    state = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=["notification_sms", "notification_whatsapp"],
        role_feature_matrix={},
        entitlements_version=3,
    )

    assert state.premium_feature_grants_list == ["notification_sms", "notification_whatsapp"]
    assert "notification_sms" in state.available_features
    assert "notification_whatsapp" in state.available_features
    assert state.has_premium_grant("notification_sms") is True
    assert state.has_premium_grant("notification_whatsapp") is True


@pytest.mark.asyncio
async def test_super_admin_entitlements_update_rejects_unknown_premium_feature(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=_make_org(tenant_id=tenant_id)),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"premium_feature_grants": ["definitely_not_real"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_FEATURE_KEY"


@pytest.mark.asyncio
async def test_org_admin_entitlements_update_requires_role_matrix_payload(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="org-admin", tenant_id=tenant_id, role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=0)

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=_make_org(tenant_id=tenant_id)),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ROLE_MATRIX_REQUIRED"


@pytest.mark.asyncio
async def test_org_admin_entitlements_update_rejects_unknown_role_key(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="org-admin", tenant_id=tenant_id, role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=1)

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(
            return_value=_make_org(
                tenant_id=tenant_id,
                premium_feature_grants_json=["analytics"],
                entitlements_version=1,
            )
        ),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"role_feature_matrix": {"line_manager": ["analytics"]}},
    )

    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["code"] == "INVALID_ROLE_KEY"
    assert body["invalid_roles"] == ["line_manager"]


@pytest.mark.asyncio
async def test_org_admin_entitlements_update_rejects_operator_premium_assignment(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="org-admin", tenant_id=tenant_id, role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=1)

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(
            return_value=_make_org(
                tenant_id=tenant_id,
                premium_feature_grants_json=["analytics", "reports"],
                entitlements_version=1,
            )
        ),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "role_feature_matrix": {
                "plant_manager": [],
                "operator": ["analytics"],
                "viewer": [],
            }
        },
    )

    assert response.status_code == 403
    body = response.json()["detail"]
    assert body["code"] == "FEATURE_SCOPE_DENIED"
    assert body["invalid_features"] == ["analytics"]
    assert body["message"] == "Operators cannot be assigned premium features."


@pytest.mark.asyncio
async def test_super_admin_entitlements_update_requires_premium_grants_payload(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="super-admin", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(return_value=_make_org(tenant_id=tenant_id)),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PREMIUM_GRANTS_REQUIRED"


@pytest.mark.asyncio
async def test_me_includes_machine_health_in_available_features_when_org_granted(client, monkeypatch):
    async_client, _ = client
    user = _make_user(user_id="viewer-mh", tenant_id="org-mh", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(user, [], tenant_entitlements_version=1)
    org = _make_org(
        tenant_id="org-mh",
        premium_feature_grants_json=["machine_health"],
        entitlements_version=1,
    )

    monkeypatch.setattr(auth_service_module.AuthService, "get_user_by_token_claims", AsyncMock(return_value=user))
    monkeypatch.setattr(auth_api.org_repo, "get_by_id", AsyncMock(return_value=org))
    monkeypatch.setattr(UserRepository, "get_plant_ids", AsyncMock(return_value=[]))

    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "machine_health" in payload["entitlements"]["available_features"]
    assert payload["entitlements"]["entitlements_version"] == 1


@pytest.mark.asyncio
async def test_me_excludes_machine_health_when_org_grant_absent(client, monkeypatch):
    async_client, _ = client
    user = _make_user(user_id="viewer-no-mh", tenant_id="org-no-mh", role=UserRole.VIEWER)
    access_token = TokenService().create_access_token(user, [], tenant_entitlements_version=0)
    org = _make_org(tenant_id="org-no-mh", premium_feature_grants_json=[], entitlements_version=0)

    monkeypatch.setattr(auth_service_module.AuthService, "get_user_by_token_claims", AsyncMock(return_value=user))
    monkeypatch.setattr(auth_api.org_repo, "get_by_id", AsyncMock(return_value=org))
    monkeypatch.setattr(UserRepository, "get_plant_ids", AsyncMock(return_value=[]))

    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "machine_health" not in payload["entitlements"]["available_features"]


@pytest.mark.asyncio
async def test_get_entitlements_includes_machine_health_for_super_admin(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="super-admin-mh", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(
            return_value=_make_org(
                tenant_id="org-mh",
                premium_feature_grants_json=["machine_health"],
                entitlements_version=1,
            )
        ),
    )

    response = await async_client.get(
        "/api/v1/tenants/org-mh/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "machine_health" in payload["available_features"]
    assert "machine_health" in payload["premium_feature_grants"]
    assert payload["entitlements_version"] == 1


@pytest.mark.asyncio
async def test_get_entitlements_machine_health_flows_to_all_role_effective_features(client, monkeypatch):
    async_client, _ = client
    caller = _make_user(user_id="super-admin-mh2", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(
            return_value=_make_org(
                tenant_id="org-mh2",
                premium_feature_grants_json=["machine_health"],
                entitlements_version=1,
            )
        ),
    )

    response = await async_client.get(
        "/api/v1/tenants/org-mh2/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    for role_name in ("org_admin", "plant_manager", "operator", "viewer"):
        assert "machine_health" in payload["effective_features_by_role"][role_name], f"{role_name} missing machine_health"


@pytest.mark.asyncio
async def test_super_admin_can_grant_machine_health_via_put_entitlements(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="super-admin-mh3", tenant_id=None, role=UserRole.SUPER_ADMIN)
    access_token = TokenService().create_access_token(caller, [])

    initial_org = _make_org(tenant_id=tenant_id, entitlements_version=0)
    granted_org = _make_org(
        tenant_id=tenant_id,
        premium_feature_grants_json=["machine_health"],
        entitlements_version=1,
    )

    monkeypatch.setattr(orgs_api.org_repo, "get_by_id", AsyncMock(side_effect=[initial_org, granted_org]))
    monkeypatch.setattr(orgs_api.org_repo, "update_entitlements", AsyncMock(return_value=granted_org))

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"premium_feature_grants": ["machine_health"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "machine_health" in payload["premium_feature_grants"]
    assert "machine_health" in payload["available_features"]
    assert payload["entitlements_version"] == 1


@pytest.mark.asyncio
async def test_org_admin_cannot_delegate_machine_health_to_plant_manager(client, monkeypatch):
    async_client, _ = client
    tenant_id = "SH00000001"
    caller = _make_user(user_id="org-admin-mh", tenant_id=tenant_id, role=UserRole.ORG_ADMIN)
    access_token = TokenService().create_access_token(caller, [], tenant_entitlements_version=1)

    monkeypatch.setattr(
        orgs_api.org_repo,
        "get_by_id",
        AsyncMock(
            return_value=_make_org(
                tenant_id=tenant_id,
                premium_feature_grants_json=["machine_health"],
                entitlements_version=1,
            )
        ),
    )

    response = await async_client.put(
        f"/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"role_feature_matrix": {"plant_manager": ["machine_health"], "operator": [], "viewer": []}},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_SCOPE_DENIED"
    assert "machine_health" in response.json()["detail"].get("invalid_features", [])
