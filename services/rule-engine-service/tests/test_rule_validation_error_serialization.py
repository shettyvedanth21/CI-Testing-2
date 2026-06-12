from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = PROJECT_ROOT / "services"
RULE_ENGINE_DIR = SERVICES_DIR / "rule-engine-service"
for path in (PROJECT_ROOT, SERVICES_DIR, RULE_ENGINE_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("EMAIL_SMTP_HOST", "smtp.test.local")
os.environ.setdefault("EMAIL_SMTP_USERNAME", "tester")
os.environ.setdefault("EMAIL_SMTP_PASSWORD", "secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "shared-secret")
os.environ.setdefault("QUEUE_BACKEND", "memory")

from app import app
from app.database import Base, get_db
from shared.tenant_context import _sign_internal_service_request


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def client(session_factory, monkeypatch):
    async def _override_get_db():
        async with session_factory() as session:
            yield session

    async def _resolve_accessible_device_ids(self):
        return ["DEVICE-1"]

    async def _dispatch_alert(self, **kwargs):
        return SimpleNamespace(recipient_results=[])

    monkeypatch.setattr(
        "app.services.device_scope.DeviceScopeService.resolve_accessible_device_ids",
        _resolve_accessible_device_ids,
    )
    monkeypatch.setattr(
        "app.notifications.adapter.NotificationAdapter.dispatch_alert",
        _dispatch_alert,
    )

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
        test_client.close()


def _internal_headers(tenant_id: str = "TENANT-A") -> dict[str, str]:
    timestamp = int(time.time())
    service_name = "rule-engine-tests"
    signature = _sign_internal_service_request(service_name, tenant_id, timestamp)
    return {
        "X-Internal-Service": service_name,
        "X-Internal-Service-Timestamp": str(timestamp),
        "X-Internal-Service-Signature": signature,
        "X-Tenant-Id": tenant_id,
    }


def _valid_rule_payload() -> dict[str, object]:
    return {
        "rule_name": "High Power",
        "scope": "selected_devices",
        "device_ids": ["DEVICE-1"],
        "rule_type": "threshold",
        "property": "power",
        "condition": ">",
        "threshold": 10.0,
        "notification_channels": ["email"],
    }


def test_rule_creation_validation_error_returns_422_without_serialization_crash(client: TestClient) -> None:
    payload = _valid_rule_payload()
    payload.pop("device_ids")

    response = client.post("/api/v1/rules", json=payload, headers=_internal_headers())

    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "VALIDATION_ERROR"
    assert data["message"] == "Invalid request payload"
    assert data["details"][0]["ctx"]["error"] == {
        "type": "ValueError",
        "message": "device_ids is required when scope is 'selected_devices'",
    }


def test_rule_creation_nested_valueerror_validator_returns_json_safe_details(client: TestClient) -> None:
    payload = _valid_rule_payload()
    payload["notification_channels"] = ["sms"]
    payload["notification_recipients"] = [{"channel": "sms", "value": "not-a-phone"}]

    response = client.post("/api/v1/rules", json=payload, headers=_internal_headers())

    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "VALIDATION_ERROR"
    assert data["details"][0]["ctx"]["error"] == {
        "type": "ValueError",
        "message": "phone recipient must be an E.164-compatible phone number",
    }


def test_rule_creation_missing_required_field_still_returns_clean_422(client: TestClient) -> None:
    payload = _valid_rule_payload()
    payload.pop("rule_name")

    response = client.post("/api/v1/rules", json=payload, headers=_internal_headers())

    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "VALIDATION_ERROR"
    assert data["details"][0]["type"] == "missing"
    assert data["details"][0]["loc"] == ["body", "rule_name"]


def test_valid_rule_creation_still_returns_201(client: TestClient) -> None:
    response = client.post("/api/v1/rules", json=_valid_rule_payload(), headers=_internal_headers())

    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["data"]["rule_name"] == "High Power"
    assert data["data"]["device_ids"] == ["DEVICE-1"]
