from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = REPO_ROOT / "services" / "rule-engine-service"
SERVICES_ROOT = REPO_ROOT / "services"
sys.path = [path for path in sys.path if path not in {"", str(REPO_ROOT), str(SERVICES_ROOT), str(SERVICE_ROOT)}]
sys.path.insert(0, str(SERVICE_ROOT))
sys.path.insert(1, str(SERVICES_ROOT))

import app.api.v1.alerts as alerts_module
from app.api.v1.router import api_router
from app.database import get_db
from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature
from services.shared.tenant_context import TenantContext


def _client(monkeypatch) -> TestClient:
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

    monkeypatch.setattr(alerts_module, "_resolve_accessible_device_ids", AsyncMock(return_value=None))
    monkeypatch.setattr(alerts_module.ActivityEventRepository, "unread_count", AsyncMock(return_value=3))
    monkeypatch.setattr(alerts_module.ActivityEventRepository, "list_events", AsyncMock(return_value=([], 0)))

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(require_feature("rules"))])
    return TestClient(app)


def test_org_admin_alerts_unread_count_endpoint_returns_success(monkeypatch):
    response = _client(monkeypatch).get("/api/v1/alerts/events/unread-count")

    assert response.status_code == 200
    assert response.json()["data"]["count"] == 3


def test_org_admin_alerts_history_endpoint_returns_success(monkeypatch):
    response = _client(monkeypatch).get("/api/v1/alerts/events?page=1&page_size=25")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["total"] == 0
    assert payload["page"] == 1
    assert payload["page_size"] == 25
