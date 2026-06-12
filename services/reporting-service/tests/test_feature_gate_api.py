from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from src.database import get_db
from src.handlers import common_router, settings_router
from src.handlers import report_common as report_common_module
from src.handlers import settings as settings_module
from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature
from services.shared.tenant_context import TenantContext


def test_reporting_history_api_blocks_requests_without_reports_entitlement():
    app = FastAPI()
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=[],
        role_feature_matrix={},
        entitlements_version=0,
    )

    @app.middleware("http")
    async def _inject_context(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="SH00000001",
            user_id="user-1",
            role="org_admin",
            plant_ids=[],
            is_super_admin=False,
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        return await call_next(request)

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(
        common_router,
        prefix="/api/reports",
        dependencies=[Depends(require_feature("reports"))],
    )

    response = TestClient(app).get("/api/reports/history")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_DISABLED"


def test_reporting_history_api_returns_503_for_malformed_entitlement_state():
    app = FastAPI()

    @app.middleware("http")
    async def _inject_context(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="SH00000001",
            user_id="user-1",
            role="org_admin",
            plant_ids=[],
            is_super_admin=False,
            entitlements="FeatureEntitlementState(premium_feature_grants=('reports',))",
        )
        request.state.feature_entitlements = "FeatureEntitlementState(premium_feature_grants=('reports',))"
        return await call_next(request)

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(
        common_router,
        prefix="/api/reports",
        dependencies=[Depends(require_feature("reports"))],
    )

    response = TestClient(app).get("/api/reports/history")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AUTH_STATE_UNAVAILABLE"


def test_reporting_org_admin_surfaces_load_for_history_schedules_and_tariff(monkeypatch):
    app = FastAPI()
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=["reports"],
        role_feature_matrix={},
        entitlements_version=1,
    )

    @app.middleware("http")
    async def _inject_context(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="SH00000001",
            user_id="user-1",
            role="org_admin",
            plant_ids=[],
            is_super_admin=False,
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        return await call_next(request)

    async def _fake_db():
        yield object()

    monkeypatch.setattr(report_common_module, "_resolve_accessible_device_ids", AsyncMock(return_value=None))
    monkeypatch.setattr(report_common_module.ReportRepository, "list_reports", AsyncMock(return_value=[]))
    monkeypatch.setattr(report_common_module.ScheduledRepository, "list_schedules", AsyncMock(return_value=[]))
    monkeypatch.setattr(settings_module.TariffRepository, "get_tariff", AsyncMock(return_value=None))
    monkeypatch.setattr(settings_module.TariffRepository, "get_effective_version", AsyncMock(return_value=None))
    monkeypatch.setattr(settings_module.TariffRepository, "list_versions", AsyncMock(return_value=[]))

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(
        common_router,
        prefix="/api/reports",
        dependencies=[Depends(require_feature("reports"))],
    )
    app.include_router(
        settings_router,
        prefix="/api/v1/settings",
        dependencies=[Depends(require_feature("settings"))],
    )

    client = TestClient(app)

    history = client.get("/api/reports/history")
    schedules = client.get("/api/reports/schedules")
    tariff = client.get("/api/v1/settings/tariff")
    tariff_history = client.get("/api/v1/settings/tariff/history")

    assert history.status_code == 200
    assert history.json() == {"reports": []}
    assert schedules.status_code == 200
    assert schedules.json() == {"schedules": []}
    assert tariff.status_code == 200
    assert tariff.json()["rate"] is None
    assert tariff_history.status_code == 200
    assert tariff_history.json() == {"versions": []}
