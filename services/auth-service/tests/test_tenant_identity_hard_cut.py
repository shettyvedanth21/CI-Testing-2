import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
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

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.orgs import router as orgs_router
from app.database import Base, get_db
from app.models.auth import (
    TENANT_ID_PREFIX,
    AuthActionToken,
    AuthActionType,
    Organization,
    TenantIdSequence,
    User,
    UserRole,
)
from app.rate_limit import configure_rate_limiting
from app.repositories.org_repository import OrgRepository
from app.repositories.plant_repository import PlantRepository
from app.repositories.user_repository import UserRepository
from app.services import auth_service as auth_service_module
from app.services.action_token_service import ActionTokenService
from app.services.token_service import TokenService
from shared import auth_middleware as middleware


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


def _assert_not_uuid_like(value: str) -> None:
    with pytest.raises(ValueError):
        UUID(value)


async def _seed_allocator(session, next_value: int = 1) -> None:
    session.add(TenantIdSequence(prefix=TENANT_ID_PREFIX, next_value=next_value))
    await session.commit()


async def _create_super_admin(session, *, email: str = "super-admin@example.com", password: str = "SuperSecret123!") -> User:
    user = User(
        email=email,
        hashed_password=auth_service_module.pwd_ctx.hash(password),
        full_name="Super Admin",
        role=UserRole.SUPER_ADMIN,
        tenant_id=None,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _create_org_admin(session, tenant_id: str, *, email: str = "org-admin@example.com", password: str = "OrgAdmin123!") -> User:
    user = User(
        email=email,
        hashed_password=auth_service_module.pwd_ctx.hash(password),
        full_name="Org Admin",
        role=UserRole.ORG_ADMIN,
        tenant_id=tenant_id,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_db(tmp_path, monkeypatch):
    db_path = tmp_path / "auth-hard-cut.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    fake_redis = FakeRedis()
    monkeypatch.setattr(TokenService, "_get_redis_client", lambda self: fake_redis)
    monkeypatch.setattr(middleware, "_get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(auth_service_module.pwd_ctx, "hash", lambda secret: f"hashed::{secret}")
    monkeypatch.setattr(auth_service_module.pwd_ctx, "verify", lambda plain, hashed: hashed == f"hashed::{plain}")

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()
    configure_rate_limiting(app)
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(orgs_router)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield {
            "client": client,
            "engine": engine,
            "session_factory": session_factory,
            "redis": fake_redis,
            "app": app,
        }

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_tenant_creation_returns_sh_prefixed_ids(auth_db):
    session_factory = auth_db["session_factory"]
    client = auth_db["client"]

    async with session_factory() as session:
        await _seed_allocator(session, next_value=1)
        super_admin = await _create_super_admin(session)

    access_token = TokenService().create_access_token(super_admin, [])
    response = await client.post(
        "/api/admin/tenants",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "Shivex Alpha", "slug": "shivex-alpha"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] == "SH00000001"
    _assert_not_uuid_like(payload["id"])

    async with session_factory() as session:
        org = await session.get(Organization, "SH00000001")
        sequence_row = await session.get(TenantIdSequence, TENANT_ID_PREFIX)

    assert org is not None
    assert sequence_row is not None
    assert sequence_row.next_value == 2


@pytest.mark.asyncio
async def test_concurrent_tenant_creation_does_not_duplicate_ids(tmp_path):
    db_path = tmp_path / "tenant-id-concurrency.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        await _seed_allocator(session, next_value=1)

    repo = OrgRepository()

    async def _create(index: int) -> str:
        async with session_factory() as session:
            org = await repo.create(session, name=f"Tenant {index}", slug=f"tenant-{index}")
            await session.commit()
            return org.id

    ids = await asyncio.gather(*(_create(index) for index in range(1, 6)))
    assert sorted(ids) == ["SH00000001", "SH00000002", "SH00000003", "SH00000004", "SH00000005"]
    assert len(set(ids)) == 5

    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_owned_auth_rows_persist_string10_tenant_ids(auth_db):
    session_factory = auth_db["session_factory"]

    async with session_factory() as session:
        await _seed_allocator(session, next_value=1)
        org = await OrgRepository().create(session, "Shivex Plants", "shivex-plants")
        user = await UserRepository().create(
            session,
            email="viewer@example.com",
            hashed_password="hashed-password",
            role=UserRole.VIEWER,
            tenant_id=org.id,
            full_name="Viewer User",
        )
        plant = await PlantRepository().create(
            session,
            tenant_id=org.id,
            name="Main Plant",
            location="Line 1",
            timezone="Asia/Kolkata",
        )
        raw_token = await ActionTokenService().create_token(
            session,
            user_id=user.id,
            action_type=AuthActionType.PASSWORD_RESET,
            expires_in_minutes=30,
            created_by_user_id=user.id,
            created_by_role=user.role.value,
            tenant_id=org.id,
            metadata={"source": "test"},
        )
        await session.commit()
        token_row = (
            await session.execute(select(AuthActionToken).where(AuthActionToken.user_id == user.id))
        ).scalar_one()

    assert org.id == "SH00000001"
    assert len(org.id) == 10
    assert user.tenant_id == org.id
    assert len(user.tenant_id or "") == 10
    assert plant.tenant_id == org.id
    assert len(plant.tenant_id) == 10
    assert token_row.tenant_id == org.id
    assert len(token_row.tenant_id or "") == 10
    assert raw_token


@pytest.mark.asyncio
async def test_login_and_me_keep_sh_tenant_ids_in_tokens_and_payloads(auth_db):
    session_factory = auth_db["session_factory"]
    client = auth_db["client"]

    async with session_factory() as session:
        await _seed_allocator(session, next_value=1)
        org = await OrgRepository().create(session, "Shivex Login", "shivex-login")
        await session.commit()
        await session.refresh(org)
        await _create_org_admin(session, org.id)

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "org-admin@example.com", "password": "OrgAdmin123!"},
    )
    assert login_response.status_code == 200
    login_payload = login_response.json()
    claims = TokenService().decode_access_token(login_payload["access_token"])
    assert claims["tenant_id"] == "SH00000001"

    me_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login_payload['access_token']}"},
    )
    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["user"]["tenant_id"] == "SH00000001"
    assert me_payload["tenant"]["id"] == "SH00000001"


