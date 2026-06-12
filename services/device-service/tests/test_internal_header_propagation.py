from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "test-internal-secret")

from app.database import Base
from app.services.dashboard import DashboardService
from app.services.performance_trends import PerformanceTrendService
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"data": {"items": [{"timestamp": datetime.now(timezone.utc).isoformat(), "power": 10.0}]}}


class _FakeAsyncClient:
    def __init__(self, recorder: list[dict[str, str]], *args, **kwargs) -> None:
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url: str, *, params=None, headers=None, **kwargs):
        self._recorder.append(headers or {})
        return _FakeResponse()

    async def post(self, url: str, *, json=None, headers=None, **kwargs):
        self._recorder.append(headers or {})
        return _FakeResponse()

    async def request(self, method: str, url: str, **kwargs):
        headers = kwargs.get("headers")
        self._recorder.append(headers or {})
        return _FakeResponse()


@pytest.mark.asyncio
async def test_performance_trends_fetch_includes_tenant_header(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        seen_headers: list[dict[str, str]] = []
        monkeypatch.setattr("app.services.performance_trends.settings.DATA_SERVICE_BASE_URL", "http://data-service:8081")
        monkeypatch.setattr("app.services.performance_trends.get_client", AsyncMock(return_value=_FakeAsyncClient(seen_headers)))

        service = PerformanceTrendService(session)
        await service._fetch_bucket_telemetry_mean(
            "DEVICE-1",
            "tenant-a",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
        )

    assert seen_headers[0]["X-Internal-Service"] == "device-service"
    assert seen_headers[0]["X-Tenant-Id"] == "tenant-a"


@pytest.mark.asyncio
async def test_performance_trends_health_sample_fetch_includes_tenant_header(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        seen_headers: list[dict[str, str]] = []
        monkeypatch.setattr("app.services.performance_trends.settings.DATA_SERVICE_BASE_URL", "http://data-service:8081")
        monkeypatch.setattr("app.services.performance_trends.get_client", AsyncMock(return_value=_FakeAsyncClient(seen_headers)))

        service = PerformanceTrendService(session)
        await service._fetch_bucket_health_sample(
            "DEVICE-1",
            "tenant-a",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
        )

    assert seen_headers[0]["X-Internal-Service"] == "device-service"
    assert seen_headers[0]["X-Tenant-Id"] == "tenant-a"


@pytest.mark.asyncio
async def test_dashboard_post_includes_ctx_tenant_header(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        seen_headers: list[dict[str, str]] = []
        monkeypatch.setattr("app.services.dashboard.settings.DATA_SERVICE_BASE_URL", "http://data-service:8081")
        monkeypatch.setattr("app.services.dashboard.get_client", AsyncMock(return_value=_FakeAsyncClient(seen_headers)))

        service = DashboardService(
            session,
            ctx=TenantContext(
                tenant_id="tenant-a",
                user_id="internal",
                role="internal_service",
                plant_ids=[],
                is_super_admin=False,
            ),
        )
        await service._http_post_json(
            service_key="data_service",
            url="http://data-service/api/v1/data/telemetry/latest-batch",
            body={"device_ids": ["DEVICE-1"]},
        )

    assert seen_headers[0]["X-Internal-Service"] == "device-service"
    assert seen_headers[0]["X-Tenant-Id"] == "tenant-a"
