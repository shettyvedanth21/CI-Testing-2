from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = PROJECT_ROOT / "services"
for path in (PROJECT_ROOT, SERVICES_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-at-least-32-chars")

from src.api.routes import create_router, get_telemetry_service
from src.models import EnrichmentStatus, TelemetryPoint
from src.repositories.influxdb_repository import InfluxDBRepository
from src.services.telemetry_service import TelemetryService
from services.shared.tenant_context import TenantContext


class _RouteTelemetryServiceStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def get_latest(
        self,
        tenant_id: str,
        device_id: str,
        accessible_plant_ids: list[str] | None = None,
    ):
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "device_id": device_id,
                "accessible_plant_ids": accessible_plant_ids,
            }
        )
        return None

    async def get_earliest(
        self,
        tenant_id: str,
        device_id: str,
        start_time: datetime | None = None,
        accessible_plant_ids: list[str] | None = None,
    ):
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "device_id": device_id,
                "accessible_plant_ids": accessible_plant_ids,
                "start_time": start_time,
            }
        )
        return None

    async def assert_device_access(
        self,
        *,
        tenant_id: str,
        device_id: str,
        accessible_plant_ids: list[str] | None = None,
    ) -> None:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "device_id": device_id,
                "accessible_plant_ids": accessible_plant_ids,
                "authorize_only": True,
            }
        )


class _NoopInfluxRepository:
    def close(self) -> None:
        return None


class _BatchProbeInfluxRepository(_NoopInfluxRepository):
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def get_latest_telemetry_batch(
        self,
        tenant_id: str,
        device_ids: list[str],
    ) -> dict[str, TelemetryPoint | None]:
        self.calls.append((tenant_id, list(device_ids)))
        return {device_id: None for device_id in device_ids}

    async def async_get_latest_telemetry_batch(
        self,
        tenant_id: str,
        device_ids: list[str],
    ) -> dict[str, TelemetryPoint | None]:
        return self.get_latest_telemetry_batch(tenant_id, device_ids)


