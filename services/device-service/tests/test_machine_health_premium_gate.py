from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import sys

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.api.v1 import devices as devices_api
from app.api.v1.router import api_router
from app.database import Base, get_db
from app.models.device import Device, MachineHealthLatest
from services.shared.tenant_context import TenantContext
from services.shared.feature_entitlements import build_feature_entitlement_state


async def _build_app(monkeypatch: pytest.MonkeyPatch, *, machine_health_enabled: bool):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(Device(
            device_id="DEVICE-GATE",
            tenant_id="TENANT-GATE",
            plant_id="PLANT-GATE",
            device_name="Gate Test Device",
            device_type="compressor",
            device_id_class="active",
        ))
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id", "TENANT-GATE")
        role = request.headers.get("X-Role", "org_admin")
        premium_grants = ["machine_health"] if machine_health_enabled else []
        entitlements = build_feature_entitlement_state(
            role=role,
            premium_feature_grants=premium_grants,
            role_feature_matrix=None,
            entitlements_version=1 if machine_health_enabled else 0,
        )
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id,
            user_id=request.headers.get("X-User-Id", "user-1"),
            role=role,
            plant_ids=[request.headers.get("X-Plant-Id", "PLANT-GATE")],
            is_super_admin=role == "super_admin",
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        request.state.role = role
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": request.state.tenant_context.user_id,
            "tenant_id": request.state.tenant_context.tenant_id,
            "role": request.state.tenant_context.role,
            "plant_ids": list(request.state.tenant_context.plant_ids),
            "is_authenticated": True,
        },
    )

    return app, session_factory, engine


@pytest.mark.asyncio
async def test_degradation_score_denied_when_machine_health_disabled(monkeypatch):
    app, _, engine = await _build_app(monkeypatch, machine_health_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-GATE/degradation-score",
            headers={"X-Tenant-Id": "TENANT-GATE"},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_DISABLED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_degradation_score_allowed_when_machine_health_enabled(monkeypatch):
    app, _, engine = await _build_app(monkeypatch, machine_health_enabled=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-GATE/degradation-score",
            headers={"X-Tenant-Id": "TENANT-GATE"},
        )
    assert response.status_code == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_anomaly_activity_denied_when_machine_health_disabled(monkeypatch):
    app, _, engine = await _build_app(monkeypatch, machine_health_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-GATE/anomaly-activity",
            headers={"X-Tenant-Id": "TENANT-GATE"},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_DISABLED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_anomaly_activity_allowed_when_machine_health_enabled(monkeypatch):
    app, _, engine = await _build_app(monkeypatch, machine_health_enabled=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-GATE/anomaly-activity",
            headers={"X-Tenant-Id": "TENANT-GATE"},
        )
    assert response.status_code == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_anomaly_events_denied_when_machine_health_disabled(monkeypatch):
    app, _, engine = await _build_app(monkeypatch, machine_health_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-GATE/anomaly-events",
            headers={"X-Tenant-Id": "TENANT-GATE"},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_DISABLED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_anomaly_events_allowed_when_machine_health_enabled(monkeypatch):
    app, _, engine = await _build_app(monkeypatch, machine_health_enabled=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-GATE/anomaly-events",
            headers={"X-Tenant-Id": "TENANT-GATE"},
        )
    assert response.status_code == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_non_premium_device_endpoints_still_work_without_machine_health(monkeypatch):
    app, _, engine = await _build_app(monkeypatch, machine_health_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-GATE",
            headers={"X-Tenant-Id": "TENANT-GATE"},
        )
    assert response.status_code == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_denied_response_includes_machine_health_feature_message(monkeypatch):
    app, _, engine = await _build_app(monkeypatch, machine_health_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-GATE/degradation-score",
            headers={"X-Tenant-Id": "TENANT-GATE"},
        )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "FEATURE_DISABLED"
    assert "machine_health" in detail["message"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_internal_service_bypasses_machine_health_gate(monkeypatch):
    from services.shared.feature_entitlements import require_feature
    dep = require_feature("machine_health")
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=[],
        role_feature_matrix=None,
        entitlements_version=0,
    )
    internal_ctx = TenantContext(
        tenant_id="TENANT-GATE",
        user_id="scheduler",
        role="internal_service",
        plant_ids=["PLANT-GATE"],
        is_super_admin=False,
        entitlements=entitlements,
    )
    fake_request = type("Req", (), {
        "state": type("State", (), {
            "tenant_context": internal_ctx,
            "feature_entitlements": entitlements,
        })(),
    })()
    dep(fake_request)
