from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))
SERVICE_ROOT = REPO_ROOT / "services" / "copilot-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from src.api import chat as chat_module
from src.api.chat import router as chat_router
from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature
from services.shared.tenant_context import TenantContext


def _client(*, granted: bool) -> TestClient:
    app = FastAPI()
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=["copilot"] if granted else [],
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

    app.include_router(chat_router, dependencies=[Depends(require_feature("copilot"))])
    return TestClient(app)


def test_copilot_chat_blocks_requests_without_entitlement():
    response = _client(granted=False).post(
        "/api/v1/copilot/chat",
        json={"message": "Show me today's top idle waste machines", "conversation_history": []},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_DISABLED"


def test_copilot_chat_allows_requests_with_entitlement(monkeypatch):
    class _FakeEngine:
        async def process_question(self, **kwargs):
            return {"answer": "curated-ok", "reasoning": "deterministic", "error_code": None}

    async def _fake_tariff(_tenant_id: str):
        return 8.5, "INR"

    monkeypatch.setattr(chat_module, "_get_engine", lambda: (object(), _FakeEngine()))
    monkeypatch.setattr(chat_module, "get_current_tariff", _fake_tariff)

    response = _client(granted=True).post(
        "/api/v1/copilot/chat",
        json={"message": "Show me today's top idle waste machines", "conversation_history": []},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "curated-ok"
