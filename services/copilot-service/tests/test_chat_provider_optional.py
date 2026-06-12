import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import Request

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from src.ai.copilot_engine import CopilotEngine
from src.ai.model_client import AIUnavailableError
from src.api import chat as chat_module
from src.response.schema import ChatRequest, CopilotResponse
from services.shared.tenant_context import TenantContext


class _UnavailableModelClient:
    def is_provider_configured(self) -> bool:
        return False

    def is_available(self) -> bool:
        return False

    async def generate(self, messages, max_tokens=1000):
        raise AssertionError("generate() should not run when provider is unavailable")


class _StubEngine:
    def __init__(self):
        self.calls: list[dict] = []

    async def process_question(self, **kwargs):
        self.calls.append(kwargs)
        return CopilotResponse(answer="curated-ok", reasoning="deterministic")


class _UnavailableEngine:
    async def process_question(self, **kwargs):
        raise AIUnavailableError("provider temporarily unavailable")


class _ExplodingEngine:
    async def process_question(self, **kwargs):
        raise RuntimeError("unexpected downstream failure")


def _request_with_tenant(tenant_id: str = "tenant-a") -> Request:
    req = Request(scope={"type": "http", "method": "POST", "path": "/api/v1/copilot/chat", "headers": []})
    req.state.tenant_context = TenantContext(
        tenant_id=tenant_id,
        user_id="u-1",
        role="tenant_admin",
        plant_ids=[],
        is_super_admin=False,
        entitlements=None,
    )
    return req


def test_curated_chat_works_when_provider_not_configured(monkeypatch):
    stub_engine = _StubEngine()
    unavailable_model = _UnavailableModelClient()
    monkeypatch.setattr(chat_module, "_get_engine", lambda: (unavailable_model, stub_engine))

    async def _fake_tariff(_tenant_id: str):
        return 8.5, "INR"

    monkeypatch.setattr(chat_module, "get_current_tariff", _fake_tariff)

    response = asyncio.run(
        chat_module.chat(
            _request_with_tenant(),
            ChatRequest(message="Summarize today's factory performance"),
        )
    )

    assert response.error_code is None
    assert response.answer == "curated-ok"
    assert stub_engine.calls
    assert stub_engine.calls[0]["message"] == "Summarize today's factory performance"


def test_unsupported_question_without_provider_returns_safe_fallback(monkeypatch):
    unavailable_model = _UnavailableModelClient()
    engine = CopilotEngine(model_client=unavailable_model)
    monkeypatch.setattr(chat_module, "_get_engine", lambda: (unavailable_model, engine))

    async def _fake_tariff(_tenant_id: str):
        return 8.5, "INR"

    monkeypatch.setattr(chat_module, "get_current_tariff", _fake_tariff)

    response = asyncio.run(
        chat_module.chat(
            _request_with_tenant(),
            ChatRequest(message="Show OEE by line"),
        )
    )

    assert response.error_code == "APPROVED_QUESTIONS_ONLY"
    assert response.answer == "This Copilot currently supports approved factory questions only."


def test_curated_questions_endpoint_works_without_provider():
    response = asyncio.run(chat_module.curated_questions(_request_with_tenant()))
    assert response.starter_questions


def test_chat_requires_auth_context_before_processing(monkeypatch):
    stub_engine = _StubEngine()
    monkeypatch.setattr(chat_module, "_get_engine", lambda: (object(), stub_engine))

    request = Request(scope={"type": "http", "method": "POST", "path": "/api/v1/copilot/chat", "headers": []})

    with pytest.raises(Exception) as exc:
        asyncio.run(
            chat_module.chat(
                request,
                ChatRequest(message="Summarize today's factory performance"),
            )
        )

    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "MISSING_AUTH_CONTEXT"
    assert not stub_engine.calls


def test_chat_requires_tenant_scope_before_processing(monkeypatch):
    stub_engine = _StubEngine()
    monkeypatch.setattr(chat_module, "_get_engine", lambda: (object(), stub_engine))

    request = Request(scope={"type": "http", "method": "POST", "path": "/api/v1/copilot/chat", "headers": []})
    request.state.tenant_context = TenantContext(
        tenant_id=None,
        user_id="u-1",
        role="super_admin",
        plant_ids=[],
        is_super_admin=True,
        entitlements=None,
    )

    with pytest.raises(Exception) as exc:
        asyncio.run(
            chat_module.chat(
                request,
                ChatRequest(message="Summarize today's factory performance"),
            )
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "TENANT_SCOPE_REQUIRED"
    assert not stub_engine.calls


def test_chat_returns_ai_unavailable_contract(monkeypatch):
    monkeypatch.setattr(chat_module, "_get_engine", lambda: (object(), _UnavailableEngine()))

    async def _fake_tariff(_tenant_id: str):
        return 8.5, "INR"

    monkeypatch.setattr(chat_module, "get_current_tariff", _fake_tariff)

    response = asyncio.run(
        chat_module.chat(
            _request_with_tenant(),
            ChatRequest(message="Summarize today's factory performance"),
        )
    )

    assert response.error_code == "AI_UNAVAILABLE"
    assert response.answer == "AI service is temporarily unavailable. Please try again."


def test_chat_returns_internal_error_contract_on_unexpected_failure(monkeypatch):
    monkeypatch.setattr(chat_module, "_get_engine", lambda: (object(), _ExplodingEngine()))

    async def _fake_tariff(_tenant_id: str):
        return 8.5, "INR"

    monkeypatch.setattr(chat_module, "get_current_tariff", _fake_tariff)

    response = asyncio.run(
        chat_module.chat(
            _request_with_tenant(),
            ChatRequest(message="Summarize today's factory performance"),
        )
    )

    assert response.error_code == "INTERNAL_ERROR"
    assert response.answer == "Something went wrong. Please try again."
