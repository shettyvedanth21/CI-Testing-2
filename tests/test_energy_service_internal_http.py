from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest


ENERGY_SERVICES_DIR = Path(__file__).resolve().parents[1] / "services" / "energy-service" / "app" / "services"


def load_energy_service_module(module_name: str, filename: str):
    if str(ENERGY_SERVICES_DIR) not in sys.path:
        sys.path.insert(0, str(ENERGY_SERVICES_DIR))
    spec = importlib.util.spec_from_file_location(module_name, ENERGY_SERVICES_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_internal_get_attaches_service_and_tenant_headers():
    internal_http = load_energy_service_module("energy_internal_http_test", "internal_http.py")
    captured = {}

    async def _run():
        async def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["query"] = dict(request.url.params)
            return httpx.Response(200, json={"success": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://example.test") as client:
            response = await internal_http.internal_get(
                client,
                "http://example.test/api/v1/devices",
                service_name="energy-service",
                tenant_id="SH00000001",
                params={"tenant_id": "SH00000001"},
            )
        return response

    response = asyncio.run(_run())

    assert response.status_code == 200
    assert captured["headers"]["x-internal-service"] == "energy-service"
    assert captured["headers"]["x-tenant-id"] == "SH00000001"
    assert captured["query"]["tenant_id"] == "SH00000001"
