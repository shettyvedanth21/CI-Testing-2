from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services import remote_clients


class _FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_tariff_cache_failure_does_not_crash_on_logger_kwargs(monkeypatch):
    async def _fake_fetch_tenant_tariff(*args, **kwargs):  # noqa: ANN001
        request = httpx.Request("GET", "http://reporting-service/api/reports/tariffs/internal/resolve")
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    monkeypatch.setattr(remote_clients.httpx, "AsyncClient", lambda *args, **kwargs: _FakeAsyncClient())
    monkeypatch.setattr(remote_clients, "fetch_tenant_tariff", _fake_fetch_tenant_tariff)

    cache = remote_clients.TariffCache()
    snapshot = await cache.get("tenant-123")

    assert snapshot.rate is None
    assert snapshot.currency == "INR"
    assert snapshot.configured is False
    assert snapshot.stale is True
