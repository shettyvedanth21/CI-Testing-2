from __future__ import annotations

import sys
from pathlib import Path

import pytest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.shared.tenant_context import build_internal_headers
from src.tasks import waste_task


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse], calls: list[dict]):
        self._responses = list(responses)
        self._calls = calls

    async def get(self, url: str, *, params=None, headers=None):
        self._calls.append({
            "url": url,
            "params": params,
            "headers": headers,
        })
        if not self._responses:
            raise AssertionError("No fake response configured")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_reporting_reference_uses_tenant_scoped_internal_headers(monkeypatch):
    calls: list[dict] = []
    responses = [
        _FakeResponse(
            200,
            {
                "reports": [
                    {
                        "report_id": "report-1",
                        "status": "completed",
                    }
                ]
            },
        ),
        _FakeResponse(
            200,
            {
                "start_date": "2026-04-08",
                "end_date": "2026-04-08",
                "device_scope": "AD00000001",
                "summary": {"total_kwh": 12.5},
            },
        ),
    ]

    monkeypatch.setattr(
        waste_task,
        "get_reporting_http_client",
        lambda: _FakeAsyncClient(responses, calls),
    )

    total_kwh = await waste_task._find_reporting_reference_kwh(
        "selected",
        ["AD00000001"],
        start_date=__import__("datetime").date(2026, 4, 8),
        end_date=__import__("datetime").date(2026, 4, 8),
        tenant_id="tenant-123",
    )

    assert total_kwh == 12.5
    assert len(calls) == 2
    for call in calls:
        expected = build_internal_headers("waste-analysis-service", "tenant-123")
        assert call["headers"]["X-Internal-Service"] == expected["X-Internal-Service"]
        assert call["headers"]["X-Tenant-Id"] == expected["X-Tenant-Id"]
        assert "X-Internal-Service-Signature" in call["headers"]
        assert "X-Internal-Service-Timestamp" in call["headers"]
