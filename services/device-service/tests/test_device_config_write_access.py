from __future__ import annotations

import os
import sys
from importlib import util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (BASE_DIR, REPO_ROOT, SERVICES_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

_APP_SPEC = util.spec_from_file_location("device_service_app_init", BASE_DIR / "app" / "__init__.py")
assert _APP_SPEC and _APP_SPEC.loader
device_app_module = util.module_from_spec(_APP_SPEC)
_APP_SPEC.loader.exec_module(device_app_module)
from app.api.v1 import devices as devices_api
from app.api.v1.router import api_router
from app.database import Base, get_db
from app.models.device import Device
from shared.feature_entitlements import build_feature_entitlement_state
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def device_app():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            Device(
                device_id="AD00000012",
                tenant_id="ORG-1",
                plant_id="PLANT-1",
                device_name="Scoped Compressor",
                device_type="compressor",
                device_id_class="active",
                data_source_type="metered",
                legacy_status="active",
            )
        )
        session.add(
            Device(
                device_id="AD00000013",
                tenant_id="ORG-1",
                plant_id="PLANT-2",
                device_name="Foreign Plant Compressor",
                device_type="compressor",
                device_id_class="active",
                data_source_type="metered",
                legacy_status="active",
            )
        )
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _attach_test_entitlements(request, call_next):
        auth_state = devices_api.get_auth_state(request)
        role = str(auth_state.get("role") or "viewer")
        grants_header = request.headers.get("X-Test-Premium-Grants", "")
        grants = [item.strip() for item in grants_header.split(",") if item.strip()]
        role_feature_matrix = {"plant_manager": [], "operator": [], "viewer": []}
        if "waste_analysis" in grants:
            role_feature_matrix["plant_manager"] = ["waste_analysis"]
        entitlements = build_feature_entitlement_state(
            role=role,
            premium_feature_grants=grants,
            role_feature_matrix=role_feature_matrix,
            entitlements_version=1 if grants else 0,
        )
        request.state.tenant_context = TenantContext(
            tenant_id=str(auth_state.get("tenant_id") or "ORG-1"),
            user_id=str(auth_state.get("user_id") or "user-1"),
            role=role,
            plant_ids=[str(plant_id) for plant_id in (auth_state.get("plant_ids") or [])],
            is_super_admin=bool(auth_state.get("is_super_admin")),
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        request.state.is_authenticated = True
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")
    app.add_exception_handler(
        device_app_module.RequestValidationError,
        device_app_module.validation_exception_handler,
    )
    app.add_exception_handler(
        device_app_module.HTTPException,
        device_app_module.http_exception_handler,
    )
    app.add_exception_handler(
        Exception,
        device_app_module.unhandled_exception_handler,
    )

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    try:
        yield app
    finally:
        await engine.dispose()


def _apply_auth(monkeypatch, role: str) -> None:
    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": "user-1",
            "tenant_id": "ORG-1",
            "role": role,
            "plant_ids": ["PLANT-1"],
            "is_authenticated": True,
        },
    )
    monkeypatch.setattr(
        devices_api.TenantContext,
        "from_request",
        classmethod(
            lambda cls, request: TenantContext(
                tenant_id="ORG-1",
                user_id="user-1",
                role=role,
                plant_ids=["PLANT-1"],
                is_super_admin=False,
            )
        ),
    )
    monkeypatch.setattr(devices_api, "_list_tenant_plants", AsyncMock(return_value=[{"id": "PLANT-1", "name": "Plant 1"}]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url", "payload"),
    [
        ("post", "/api/v1/devices/AD00000012/idle-config", {"idle_current_threshold": 4.5}),
        (
            "post",
            "/api/v1/devices/AD00000012/shifts",
            {
                "shift_name": "Morning",
                "shift_start": "08:00:00",
                "shift_end": "16:00:00",
                "maintenance_break_minutes": 0,
                "day_of_week": 0,
                "is_active": True,
            },
        ),
        ("put", "/api/v1/devices/AD00000012/dashboard-widgets", {"selected_fields": []}),
        (
            "post",
            "/api/v1/devices/AD00000012/health-config",
            {
                "parameter_name": "current",
                "normal_min": 0,
                "normal_max": 10,
                "weight": 100,
                "ignore_zero_value": False,
                "is_active": True,
            },
        ),
    ],
)
async def test_operator_cannot_write_device_detail_configuration(device_app, monkeypatch, method, url, payload):
    _apply_auth(monkeypatch, "operator")

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        response = await getattr(client, method)(
            url,
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json=payload,
        )

    body = response.json()
    assert response.status_code == 403
    assert body["code"] == "FORBIDDEN"
    assert "operator" in body["message"]


@pytest.mark.asyncio
async def test_operator_cannot_write_waste_config_returns_feature_disabled(device_app, monkeypatch):
    _apply_auth(monkeypatch, "operator")

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        response = await client.put(
            "/api/v1/devices/AD00000012/waste-config",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={"overconsumption_current_threshold_a": 9.5},
        )

    body = response.json()
    assert response.status_code == 403
    assert body["code"] == "FEATURE_DISABLED"


@pytest.mark.asyncio
async def test_plant_manager_can_still_save_idle_threshold(device_app, monkeypatch):
    _apply_auth(monkeypatch, "plant_manager")

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/AD00000012/idle-config",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={"idle_current_threshold": 4.5},
        )

    assert response.status_code != 403


