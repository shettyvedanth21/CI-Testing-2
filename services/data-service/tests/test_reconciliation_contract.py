from __future__ import annotations

import httpx
import pytest

from src.services.reconciliation import FLEET_SNAPSHOT_PAGE_SIZE, ReconciliationService


class _StubInfluxRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def get_latest_telemetry_batch(self, tenant_id: str, device_ids: list[str]):
        self.calls.append((tenant_id, list(device_ids)))
        return {}

    async def async_get_latest_telemetry_batch(self, tenant_id: str, device_ids: list[str]):
        return self.get_latest_telemetry_batch(tenant_id, device_ids)


class _StubOutboxRepository:
    async def ensure_schema(self) -> None:
        return None

    async def insert_reconciliation_log(self, **_kwargs) -> None:
        return None

    async def enqueue_telemetry(self, **_kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_fetch_mysql_state_uses_supported_fleet_snapshot_page_size() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/api/v1/devices/internal/active-tenant-ids"):
            return httpx.Response(status_code=200, json={"tenant_ids": ["ORG-1"]})
        payload = {
            "success": True,
            "page": 1,
            "page_size": FLEET_SNAPSHOT_PAGE_SIZE,
            "total_pages": 1,
            "devices": [
                {
                    "device_id": "AD00000001",
                    "last_seen_timestamp": "2026-04-07T07:00:00Z",
                }
            ],
        }
        return httpx.Response(status_code=200, json=payload)

    service = ReconciliationService(
        influx_repository=_StubInfluxRepository(),
        outbox_repository=_StubOutboxRepository(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0),
    )

    try:
        devices = await service._fetch_mysql_state()
    finally:
        await service.stop()

    assert len(requests) == 2
    active_tenants_request = requests[0]
    fleet_snapshot_request = requests[1]
    assert active_tenants_request.url.path.endswith("/api/v1/devices/internal/active-tenant-ids")
    assert active_tenants_request.headers["x-internal-service"] == "data-service"
    assert fleet_snapshot_request.url.path.endswith("/api/v1/devices/dashboard/fleet-snapshot")
    assert fleet_snapshot_request.url.params["page_size"] == str(FLEET_SNAPSHOT_PAGE_SIZE)
    assert fleet_snapshot_request.url.params["sort"] == "device_name"
    assert fleet_snapshot_request.headers["x-internal-service"] == "data-service"
    assert fleet_snapshot_request.headers["x-tenant-id"] == "ORG-1"
    assert devices == [{"device_id": "AD00000001", "last_seen_timestamp": "2026-04-07T07:00:00Z"}]


@pytest.mark.asyncio
async def test_run_once_batches_latest_telemetry_by_tenant() -> None:
    influx = _StubInfluxRepository()
    service = ReconciliationService(
        influx_repository=influx,
        outbox_repository=_StubOutboxRepository(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})), timeout=10.0),
    )

    async def fake_fetch_mysql_state():
        return [
            {"tenant_id": "ORG-1", "device_id": "AD00000001", "last_seen_timestamp": None},
            {"tenant_id": "ORG-1", "device_id": "TD00000001", "last_seen_timestamp": None},
            {"tenant_id": "ORG-2", "device_id": "VD00000001", "last_seen_timestamp": None},
        ]

    service._fetch_mysql_state = fake_fetch_mysql_state

    try:
        await service.run_once()
    finally:
        await service.stop()

    assert influx.calls == [
        ("ORG-1", ["AD00000001", "TD00000001"]),
        ("ORG-2", ["VD00000001"]),
    ]
