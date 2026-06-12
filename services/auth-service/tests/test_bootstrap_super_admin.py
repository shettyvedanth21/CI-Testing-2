import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path = [p for p in sys.path if p not in {str(AUTH_SERVICE_ROOT), str(REPO_ROOT)}]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(AUTH_SERVICE_ROOT))

for module_name in list(sys.modules):
    if module_name == "app" or module_name.startswith("app."):
        del sys.modules[module_name]

os.chdir(AUTH_SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.models.auth import Base, Organization, Plant, TenantIdSequence, User, UserRole
from app import main as app_main
from app.services import bootstrap_service
from app.services.bootstrap_service import (
    ensure_bootstrap_super_admin,
    ensure_local_bootstrap_state,
    ensure_tenant_allocator_state,
)
from app.config import settings


def test_bootstrap_defaults_are_safe():
    assert settings.BOOTSTRAP_SUPER_ADMIN_ENABLED is False
    assert settings.BOOTSTRAP_SUPER_ADMIN_EMAIL == ""
    assert settings.BOOTSTRAP_SUPER_ADMIN_PASSWORD == ""
    assert settings.BOOTSTRAP_SUPER_ADMIN_FULL_NAME == ""
    assert settings.LOCAL_BOOTSTRAP_ENABLED is False


def test_validate_bootstrap_contract_blocks_local_bootstrap_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_ENABLED", False)

    with pytest.raises(RuntimeError, match="LOCAL_BOOTSTRAP_ENABLED cannot be true in production"):
        app_main.validate_bootstrap_contract()


def test_validate_bootstrap_contract_requires_explicit_super_admin_values(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", False)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_ENABLED", True)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_EMAIL", "")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_PASSWORD", "")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_FULL_NAME", "")

    with pytest.raises(RuntimeError, match="Missing bootstrap super-admin settings"):
        app_main.validate_bootstrap_contract()


def test_validate_bootstrap_contract_allows_explicit_super_admin_enable(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", False)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_ENABLED", True)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_PASSWORD", "secure-bootstrap-password")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_FULL_NAME", "Bootstrap Admin")

    app_main.validate_bootstrap_contract()