@pytest.mark.asyncio
async def test_plant_manager_cannot_read_out_of_scope_current_state(device_app, monkeypatch):
    _apply_auth(monkeypatch, "plant_manager")
    get_current_state = AsyncMock(
        return_value={
            "device_id": "AD00000013",
            "state": "unknown",
            "current": 10.0,
            "voltage": 230.0,
            "threshold": 1.0,
            "timestamp": None,
            "current_field": "current",
            "voltage_field": "voltage",
        }
    )
    monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_current_state", get_current_state)

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/AD00000013/current-state",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
        )

    body = response.json()
    assert response.status_code == 403
    assert body["code"] == "PLANT_ACCESS_DENIED"
    get_current_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_plant_manager_can_read_current_state_for_assigned_device(device_app, monkeypatch):
    _apply_auth(monkeypatch, "plant_manager")
    get_current_state = AsyncMock(
        return_value={
            "device_id": "AD00000012",
            "state": "running",
            "current": 12.5,
            "voltage": 231.5,
            "threshold": 4.7,
            "timestamp": "2026-04-08T14:00:00Z",
            "current_field": "current",
            "voltage_field": "voltage",
        }
    )
    monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_current_state", get_current_state)

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/AD00000012/current-state",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["device_id"] == "AD00000012"
    assert body["state"] == "running"
    get_current_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_plant_manager_cannot_write_out_of_scope_idle_threshold(device_app, monkeypatch):
    _apply_auth(monkeypatch, "plant_manager")
    set_idle_config = AsyncMock(
        return_value={
            "device_id": "AD00000013",
            "idle_current_threshold": 4.5,
            "configured": True,
        }
    )
    monkeypatch.setattr("app.services.idle_running.IdleRunningService.set_idle_config", set_idle_config)

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/AD00000013/idle-config",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={"idle_current_threshold": 4.5},
        )

    body = response.json()
    assert response.status_code == 403
    assert body["code"] == "PLANT_ACCESS_DENIED"
    set_idle_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_admin_cannot_read_waste_config_without_waste_analysis_entitlement(device_app, monkeypatch):
    _apply_auth(monkeypatch, "org_admin")
    get_waste_config = AsyncMock(return_value={"device_id": "AD00000012", "overconsumption_current_threshold_a": 9.5})
    monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_waste_config", get_waste_config)

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/AD00000012/waste-config",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
        )

    body = response.json()
    assert response.status_code == 403
    assert body["code"] == "FEATURE_DISABLED"
    get_waste_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_admin_cannot_write_waste_config_without_waste_analysis_entitlement(device_app, monkeypatch):
    _apply_auth(monkeypatch, "org_admin")
    set_waste_config = AsyncMock(return_value={"device_id": "AD00000012", "overconsumption_current_threshold_a": 9.5})
    monkeypatch.setattr("app.services.idle_running.IdleRunningService.set_waste_config", set_waste_config)

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        response = await client.put(
            "/api/v1/devices/AD00000012/waste-config",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={"overconsumption_current_threshold_a": 9.5},
        )

    body = response.json()
    assert response.status_code == 403
    assert body["code"] == "FEATURE_DISABLED"
    set_waste_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_admin_can_read_and_write_waste_config_with_waste_analysis_entitlement(device_app, monkeypatch):
    _apply_auth(monkeypatch, "org_admin")
    get_waste_config = AsyncMock(return_value={"device_id": "AD00000012", "overconsumption_current_threshold_a": 9.5})
    set_waste_config = AsyncMock(return_value={"device_id": "AD00000012", "overconsumption_current_threshold_a": 9.5})
    refresh = AsyncMock()
    monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_waste_config", get_waste_config)
    monkeypatch.setattr("app.services.idle_running.IdleRunningService.set_waste_config", set_waste_config)
    monkeypatch.setattr(devices_api, "_refresh_loss_views_after_waste_config_change", refresh)

    headers = {
        "X-Tenant-Id": "ORG-1",
        "Authorization": "Bearer token",
        "X-Test-Premium-Grants": "waste_analysis",
    }

    async with AsyncClient(transport=ASGITransport(app=device_app), base_url="http://testserver") as client:
        read_response = await client.get("/api/v1/devices/AD00000012/waste-config", headers=headers)
        write_response = await client.put(
            "/api/v1/devices/AD00000012/waste-config",
            headers=headers,
            json={"overconsumption_current_threshold_a": 9.5},
        )

    assert read_response.status_code == 200
    assert write_response.status_code == 200
    get_waste_config.assert_awaited_once()
    set_waste_config.assert_awaited_once()
    refresh.assert_awaited_once()
