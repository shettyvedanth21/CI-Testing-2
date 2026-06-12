from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.api.dependencies import get_result_repository
from src.api.routes import analytics as analytics_routes
from shared.feature_entitlements import build_feature_entitlement_state, require_feature
from shared.tenant_context import TenantContext


def _client(*, granted: bool) -> TestClient:
    app = FastAPI()
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=["analytics"] if granted else [],
        role_feature_matrix={},
        entitlements_version=1 if granted else 0,
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

    app.include_router(
        analytics_routes.router,
        prefix="/api/v1/analytics",
        dependencies=[Depends(require_feature("analytics"))],
    )
    app.dependency_overrides[get_result_repository] = lambda: SimpleNamespace(list_jobs=AsyncMock(return_value=[]))
    return TestClient(app)


def test_analytics_api_blocks_requests_without_entitlement():
    response = _client(granted=False).get("/api/v1/analytics/jobs")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_DISABLED"


def test_analytics_api_allows_requests_with_entitlement():
    response = _client(granted=True).get("/api/v1/analytics/jobs")

    assert response.status_code == 200
    assert response.json() == []


def test_analytics_models_api_allows_requests_with_entitlement():
    response = _client(granted=True).get("/api/v1/analytics/models")

    assert response.status_code == 200
    payload = response.json()
    assert "anomaly_detection" in payload
    assert "failure_prediction" in payload
