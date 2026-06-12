from __future__ import annotations

import os
import sys

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, "/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/services/reporting-service")
sys.path.insert(1, "/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/services")

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from src.handlers.energy_reports import resolve_all_devices, validate_device_for_reporting
from src.config import settings
from src.services.device_scope import ReportingDeviceScopeService
from services.shared.tenant_context import TenantContext


def _ctx(role: str = "plant_manager", plant_ids: list[str] | None = None) -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
        user_id="user-1",
        role=role,
        plant_ids=["PLANT-1"] if plant_ids is None else plant_ids,
        is_super_admin=False,
    )


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://device-service")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse], calls: list[tuple[str, dict | None, dict | None]]):
        self._responses = list(responses)
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params=None, headers=None):
        self._calls.append((url, params, headers))
        if not self._responses:
            raise AssertionError("Unexpected extra HTTP call")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_reporting_device_scope_filters_all_devices_to_assigned_plants(monkeypatch):
    calls: list[tuple[str, dict | None, dict | None]] = []
    responses = [
        _FakeResponse(
            200,
            {
                "data": [
                    {"device_id": "D1", "plant_id": "PLANT-1"},
                    {"device_id": "D3", "plant_id": "PLANT-1"},
                ],
                "total_pages": 1,
            },
        ),
        _FakeResponse(
            200,
            {
                "data": [
                    {"device_id": "D2", "plant_id": "PLANT-2"},
                ],
                "total_pages": 1,
            },
        ),
        _FakeResponse(
            200,
            {
                "data": [
                    {"device_id": "D1", "plant_id": "PLANT-1"},
                    {"device_id": "D3", "plant_id": "PLANT-1"},
                ],
                "total_pages": 1,
            },
        ),
        _FakeResponse(
            200,
            {
                "data": [
                    {"device_id": "D2", "plant_id": "PLANT-2"},
                ],
                "total_pages": 1,
            },
        ),
    ]

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, calls),
    )
    monkeypatch.setattr(settings, "DEVICE_SERVICE_URL", "http://device-service:8001")

    ctx = _ctx(plant_ids=["PLANT-1", "PLANT-2"])
    device_ids = await ReportingDeviceScopeService(ctx).resolve_accessible_device_ids()
    helper_ids = await resolve_all_devices(ctx)

    assert device_ids == ["D1", "D3", "D2"]
    assert helper_ids == ["D1", "D3", "D2"]
    assert calls[0][1] == {"tenant_id": "TENANT-A", "page": 1, "page_size": 100, "plant_id": "PLANT-1"}
    assert calls[1][1] == {"tenant_id": "TENANT-A", "page": 1, "page_size": 100, "plant_id": "PLANT-2"}
    assert calls[0][2]["X-Tenant-Id"] == "TENANT-A"


@pytest.mark.asyncio
async def test_reporting_device_scope_allows_org_admin_all_devices(monkeypatch):
    calls: list[tuple[str, dict | None, dict | None]] = []
    responses = [
        _FakeResponse(
            200,
            {
                "data": [
                    {"device_id": "D1", "plant_id": "PLANT-1"},
                    {"device_id": "D2", "plant_id": "PLANT-2"},
                ],
                "total_pages": 1,
            },
        )
    ]

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, calls),
    )
    monkeypatch.setattr(settings, "DEVICE_SERVICE_URL", "http://device-service:8001")

    device_ids = await ReportingDeviceScopeService(_ctx(role="org_admin", plant_ids=[])).resolve_accessible_device_ids()

    assert device_ids == ["D1", "D2"]
    assert calls[0][1] == {"tenant_id": "TENANT-A", "page": 1, "page_size": 100}


@pytest.mark.asyncio
async def test_reporting_device_scope_returns_empty_when_plant_scoped_role_has_no_plants(monkeypatch):
    calls: list[tuple[str, dict | None, dict | None]] = []

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient([], calls),
    )
    monkeypatch.setattr(settings, "DEVICE_SERVICE_URL", "http://device-service:8001")

    device_ids = await ReportingDeviceScopeService(_ctx(plant_ids=[])).resolve_accessible_device_ids()

    assert device_ids == []
    assert calls == []


@pytest.mark.asyncio
async def test_validate_device_for_reporting_rejects_out_of_scope_device(monkeypatch):
    calls: list[tuple[str, dict | None, dict | None]] = []
    responses = [
        _FakeResponse(
            200,
            {
                "data": {
                    "device_id": "D2",
                    "plant_id": "PLANT-2",
                }
            },
        )
    ]

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, calls),
    )
    monkeypatch.setattr(settings, "DEVICE_SERVICE_URL", "http://device-service:8001")

    with pytest.raises(HTTPException) as exc_info:
        await validate_device_for_reporting("D2", _ctx())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "DEVICE_NOT_FOUND"
