from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("REDIS_URL", "memory://")

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.api.routes import analytics as analytics_routes
from src.rate_limit import configure_rate_limiting
from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch):
    app = FastAPI()
    configure_rate_limiting(app)
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=["analytics"],
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

    app.include_router(
        analytics_routes.router,
        prefix="/api/v1/analytics",
        dependencies=[Depends(require_feature("analytics"))],
    )
    monkeypatch.setattr(
        analytics_routes.AccuracyEvaluator,
        "evaluate_failure_predictions",
        AsyncMock(return_value=SimpleNamespace(as_dict=lambda: {"status": "ok", "sample_size": 1})),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.22", 123)),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_analytics_accuracy_evaluate_is_rate_limited(client: AsyncClient):
    for _ in range(3):
        response = await client.post("/api/v1/analytics/accuracy/evaluate")
        assert response.status_code == 200

    response = await client.post("/api/v1/analytics/accuracy/evaluate")

    assert response.status_code == 429