@pytest.mark.asyncio
async def test_ensure_bootstrap_super_admin_creates_exactly_one_user(monkeypatch):
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_ENABLED", True)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_EMAIL", "manash.ray@cittagent.com")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_PASSWORD", "Shivex@2706")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_FULL_NAME", "Shivex Super-Admin")
    monkeypatch.setattr(bootstrap_service.pwd_ctx, "hash", lambda secret: f"hashed::{secret}")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        created = await ensure_bootstrap_super_admin(session)
        assert created is True

    async with session_factory() as session:
        created_again = await ensure_bootstrap_super_admin(session)
        assert created_again is False
        users = (await session.execute(select(User).order_by(User.email.asc()))).scalars().all()
        assert len(users) == 1
        assert users[0].email == "manash.ray@cittagent.com"
        assert users[0].hashed_password == "hashed::Shivex@2706"
        assert users[0].role == UserRole.SUPER_ADMIN
        assert users[0].is_active is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_bootstrap_super_admin_updates_configured_identity_from_env(monkeypatch):
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_ENABLED", True)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_EMAIL", "bootstrap@example.com")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_PASSWORD", "InitialSecure@123")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_FULL_NAME", "Bootstrap Admin")
    monkeypatch.setattr(bootstrap_service.pwd_ctx, "hash", lambda secret: f"hashed::{secret}")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        created = await ensure_bootstrap_super_admin(session)
        assert created is True

    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_PASSWORD", "RotatedSecure@456")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPER_ADMIN_FULL_NAME", "Bootstrap Admin Renamed")

    async with session_factory() as session:
        created_again = await ensure_bootstrap_super_admin(session)
        assert created_again is False

    async with session_factory() as session:
        users = (await session.execute(select(User).order_by(User.email.asc()))).scalars().all()

    assert len(users) == 1
    assert users[0].email == "bootstrap@example.com"
    assert users[0].hashed_password == "hashed::RotatedSecure@456"
    assert users[0].full_name == "Bootstrap Admin Renamed"
    assert users[0].role == UserRole.SUPER_ADMIN
    assert users[0].tenant_id is None
    assert users[0].is_active is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_local_bootstrap_state_creates_deterministic_tenant_and_plant(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_ID", "SH00000001")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_NAME", "Shivex Demo Tenant")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_SLUG", "shivex-demo")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PLANT_ID", "demo-plant")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PLANT_NAME", "Demo Plant")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PLANT_LOCATION", "Local Demo Plant")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PLANT_TIMEZONE", "Asia/Kolkata")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PREMIUM_FEATURES", "reports,waste_analysis")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        result = await ensure_local_bootstrap_state(session)
        assert result == {
            "tenant_created": True,
            "tenant_updated": False,
            "plant_created": True,
            "plant_updated": False,
            "allocator_updated": True,
        }

    async with session_factory() as session:
        org = await session.scalar(select(Organization).where(Organization.id == "SH00000001"))
        plant = await session.scalar(select(Plant).where(Plant.id == "demo-plant"))
        allocator = await session.scalar(select(TenantIdSequence).where(TenantIdSequence.prefix == "SH"))

    assert org is not None
    assert org.slug == "shivex-demo"
    assert sorted(org.premium_feature_grants_json) == ["reports", "waste_analysis"]
    assert org.entitlements_version == 1
    assert plant is not None
    assert plant.tenant_id == "SH00000001"
    assert allocator is not None
    assert allocator.next_value == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_local_bootstrap_state_is_idempotent_and_merges_features(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_ID", "SH00000001")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_NAME", "Shivex Demo Tenant")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_SLUG", "shivex-demo")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PLANT_ID", "demo-plant")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PLANT_NAME", "Demo Plant")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PLANT_LOCATION", "Local Demo Plant")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PLANT_TIMEZONE", "Asia/Kolkata")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_PREMIUM_FEATURES", "reports,waste_analysis")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        session.add(
            Organization(
                id="SH00000001",
                name="Old Name",
                slug="shivex-demo",
                is_active=False,
                premium_feature_grants_json=["reports"],
                role_feature_matrix_json={},
                entitlements_version=4,
            )
        )
        session.add(
            Plant(
                id="demo-plant",
                tenant_id="SH00000001",
                name="Old Plant",
                location=None,
                timezone="UTC",
                is_active=False,
            )
        )
        session.add(TenantIdSequence(prefix="SH", next_value=1))
        await session.commit()

    async with session_factory() as session:
        first = await ensure_local_bootstrap_state(session)
        second = await ensure_local_bootstrap_state(session)

    assert first == {
        "tenant_created": False,
        "tenant_updated": True,
        "plant_created": False,
        "plant_updated": True,
        "allocator_updated": True,
    }
    assert second == {
        "tenant_created": False,
        "tenant_updated": False,
        "plant_created": False,
        "plant_updated": False,
        "allocator_updated": False,
    }

    async with session_factory() as session:
        orgs = (await session.execute(select(Organization))).scalars().all()
        plants = (await session.execute(select(Plant))).scalars().all()
        allocator = await session.scalar(select(TenantIdSequence).where(TenantIdSequence.prefix == "SH"))

    assert len(orgs) == 1
    assert len(plants) == 1
    assert sorted(orgs[0].premium_feature_grants_json) == ["reports", "waste_analysis"]
    assert orgs[0].is_active is True
    assert orgs[0].entitlements_version == 5
    assert plants[0].name == "Demo Plant"
    assert plants[0].timezone == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_ensure_tenant_allocator_state_reseeds_missing_sequence_row():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        updated = await ensure_tenant_allocator_state(session)
        assert updated is True

    async with session_factory() as session:
        allocator = await session.scalar(select(TenantIdSequence).where(TenantIdSequence.prefix == "SH"))

    assert allocator is not None
    assert allocator.next_value == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_tenant_allocator_state_advances_past_existing_org_ids():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        session.add_all(
            [
                Organization(id="SH00000002", name="Org 2", slug="org-2", is_active=True),
                Organization(id="SH00000007", name="Org 7", slug="org-7", is_active=True),
                Organization(id="LEGACY", name="Legacy Org", slug="legacy", is_active=True),
            ]
        )
        session.add(TenantIdSequence(prefix="SH", next_value=3))
        await session.commit()

    async with session_factory() as session:
        updated = await ensure_tenant_allocator_state(session)
        assert updated is True

    async with session_factory() as session:
        allocator = await session.scalar(select(TenantIdSequence).where(TenantIdSequence.prefix == "SH"))

    assert allocator is not None
    assert allocator.next_value == 8

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_tenant_allocator_state_skips_when_schema_is_not_bootstrapped():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        updated = await ensure_tenant_allocator_state(session)

    assert updated is False

    await engine.dispose()


def test_local_bootstrap_default_features_include_copilot() -> None:
    assert "copilot" in settings.local_bootstrap_premium_features
