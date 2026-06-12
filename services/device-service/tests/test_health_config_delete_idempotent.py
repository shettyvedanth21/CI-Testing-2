from pathlib import Path
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.api.v1 import devices as devices_api
from services.shared.tenant_context import TenantContext


class _HealthServiceDeleted:
    def __init__(self, session):
        self.session = session

    async def delete_health_config(self, config_id, device_id, tenant_id):
        return True


class _HealthServiceMissing:
    def __init__(self, session):
        self.session = session

    async def delete_health_config(self, config_id, device_id, tenant_id):
        return False


class _ProjectionService:
    def __init__(self, session):
        self.session = session

    async def recompute_after_configuration_change(self, device_id, tenant_id):
        return {"device_id": device_id}


class _DashboardService:
    def __init__(self, session):
        self.session = session

    async def publish_device_update(self, device_id, tenant_id, partial=True):
        return {
            "generated_at": "2026-03-19T00:00:00+00:00",
            "stale": False,
            "warnings": [],
            "devices": [{"device_id": device_id, "version": 1}],
            "partial": partial,
            "version": 1,
        }


class _TrendService:
    def __init__(self, session):
        self.session = session

    async def repair_recent_health_window(self, *args, **kwargs):
        return None


def _apply_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": "user-1",
            "tenant_id": "ORG-1",
            "role": "org_admin",
            "plant_ids": [],
            "is_authenticated": True,
        },
    )
    monkeypatch.setattr(
        devices_api.TenantContext,
        "from_request",
        classmethod(
            lambda cls, request: TenantContext(
                tenant_id="ORG-1",
                user_id="user-1",
                role="org_admin",
                plant_ids=[],
                is_super_admin=False,
            )
        ),
    )


def _apply_device_lookup(monkeypatch) -> None:
    monkeypatch.setattr(
        devices_api,
        "_resolve_scoped_device",
        AsyncMock(return_value=SimpleNamespace(plant_id="PLANT-1")),
    )


def _apply_post_delete_services(monkeypatch) -> None:
    monkeypatch.setattr("app.services.live_projection.LiveProjectionService", _ProjectionService)
    monkeypatch.setattr("app.services.live_dashboard.LiveDashboardService", _DashboardService)
    monkeypatch.setattr("app.services.performance_trends.PerformanceTrendService", _TrendService)


@pytest.mark.asyncio
async def test_delete_health_config_returns_success_when_deleted(monkeypatch):
    _apply_auth(monkeypatch)
    _apply_device_lookup(monkeypatch)
    publish_mock = AsyncMock()
    monkeypatch.setattr("app.services.health_config.HealthConfigService", _HealthServiceDeleted)
    _apply_post_delete_services(monkeypatch)
    monkeypatch.setattr(devices_api.fleet_stream_broadcaster, "publish", publish_mock)

    result = await devices_api.delete_health_config(
        device_id="MACHINE-001",
        config_id=10,
        tenant_id=None,
        request=SimpleNamespace(
            headers={"X-Tenant-Id": "ORG-1"},
            state=SimpleNamespace(role="org_admin", tenant_id="ORG-1"),
            query_params={},
        ),
        db=AsyncMock(),
    )

    assert result["success"] is True
    assert result["deleted"] is True
    publish_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_health_config_is_idempotent_when_missing(monkeypatch):
    _apply_auth(monkeypatch)
    _apply_device_lookup(monkeypatch)
    publish_mock = AsyncMock()
    monkeypatch.setattr("app.services.health_config.HealthConfigService", _HealthServiceMissing)
    _apply_post_delete_services(monkeypatch)
    monkeypatch.setattr(devices_api.fleet_stream_broadcaster, "publish", publish_mock)

    result = await devices_api.delete_health_config(
        device_id="MACHINE-001",
        config_id=10,
        tenant_id=None,
        request=SimpleNamespace(
            headers={"X-Tenant-Id": "ORG-1"},
            state=SimpleNamespace(role="org_admin", tenant_id="ORG-1"),
            query_params={},
        ),
        db=AsyncMock(),
    )

    assert result["success"] is True
    assert result["deleted"] is False
    assert "already deleted" in result["message"]
    publish_mock.assert_awaited_once()
