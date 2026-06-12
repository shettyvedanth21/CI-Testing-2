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

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("MINIO_EXTERNAL_URL", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio123")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from src.database import get_db
from src.handlers import waste_router
from src.handlers import waste_analysis as waste_analysis_module
from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature
from services.shared.tenant_context import TenantContext


def test_waste_history_api_blocks_requests_without_waste_entitlement():
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
        waste_router,
        prefix="/api/v1/waste",
        dependencies=[Depends(require_feature("waste_analysis"))],
    )

    response = TestClient(app).get("/api/v1/waste/analysis/history")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_DISABLED"


def test_waste_history_api_allows_org_admin_with_waste_entitlement(monkeypatch):
    app = FastAPI()
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=["waste_analysis"],
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

    monkeypatch.setattr(waste_analysis_module.WasteRepository, "list_jobs", AsyncMock(return_value=[]))

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(
        waste_router,
        prefix="/api/v1/waste",
        dependencies=[Depends(require_feature("waste_analysis"))],
    )

    response = TestClient(app).get("/api/v1/waste/analysis/history")

    assert response.status_code == 200
    assert response.json() == {"items": []}
