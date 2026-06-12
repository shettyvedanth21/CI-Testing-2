from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from src.integrations.data_service_client import DataServiceClient


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"data": {"items": []}}


class _FakeAsyncClient:
    def __init__(self, *, calls: list[dict[str, object]], timeout: float):
        self._calls = calls
        self._timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, *, params=None, headers=None):
        self._calls.append(
            {
                "timeout": self._timeout,
                "url": url,
                "params": params,
                "headers": headers,
            }
        )
        return _FakeResponse()


def test_data_service_client_signs_and_scopes_internal_headers(monkeypatch) -> None:
    monkeypatch.setenv("DATA_SERVICE_URL", "http://data-service")
    calls: list[dict[str, object]] = []

    def _fake_headers(service_name: str, tenant_id: str | None = None) -> dict[str, str]:
        return {
            "X-Internal-Service": service_name,
            "X-Tenant-Id": tenant_id or "",
            "X-Internal-Service-Signature": "signed",
            "X-Internal-Service-Timestamp": "123",
        }

    monkeypatch.setattr("src.integrations.data_service_client.build_internal_headers", _fake_headers)
    monkeypatch.setattr(
        "src.integrations.data_service_client.httpx.AsyncClient",
        lambda timeout=20.0: _FakeAsyncClient(calls=calls, timeout=timeout),
    )

    client = DataServiceClient()
    payload = asyncio.run(
        client.fetch_telemetry(
            device_id="DEVICE-1",
            start=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end=datetime(2026, 4, 2, tzinfo=timezone.utc),
            tenant_id="tenant-a",
            fields=["power"],
            limit=25,
        )
    )

    assert payload == {"data": {"items": []}}
    assert len(calls) == 1
    assert calls[0]["headers"] == {
        "X-Internal-Service": "copilot-service",
        "X-Tenant-Id": "tenant-a",
        "X-Internal-Service-Signature": "signed",
        "X-Internal-Service-Timestamp": "123",
    }
