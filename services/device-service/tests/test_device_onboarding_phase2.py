from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MQTT_BROKER_HOST", "mqtt.test.local")
os.environ.setdefault("MQTT_BROKER_PORT", "1883")

import app as device_app_module
from app.api.v1 import devices as devices_api
from app.api.v1.router import api_router
from app.database import Base, get_db
from app.models.device import Device, DeviceIdSequence, DeviceMQTTCredential
from app.schemas.device import DeviceCreate
from app.services.device_errors import DeviceAlreadyExistsError, DeviceIdAllocationError
from app.services.device_onboarding import DeviceOnboardingService
from services.shared.tenant_context import TenantContext


async def _seed_sequences(session_factory) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                DeviceIdSequence(prefix="AD", next_value=1),
                DeviceIdSequence(prefix="TD", next_value=1),
                DeviceIdSequence(prefix="VD", next_value=1),
            ]
        )
        await session.commit()


def _apply_auth(monkeypatch, *, role: str = "org_admin", tenant_id: str = "ORG-1", plant_ids: list[str] | None = None) -> None:
    effective_plant_ids = plant_ids if plant_ids is not None else ["PLANT-1"]
    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": "user-1",
            "tenant_id": tenant_id,
            "role": role,
            "plant_ids": effective_plant_ids,
            "is_authenticated": True,
        },
    )
    monkeypatch.setattr(
        devices_api.TenantContext,
        "from_request",
        classmethod(
            lambda cls, request: TenantContext(
                tenant_id=tenant_id,
                user_id="user-1",
                role=role,
                plant_ids=effective_plant_ids,
                is_super_admin=role == "super_admin",
            )
        ),
    )


@pytest_asyncio.fixture
async def device_app():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_sequences(session_factory)

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.add_exception_handler(
        device_app_module.RequestValidationError,
        device_app_module.validation_exception_handler,
    )
    app.add_exception_handler(
        device_app_module.HTTPException,
        device_app_module.http_exception_handler,
    )
    app.add_exception_handler(
        Exception,
        device_app_module.unhandled_exception_handler,
    )

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    try:
        yield app, session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_onboard_double_submit_returns_single_device_and_single_credential(device_app, monkeypatch):
    app, session_factory = device_app
    _apply_auth(monkeypatch)

    async def _allow_plant_access(*args, **kwargs):
        return None

    monkeypatch.setattr(devices_api, "_validate_org_plant_access", _allow_plant_access)
    payload = {
        "device_id": "PHASE2-ONBOARD-1",
        "device_name": "Phase 2 Device",
        "device_type": "compressor",
        "device_id_class": "active",
        "phase_type": "single",
        "data_source_type": "metered",
        "plant_id": "PLANT-1",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json=payload,
        )
        second = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json=payload,
        )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DEVICE_ALREADY_EXISTS"

    async with session_factory() as session:
        device_count = await session.scalar(
            select(func.count()).select_from(Device).where(Device.device_id == "PHASE2-ONBOARD-1")
        )
        credential_count = await session.scalar(
            select(func.count()).select_from(DeviceMQTTCredential).where(DeviceMQTTCredential.device_id == "PHASE2-ONBOARD-1")
        )

    assert device_count == 1
    assert credential_count == 1


@pytest.mark.asyncio
async def test_onboard_rolls_back_device_when_credential_provisioning_fails(device_app):
    _app, session_factory = device_app

    async with session_factory() as session:
        service = DeviceOnboardingService(
            session,
            TenantContext(
                tenant_id="ORG-1",
                user_id="svc:test",
                role="org_admin",
                plant_ids=["PLANT-1"],
                is_super_admin=False,
            ),
        )
        service._mqtt_service.register_credential = AsyncMock(side_effect=RuntimeError("credential bootstrap failed"))

        with pytest.raises(RuntimeError, match="credential bootstrap failed"):
            await service.onboard_device(
                DeviceCreate(
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    device_id="PHASE2-ROLLBACK-1",
                    device_name="Rollback Device",
                    device_type="compressor",
                    device_id_class="active",
                    data_source_type="metered",
                    phase_type="single",
                )
            )

    async with session_factory() as session:
        device = await session.get(Device, {"device_id": "PHASE2-ROLLBACK-1", "tenant_id": "ORG-1"})
        credential = (
            await session.execute(
                select(DeviceMQTTCredential).where(DeviceMQTTCredential.device_id == "PHASE2-ROLLBACK-1")
            )
        ).scalar_one_or_none()

    assert device is None
    assert credential is None


