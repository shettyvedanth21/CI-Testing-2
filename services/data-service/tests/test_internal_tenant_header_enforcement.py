from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = PROJECT_ROOT / "services"
for path in (PROJECT_ROOT, SERVICES_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault(
    "INTERNAL_SERVICE_SHARED_SECRET",
    "test-internal-service-secret-at-least-32-chars",
)

from shared.auth_middleware import AuthMiddleware
from services.shared.tenant_context import build_internal_headers, require_tenant
from src.services.enrichment_service import EnrichmentService


def _build_scoped_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/scoped")
    async def scoped(request: Request) -> dict[str, str]:
        return {"tenant_id": require_tenant(request)}

    return app


@pytest.mark.asyncio
async def test_internal_scoped_route_rejects_query_only_tenant_scope() -> None:
    app = _build_scoped_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/scoped",
            params={"tenant_id": "SH00000001"},
            headers=build_internal_headers("data-service"),
        )

    assert response.status_code == 403
    body = response.json()
    detail = body.get("detail", body)
    assert detail["code"] == "TENANT_SCOPE_REQUIRED"


@pytest.mark.asyncio
async def test_internal_scoped_route_rejects_spoofed_service_header_without_proof() -> None:
    app = _build_scoped_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/scoped",
            headers={
                "X-Internal-Service": "data-service",
                "X-Tenant-Id": "SH00000001",
            },
        )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_INTERNAL_SERVICE_AUTH"


@pytest.mark.asyncio
async def test_internal_scoped_route_accepts_valid_tenant_header() -> None:
    app = _build_scoped_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/scoped",
            headers=build_internal_headers("data-service", "SH00000001"),
        )

    assert response.status_code == 200
    assert response.json() == {"tenant_id": "SH00000001"}


@pytest.mark.asyncio
async def test_internal_scoped_route_rejects_conflicting_tenant_headers() -> None:
    app = _build_scoped_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/scoped",
            headers={
                **build_internal_headers("data-service", "SH00000001"),
                "X-Target-Tenant-Id": "SH00000002",
            },
        )

    assert response.status_code == 403
    assert response.json()["code"] == "TENANT_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_enrichment_service_sends_explicit_tenant_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_headers: dict[str, str] = {}

    class _Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "data": {
                    "device_id": "DEVICE-1",
                    "tenant_id": "SH00000001",
                    "device_name": "Device 1",
                    "device_type": "compressor",
                    "legacy_status": "active",
                }
            }

        def raise_for_status(self) -> None:
            return None

    async def _fake_get(url: str, *, params=None, headers=None):
        assert params == {"tenant_id": "SH00000001"}
        captured_headers.update(headers or {})
        return _Response()

    async def _fake_breaker_call(fn):
        return True, await fn()

    service = EnrichmentService(base_url="http://device-service")
    monkeypatch.setattr(service.client, "get", _fake_get)
    monkeypatch.setattr(service.circuit_breaker, "call", _fake_breaker_call)

    metadata = await service._fetch_device_metadata("DEVICE-1", "SH00000001")
    await service.close()

    assert metadata.tenant_id == "SH00000001"
    assert captured_headers["X-Internal-Service"] == "data-service"
    assert captured_headers["X-Tenant-Id"] == "SH00000001"
    assert "X-Internal-Service-Signature" in captured_headers
    assert "X-Internal-Service-Timestamp" in captured_headers
