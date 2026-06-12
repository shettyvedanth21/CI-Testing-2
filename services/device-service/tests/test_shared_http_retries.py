from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import Request

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "test-internal-secret")

from app.services.shared_http import request_with_retries
from app.services.live_dashboard import LiveDashboardService
from app.api.v1 import devices as devices_api


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://testserver"),
                response=httpx.Response(self.status_code),
            )


class _FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_request_with_retries_retries_connect_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("app.services.shared_http.asyncio.sleep", _fake_sleep)

    client = _FakeClient(
        [
            httpx.ConnectError("temporary connect failure"),
            _Response(200, {"ok": True}),
        ]
    )

    response = await request_with_retries(
        client,
        "GET",
        "/resource",
        operation="test_connect_retry",
        retries=2,
        backoff_ms=25,
    )

    assert response.status_code == 200
    assert len(client.calls) == 2
    assert sleep_calls == [0.025]


@pytest.mark.asyncio
async def test_request_with_retries_retries_retryable_status_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("app.services.shared_http.asyncio.sleep", _fake_sleep)

    first = _Response(503, {"detail": "warming up"})
    second = _Response(200, {"ok": True})
    client = _FakeClient([first, second])

    response = await request_with_retries(
        client,
        "GET",
        "/resource",
        operation="test_status_retry",
        retries=2,
        backoff_ms=30,
    )

    assert response.status_code == 200
    assert first.closed is True
    assert len(client.calls) == 2
    assert sleep_calls == [0.03]


@pytest.mark.asyncio
async def test_request_with_retries_does_not_retry_non_retryable_status() -> None:
    client = _FakeClient([_Response(404, {"detail": "not found"})])

    response = await request_with_retries(
        client,
        "GET",
        "/resource",
        operation="test_non_retryable_status",
        retries=2,
        backoff_ms=30,
    )

    assert response.status_code == 404
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_list_tenant_plants_retries_transient_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        [
            httpx.ConnectError("temporary auth-service outage"),
            _Response(200, [{"id": "PLANT-1", "name": "Plant 1"}]),
        ]
    )
    monkeypatch.setattr(devices_api.settings, "AUTH_SERVICE_BASE_URL", "http://auth-service:8090")
    monkeypatch.setattr(devices_api, "get_client", AsyncMock(return_value=client))

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer token")],
        }
    )

    plants = await devices_api._list_tenant_plants(request, "tenant-a")

    assert plants == [{"id": "PLANT-1", "name": "Plant 1"}]
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_live_dashboard_energy_fetch_retries_retryable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        [
            _Response(503, {"success": False}),
            _Response(200, {"success": True, "data": {"ok": True}}),
        ]
    )
    monkeypatch.setattr("app.services.live_dashboard.get_client", AsyncMock(return_value=client))
    monkeypatch.setattr("app.services.live_dashboard.settings.ENERGY_SERVICE_BASE_URL", "http://energy-service:8010")

    service = LiveDashboardService(session=None)  # type: ignore[arg-type]
    payload = await service._fetch_energy_json("/api/v1/energy/summary", params={"tenant_id": "tenant-a"})

    assert payload == {"success": True, "data": {"ok": True}}
    assert len(client.calls) == 2
