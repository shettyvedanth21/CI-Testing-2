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


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "memory://")

from src.database import get_db
from src.handlers import energy_router
from src.handlers import energy_reports as energy_reports_api
from src.rate_limit import configure_rate_limiting
from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature
from services.shared.tenant_context import TenantContext


class _FakeReportRepository:
    def __init__(self, *_args, **_kwargs) -> None:
        self.find_active_duplicate = AsyncMock(return_value=None)
        self.create_report = AsyncMock(return_value=None)
        self.update_report = AsyncMock(return_value=None)


class _FakeQueue:
    def __init__(self) -> None:
        self.enqueue = AsyncMock(return_value=None)


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch):
    app = FastAPI()
    configure_rate_limiting(app)
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=["reports"],
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

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(
        energy_router,
        prefix="/api/reports/energy",
        dependencies=[Depends(require_feature("reports"))],
    )

    monkeypatch.setattr(energy_reports_api, "resolve_submission_tenant_id", lambda *_args, **_kwargs: "SH00000001")
    monkeypatch.setattr(energy_reports_api, "validate_device_for_reporting", AsyncMock(return_value=None))
    monkeypatch.setattr(energy_reports_api, "enforce_report_admission", AsyncMock(return_value=0))
    monkeypatch.setattr(energy_reports_api, "ReportRepository", _FakeReportRepository)
    monkeypatch.setattr(energy_reports_api, "get_report_queue", lambda: _FakeQueue())

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.23", 123)),
        base_url="http://testserver",
    ) as async_client:
        yield async_client

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reporting_consumption_submission_is_rate_limited(client: AsyncClient):
    payload = {
        "device_id": "DEVICE-1",
        "start_date": "2026-05-01",
        "end_date": "2026-05-02",
        "tenant_id": "SH00000001",
    }

    for _ in range(10):
        response = await client.post("/api/reports/energy/consumption", json=payload)
        assert response.status_code == 200

    response = await client.post("/api/reports/energy/consumption", json=payload)

    assert response.status_code == 429