@pytest.mark.asyncio
async def test_tenant_scoped_routes_work_with_sh_tenant_ids(auth_db):
    session_factory = auth_db["session_factory"]
    client = auth_db["client"]

    async with session_factory() as session:
        await _seed_allocator(session, next_value=1)
        org = await OrgRepository().create(session, "Shivex Ops", "shivex-ops")
        await session.commit()
        await session.refresh(org)
        admin_user = await _create_org_admin(session, org.id)

    access_token = TokenService().create_access_token(admin_user, [])
    tenant_id = "SH00000001"

    create_plant_response = await client.post(
        f"/api/v1/tenants/{tenant_id}/plants",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "Plant One", "location": "Line A", "timezone": "Asia/Kolkata"},
    )
    assert create_plant_response.status_code == 201
    plant_payload = create_plant_response.json()
    assert plant_payload["tenant_id"] == tenant_id

    create_user_response = await client.post(
        f"/api/v1/tenants/{tenant_id}/users",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "email": "viewer@example.com",
            "password": "Viewer1234!",
            "full_name": "Viewer",
            "role": "viewer",
            "tenant_id": tenant_id,
            "plant_ids": [plant_payload["id"]],
        },
    )
    assert create_user_response.status_code == 201
    assert create_user_response.json()["tenant_id"] == tenant_id

    list_plants_response = await client.get(
        f"/api/v1/tenants/{tenant_id}/plants",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert list_plants_response.status_code == 200
    assert [item["tenant_id"] for item in list_plants_response.json()] == [tenant_id]

    list_users_response = await client.get(
        f"/api/v1/tenants/{tenant_id}/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert list_users_response.status_code == 200
    assert any(item["tenant_id"] == tenant_id for item in list_users_response.json())