@pytest.mark.asyncio
async def test_concurrent_onboard_requests_with_same_device_id_leave_no_orphans(tmp_path):
    database_path = tmp_path / "phase2_onboarding.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_sequences(session_factory)

    async def _onboard(worker_index: int):
        async with session_factory() as session:
            service = DeviceOnboardingService(
                session,
                TenantContext(
                    tenant_id="ORG-1",
                    user_id=f"worker-{worker_index}",
                    role="org_admin",
                    plant_ids=["PLANT-1"],
                    is_super_admin=False,
                ),
            )
            return await service.onboard_device(
                DeviceCreate(
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    device_id="PHASE2-CONCURRENT-1",
                    device_name=f"Concurrent Device {worker_index}",
                    device_type="compressor",
                    device_id_class="active",
                    data_source_type="metered",
                    phase_type="single",
                )
            )

    results = await asyncio.gather(_onboard(1), _onboard(2), return_exceptions=True)
    success_count = sum(1 for item in results if not isinstance(item, Exception))
    errors = [item for item in results if isinstance(item, Exception)]

    assert success_count == 1
    assert len(errors) == 1
    assert isinstance(errors[0], DeviceAlreadyExistsError)

    async with session_factory() as session:
        device_count = await session.scalar(
            select(func.count()).select_from(Device).where(Device.device_id == "PHASE2-CONCURRENT-1")
        )
        credential_count = await session.scalar(
            select(func.count()).select_from(DeviceMQTTCredential).where(DeviceMQTTCredential.device_id == "PHASE2-CONCURRENT-1")
        )

    assert device_count == 1
    assert credential_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_viewer_cannot_onboard_device(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch, role="viewer")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_id": "PHASE2-VIEWER-BLOCKED",
                "device_name": "Blocked Device",
                "device_type": "compressor",
                "device_id_class": "active",
                "phase_type": "single",
                "data_source_type": "metered",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["plant_manager", "operator"])
async def test_plant_scoped_roles_cannot_onboard_foreign_plant(device_app, monkeypatch, role):
    app, _session_factory = device_app
    _apply_auth(monkeypatch, role=role, plant_ids=["PLANT-1"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_id": f"PHASE2-{role.upper()}-FOREIGN",
                "device_name": "Foreign Plant Device",
                "device_type": "compressor",
                "device_id_class": "active",
                "phase_type": "single",
                "data_source_type": "metered",
                "plant_id": "PLANT-9",
            },
        )

    assert response.status_code == 403
    assert response.json()["code"] == "PLANT_ACCESS_DENIED"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["plant_manager", "operator"])
async def test_plant_scoped_roles_can_onboard_assigned_plant(device_app, monkeypatch, role):
    app, session_factory = device_app
    _apply_auth(monkeypatch, role=role, plant_ids=["PLANT-1"])

    async def _allow_plant_access(*args, **kwargs):
        return None

    monkeypatch.setattr(devices_api, "_validate_org_plant_access", _allow_plant_access)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_id": f"PHASE2-{role.upper()}-ALLOWED",
                "device_name": "Assigned Plant Device",
                "device_type": "compressor",
                "device_id_class": "active",
                "phase_type": "single",
                "data_source_type": "metered",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["device"]["tenant_id"] == "ORG-1"
    assert body["device"]["plant_id"] == "PLANT-1"

    async with session_factory() as session:
        device = await session.get(Device, { "device_id": f"PHASE2-{role.upper()}-ALLOWED", "tenant_id": "ORG-1" })

    assert device is not None


@pytest.mark.asyncio
async def test_onboard_rejects_missing_plant_id(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_name": "Missing Plant Device",
                "device_type": "compressor",
                "device_id_class": "active",
                "phase_type": "single",
                "data_source_type": "metered",
                "plant_id": "   ",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_onboard_maps_true_conflict_to_409(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async def _allow_plant_access(*args, **kwargs):
        return None

    async def _raise_conflict(self, _device_data):
        raise DeviceAlreadyExistsError("Device with ID 'ONBOARD-CONFLICT' already exists")

    monkeypatch.setattr(devices_api, "_validate_org_plant_access", _allow_plant_access)
    monkeypatch.setattr("app.api.v1.devices.DeviceOnboardingService.onboard_device", _raise_conflict)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_name": "Conflict Device",
                "device_type": "compressor",
                "device_id_class": "active",
                "phase_type": "single",
                "data_source_type": "metered",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["error"]["code"] == "DEVICE_ALREADY_EXISTS"
    assert payload["error"]["message"] == "Device with ID 'ONBOARD-CONFLICT' already exists"


@pytest.mark.asyncio
async def test_onboard_returns_503_when_device_id_allocation_fails(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async def _allow_plant_access(*args, **kwargs):
        return None

    async def _raise_allocation(self, _device_data):
        raise DeviceIdAllocationError("Unable to allocate a unique device ID")

    monkeypatch.setattr(devices_api, "_validate_org_plant_access", _allow_plant_access)
    monkeypatch.setattr("app.api.v1.devices.DeviceOnboardingService.onboard_device", _raise_allocation)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_name": "Allocation Failure Device",
                "device_type": "compressor",
                "device_id_class": "active",
                "phase_type": "single",
                "data_source_type": "metered",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "DEVICE_ID_ALLOCATION_FAILED"
    assert payload["message"] == "Unable to allocate a unique device ID"