class _TenantAwareInfluxRepository(_NoopInfluxRepository):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def query_telemetry(self, tenant_id: str, device_id: str, **_: object) -> list[TelemetryPoint]:
        self.calls.append((tenant_id, device_id))
        power = 11.0 if tenant_id == "tenant-a" else 22.0
        timestamp = (
            datetime(2026, 1, 1, tzinfo=timezone.utc)
            if tenant_id == "tenant-a"
            else datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
        return [
            TelemetryPoint(
                timestamp=timestamp,
                device_id=device_id,
                schema_version="v1",
                enrichment_status=EnrichmentStatus.SUCCESS,
                power=power,
            )
        ]

    async def async_query_telemetry(self, tenant_id: str, device_id: str, **kwargs: object) -> list[TelemetryPoint]:
        return self.query_telemetry(tenant_id, device_id, **kwargs)


class _NoopDLQRepository:
    def close(self) -> None:
        return None

    def get_operational_stats(self) -> dict[str, object]:
        return {}


class _NoopOutboxRepository:
    async def ensure_schema(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _NoopEnrichmentService:
    async def close(self) -> None:
        return None


class _NoopRuleEngineClient:
    async def close(self) -> None:
        return None


class _NoopDeviceProjectionClient:
    async def close(self) -> None:
        return None


class _NoopStageQueue:
    async def ensure_groups(self) -> None:
        return None

    async def publish(self, *args, **kwargs) -> None:
        return None

    async def incr_counter(self, *args, **kwargs) -> None:
        return None

    async def metrics(self) -> dict[str, object]:
        return {}

    async def close(self) -> None:
        return None


def _build_test_app(service: object) -> FastAPI:
    app = FastAPI()
    app.include_router(create_router())
    app.dependency_overrides[get_telemetry_service] = lambda: service
    return app


def _build_scoped_test_app(
    service: object,
    *,
    tenant_id: str,
    role: str,
    plant_ids: list[str],
) -> FastAPI:
    app = _build_test_app(service)

    class _InjectContextMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.tenant_context = TenantContext(
                tenant_id=tenant_id,
                user_id="test-user",
                role=role,
                plant_ids=plant_ids,
                is_super_admin=False,
            )
            request.state.tenant_id = tenant_id
            request.state.tenant_id = tenant_id
            request.state.role = role
            request.state.plant_ids = plant_ids
            return await call_next(request)

    app.add_middleware(_InjectContextMiddleware)
    return app


def _build_telemetry_service(influx_repository: object) -> TelemetryService:
    return TelemetryService(
        influx_repository=influx_repository,
        dlq_repository=_NoopDLQRepository(),
        outbox_repository=_NoopOutboxRepository(),
        enrichment_service=_NoopEnrichmentService(),
        rule_engine_client=_NoopRuleEngineClient(),
        device_projection_client=_NoopDeviceProjectionClient(),
        stage_queue=_NoopStageQueue(),
    )


@pytest.mark.asyncio
async def test_get_latest_rejects_missing_tenant_scope() -> None:
    service = _RouteTelemetryServiceStub()
    app = _build_test_app(service)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/v1/data/telemetry/DEVICE-1/latest")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TENANT_SCOPE_REQUIRED"
    assert service.calls == []


@pytest.mark.asyncio
async def test_get_latest_route_passes_request_plant_scope_for_viewer() -> None:
    service = _RouteTelemetryServiceStub()
    app = _build_scoped_test_app(
        service,
        tenant_id="tenant-a",
        role="viewer",
        plant_ids=["PLANT-A"],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/v1/data/telemetry/DEVICE-1/latest")

    assert response.status_code == 200
    assert service.calls == [
        {
            "tenant_id": "tenant-a",
            "device_id": "DEVICE-1",
            "accessible_plant_ids": ["PLANT-A"],
        }
    ]


@pytest.mark.asyncio
async def test_get_earliest_route_passes_request_plant_scope_for_viewer() -> None:
    service = _RouteTelemetryServiceStub()
    app = _build_scoped_test_app(
        service,
        tenant_id="tenant-a",
        role="viewer",
        plant_ids=["PLANT-A"],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/v1/data/telemetry/DEVICE-1/earliest")

    assert response.status_code == 200
    assert service.calls == [
        {
            "tenant_id": "tenant-a",
            "device_id": "DEVICE-1",
            "accessible_plant_ids": ["PLANT-A"],
            "start_time": None,
        }
    ]


@pytest.mark.asyncio
async def test_issue_ws_ticket_passes_request_plant_scope_for_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RouteTelemetryServiceStub()
    app = _build_scoped_test_app(
        service,
        tenant_id="tenant-a",
        role="viewer",
        plant_ids=["PLANT-A"],
    )

    class _FakeTicketService:
        async def issue_ticket(self, *, user_id: str, role: str, tenant_id: str, device_id: str) -> dict[str, object]:
            assert user_id == "test-user"
            assert role == "viewer"
            assert tenant_id == "tenant-a"
            assert device_id == "DEVICE-1"
            return {"ticket": "ticket-123", "expires_in_seconds": 30}

    monkeypatch.setattr("src.api.routes.get_websocket_ticket_service", lambda: _FakeTicketService())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/api/v1/data/telemetry/DEVICE-1/ws-ticket")

    assert response.status_code == 200
    assert response.json()["data"] == {"ticket": "ticket-123", "expires_in_seconds": 30}
    assert service.calls == [
        {
            "tenant_id": "tenant-a",
            "device_id": "DEVICE-1",
            "accessible_plant_ids": ["PLANT-A"],
            "authorize_only": True,
        }
    ]


@pytest.mark.asyncio
async def test_get_latest_batch_enforces_tenant_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    influx_repository = _BatchProbeInfluxRepository()
    service = _build_telemetry_service(influx_repository)

    async def _owned_devices(*, tenant_id: str, device_ids: list[str]) -> set[str]:
        assert tenant_id == "tenant-a"
        assert device_ids == ["DEVICE-1", "DEVICE-2"]
        return {"DEVICE-1"}

    async def _owned_devices_with_scope(
        *,
        tenant_id: str,
        device_ids: list[str],
        accessible_plant_ids: list[str] | None = None,
    ) -> set[str]:
        assert accessible_plant_ids is None
        return await _owned_devices(tenant_id=tenant_id, device_ids=device_ids)

    monkeypatch.setattr(service, "_fetch_tenant_owned_device_ids", _owned_devices_with_scope)

    with pytest.raises(HTTPException) as exc_info:
        await service.get_latest_batch(
            tenant_id="tenant-a",
            device_ids=["DEVICE-1", "DEVICE-2"],
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "DEVICE_NOT_FOUND"
    assert exc_info.value.detail["device_ids"] == ["DEVICE-2"]
    assert influx_repository.calls == []


@pytest.mark.asyncio
async def test_get_latest_rejects_out_of_scope_device_for_plant_scoped_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    influx_repository = _BatchProbeInfluxRepository()
    service = _build_telemetry_service(influx_repository)

    async def _owned_devices(
        *,
        tenant_id: str,
        device_ids: list[str],
        accessible_plant_ids: list[str] | None = None,
    ) -> set[str]:
        assert tenant_id == "tenant-a"
        assert device_ids == ["DEVICE-2"]
        assert accessible_plant_ids == ["PLANT-A"]
        return set()

    monkeypatch.setattr(service, "_fetch_tenant_owned_device_ids", _owned_devices)

    with pytest.raises(HTTPException) as exc_info:
        await service.get_latest(
            tenant_id="tenant-a",
            device_id="DEVICE-2",
            accessible_plant_ids=["PLANT-A"],
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "DEVICE_NOT_FOUND"
    assert influx_repository.calls == []


def test_repository_queries_apply_tenant_filter() -> None:
    query_api = MagicMock()
    query_api.query.return_value = []
    mock_client = MagicMock()
    mock_client.query_api.return_value = query_api
    mock_client.write_api.return_value = MagicMock()

    with patch("src.repositories.influxdb_repository.DLQRepository", return_value=MagicMock()):
        repository = InfluxDBRepository(client=mock_client)

    repository.query_telemetry(
        tenant_id="tenant-a",
        device_id="DEVICE-1",
        limit=1,
    )
    single_query = query_api.query.call_args_list[0].args[0]
    assert 'r.tenant_id == "tenant-a"' in single_query
    assert 'r.device_id == "DEVICE-1"' in single_query

    query_api.query.reset_mock()
    repository.get_earliest_telemetry(
        tenant_id="tenant-a",
        device_id="DEVICE-1",
    )
    earliest_query = query_api.query.call_args.args[0]
    assert '|> sort(columns: ["_time"], desc: false)' in earliest_query
    assert '|> limit(n: 1)' in earliest_query

    query_api.query.reset_mock()
    repository.get_latest_telemetry_batch(
        tenant_id="tenant-a",
        device_ids=["DEVICE-1", "DEVICE-2"],
    )
    batch_query = query_api.query.call_args.args[0]
    assert 'r.tenant_id == "tenant-a"' in batch_query
    assert 'r.device_id == "DEVICE-1"' in batch_query
    assert 'r.device_id == "DEVICE-2"' in batch_query


def test_latest_batch_query_pivots_raw_points_before_limiting() -> None:
    query_api = MagicMock()
    query_api.query.return_value = []
    mock_client = MagicMock()
    mock_client.query_api.return_value = query_api
    mock_client.write_api.return_value = MagicMock()

    with patch("src.repositories.influxdb_repository.DLQRepository", return_value=MagicMock()):
        repository = InfluxDBRepository(client=mock_client)

    repository.get_latest_telemetry_batch(
        tenant_id="tenant-a",
        device_ids=["DEVICE-1", "DEVICE-2"],
    )

    batch_query = query_api.query.call_args.args[0]
    assert "join(tables:" not in batch_query
    assert '|> pivot(' in batch_query
    assert 'rowKey: ["_time", "device_id"]' in batch_query
    assert '|> group(columns: ["device_id"])' in batch_query
    assert '|> sort(columns: ["_time"], desc: true)' in batch_query
    assert '|> limit(n: 1)' in batch_query


def test_query_telemetry_sorts_across_chunked_tables_before_limiting() -> None:
    class _Record:
        def __init__(self, at: datetime, **values: object) -> None:
            self._time = at
            self.values = values

        def get_time(self) -> datetime:
            return self._time

    class _Table:
        def __init__(self, *records: _Record) -> None:
            self.records = list(records)

    query_api = MagicMock()
    query_api.query.return_value = [
        _Table(
            _Record(
                datetime(2026, 4, 9, 14, 34, 4, tzinfo=timezone.utc),
                device_id="DEVICE-1",
                power=1000.0,
            )
        ),
        _Table(
            _Record(
                datetime(2026, 4, 9, 14, 36, 4, tzinfo=timezone.utc),
                device_id="DEVICE-1",
                power=3000.0,
            )
        ),
    ]
    mock_client = MagicMock()
    mock_client.query_api.return_value = query_api
    mock_client.write_api.return_value = MagicMock()

    with patch("src.repositories.influxdb_repository.DLQRepository", return_value=MagicMock()):
        repository = InfluxDBRepository(client=mock_client)

    points = repository.query_telemetry(
        tenant_id="tenant-a",
        device_id="DEVICE-1",
        limit=1,
    )

    assert len(points) == 1
    assert points[0].timestamp == datetime(2026, 4, 9, 14, 36, 4, tzinfo=timezone.utc)
    assert points[0].power == 3000.0


@pytest.mark.asyncio
async def test_duplicate_device_ids_across_tenants_return_only_requested_tenant_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    influx_repository = _TenantAwareInfluxRepository()
    service = _build_telemetry_service(influx_repository)

    async def _owned_devices(
        *,
        tenant_id: str,
        device_ids: list[str],
        accessible_plant_ids: list[str] | None = None,
    ) -> set[str]:
        assert tenant_id in {"tenant-a", "tenant-b"}
        assert device_ids == ["SHARED-DEVICE"]
        assert accessible_plant_ids is None
        return {"SHARED-DEVICE"}

    monkeypatch.setattr(service, "_fetch_tenant_owned_device_ids", _owned_devices)

    tenant_a_points = await service.get_telemetry(
        tenant_id="tenant-a",
        device_id="SHARED-DEVICE",
        limit=1,
    )
    tenant_b_points = await service.get_telemetry(
        tenant_id="tenant-b",
        device_id="SHARED-DEVICE",
        limit=1,
    )

    assert tenant_a_points[0].power == 11.0
    assert tenant_b_points[0].power == 22.0
    assert influx_repository.calls == [
        ("tenant-a", "SHARED-DEVICE"),
        ("tenant-b", "SHARED-DEVICE"),
    ]
