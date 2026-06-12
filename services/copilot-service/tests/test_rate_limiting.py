from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))
SERVICE_ROOT = REPO_ROOT / "services" / "copilot-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MYSQL_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("MYSQL_READONLY_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("REDIS_URL", "memory://")

from src.api import chat as chat_api
from src.rate_limit import configure_rate_limiting
from src.response.schema import CopilotResponse
from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    configure_rate_limiting(app)
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=["copilot"],
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

    app.include_router(chat_api.router, dependencies=[Depends(require_feature("copilot"))])

    original_engine = chat_api.engine
    original_model_client = chat_api.model_client
    original_tariff = chat_api.get_current_tariff
    chat_api.model_client = None
    chat_api.engine = SimpleNamespace(
        process_question=AsyncMock(
            return_value=CopilotResponse(
                answer="ok",
                reasoning="ok",
                follow_up_suggestions=[],
            )
        )
    )
    chat_api.get_current_tariff = AsyncMock(return_value=(8.0, "INR"))

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.21", 123)),
        base_url="http://testserver",
    ) as async_client:
        yield async_client

    chat_api.engine = original_engine
    chat_api.model_client = original_model_client
    chat_api.get_current_tariff = original_tariff


@pytest.mark.asyncio
async def test_copilot_chat_is_rate_limited(client: AsyncClient):
    payload = {"message": "Summarize today's factory performance", "conversation_history": []}

    for _ in range(20):
        response = await client.post("/api/v1/copilot/chat", json=payload)
        assert response.status_code == 200

    response = await client.post("/api/v1/copilot/chat", json=payload)

    assert response.status_code == 429
