from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
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
from app.schemas.rule import RuleCreate, RuleScope, RuleType, TelemetryPayload
from app.services.evaluator import RuleEvaluator
from shared.tenant_context import TenantContext, _sign_internal_service_request


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


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
        user_id="tester",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )


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
        "threshold": 100.0,
        "notification_channels": ["email"],
    }


def test_rule_create_normalizes_supported_threshold_property_alias() -> None:
    rule = RuleCreate(
        tenant_id="TENANT-A",
        rule_name="Active Power Guard",
        scope=RuleScope.SELECTED_DEVICES,
        device_ids=["DEVICE-1"],
        rule_type=RuleType.THRESHOLD,
        property="active_power",
        condition=">",
        threshold=10,
        notification_channels=["email"],
    )

    assert rule.property == "power"


def test_rule_create_rejects_unsupported_threshold_property() -> None:
    with pytest.raises(ValueError, match="Unsupported threshold property 'amps'"):
        RuleCreate(
            tenant_id="TENANT-A",
            rule_name="Bad Property",
            scope=RuleScope.SELECTED_DEVICES,
            device_ids=["DEVICE-1"],
            rule_type=RuleType.THRESHOLD,
            property="amps",
            condition=">",
            threshold=10,
            notification_channels=["email"],
        )


def test_threshold_evaluator_resolves_canonical_power_alias() -> None:
    evaluator = RuleEvaluator(SimpleNamespace(), _ctx())
    telemetry = TelemetryPayload(
        device_id="DEVICE-1",
        timestamp=datetime(2026, 4, 30, 9, 0, 0, tzinfo=timezone.utc),
        active_power=125.0,
    )

    actual_value = evaluator._extract_property_value(telemetry, "power")

    assert actual_value == pytest.approx(125.0)


def test_rule_create_and_evaluate_supported_property_via_api(client: TestClient) -> None:
    create_payload = _valid_rule_payload()
    create_payload["property"] = "active_power"

    create_response = client.post("/api/v1/rules", json=create_payload, headers=_internal_headers())

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["data"]["property"] == "power"

    evaluate_response = client.post(
        "/api/v1/rules/evaluate",
        json={
            "device_id": "DEVICE-1",
            "timestamp": "2026-04-30T09:05:00Z",
            "active_power": 150.0,
        },
        headers=_internal_headers(),
    )

    assert evaluate_response.status_code == 200
    data = evaluate_response.json()
    assert data["rules_evaluated"] == 1
    assert data["rules_triggered"] == 1
    assert data["results"][0]["actual_value"] == pytest.approx(150.0)


def test_unsupported_threshold_property_is_rejected_with_422_via_api(client: TestClient) -> None:
    payload = _valid_rule_payload()
    payload["property"] = "amps"

    response = client.post("/api/v1/rules", json=payload, headers=_internal_headers())

    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "VALIDATION_ERROR"
    assert data["details"][0]["ctx"]["error"] == {
        "type": "ValueError",
        "message": "Unsupported threshold property 'amps'. Supported properties: apparent_power, current, energy, frequency, power, power_factor, power_kw, reactive_power, run_hours, temperature, thd, voltage",
    }
