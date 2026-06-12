from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from src.database import get_db  # noqa: E402
from src.handlers import settings_router  # noqa: E402
from src.models.tenant_tariffs import Base as TenantTariffBase  # noqa: E402
from src.repositories.tariff_repository import TariffRepository  # noqa: E402
from src.services.tenant_scope import build_service_tenant_context  # noqa: E402
from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature  # noqa: E402
from services.shared.tenant_context import TenantContext  # noqa: E402


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    db_path = tmp_path / "settings-history.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(TenantTariffBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


def _build_settings_app(session_factory) -> FastAPI:
    app = FastAPI()
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=[],
        role_feature_matrix={},
        entitlements_version=1,
    )

    @app.middleware("http")
    async def _inject_context(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="SH00000001",
            user_id="settings-admin",
            role="org_admin",
            plant_ids=[],
            is_super_admin=False,
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        return await call_next(request)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.include_router(
        settings_router,
        prefix="/api/v1/settings",
        dependencies=[Depends(require_feature("settings"))],
    )
    return app


@pytest.mark.asyncio
async def test_activate_tariff_version_creates_new_active_revision(session_factory):
    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 6.5,
                "currency": "INR",
                "effective_start_at": datetime(2026, 5, 1, 0, 0, 0),
                "created_by": "finance-a",
            },
        )
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 7.25,
                "currency": "INR",
                "effective_start_at": datetime(2026, 5, 2, 0, 0, 0),
                "created_by": "finance-b",
            },
        )
        versions_before = await repo.list_versions("SH00000001")

        current = await repo.activate_version(
            version_id=versions_before[0].id,
            tenant_id="SH00000001",
            activated_by="ops-reactivate",
            effective_start_at=datetime(2026, 5, 3, 0, 0, 0),
        )
        versions_after = await repo.list_versions("SH00000001")
        active = await repo.get_effective_version("SH00000001", effective_at=datetime(2026, 5, 3, 1, 0, 0))

    assert float(current.energy_rate_per_kwh) == 6.5
    assert len(versions_after) == 3
    assert versions_after[-1].version_number == 3
    assert float(versions_after[-1].energy_rate_per_kwh) == 6.5
    assert versions_after[-1].created_by == "ops-reactivate"
    assert active is not None
    assert active.id == versions_after[-1].id


@pytest.mark.asyncio
async def test_settings_tariff_history_api_supports_history_and_activation(session_factory):
    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 6.5,
                "currency": "INR",
                "effective_start_at": datetime(2026, 5, 1, 0, 0, 0),
                "created_by": "settings-ui",
                "updated_by": "settings-ui",
            },
        )
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 7.0,
                "currency": "INR",
                "effective_start_at": datetime(2026, 5, 2, 0, 0, 0),
                "created_by": "settings-ui",
                "updated_by": "settings-ui",
            },
        )

    app = _build_settings_app(session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        empty_history = await client.get("/api/v1/settings/tariff/history", headers={"X-Tenant-Id": "SH00000001"})
        assert empty_history.status_code == 200
        history_before = empty_history.json()["versions"]
        assert len(history_before) == 2
        assert history_before[0]["rate"] == 7.0
        assert history_before[0]["is_active"] is True
        old_version_id = next(version["id"] for version in history_before if version["rate"] == 6.5)

        activated = await client.patch(
            f"/api/v1/settings/tariff/history/{old_version_id}/activate",
            headers={"X-Tenant-Id": "SH00000001"},
        )
        assert activated.status_code == 200
        activated_payload = activated.json()
        assert activated_payload["rate"] == 6.5
        assert activated_payload["is_active"] is True

        history_after_response = await client.get(
            "/api/v1/settings/tariff/history",
            headers={"X-Tenant-Id": "SH00000001"},
        )

    assert history_after_response.status_code == 200
    history_after = history_after_response.json()["versions"]
    assert len(history_after) == 3
    assert history_after[0]["rate"] == 6.5
    assert history_after[0]["is_active"] is True
    assert history_after[0]["updated_by"] == "settings-admin"
    assert any(version["id"] == old_version_id and version["is_active"] is False for version in history_after)
