from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for existing in list(sys.path):
    try:
        existing_path = Path(existing).resolve()
    except Exception:
        continue
    if existing_path.parent == SERVICES_DIR.resolve() and existing_path != BASE_DIR.resolve():
        sys.path.remove(existing)
for module_name, module in list(sys.modules.items()):
    if module_name == "app" or module_name.startswith("app."):
        module_file = Path(getattr(module, "__file__", "") or "")
        if str(module_file) and BASE_DIR.resolve() not in module_file.resolve().parents:
            sys.modules.pop(module_name, None)
for path in (SERVICES_DIR, REPO_ROOT, BASE_DIR):
    path_str = str(path)
    if path_str in sys.path:
        sys.path.remove(path_str)
    sys.path.insert(0, path_str)

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

import app as device_app_module
from app.api.v1 import devices as devices_api
from app.api.v1.router import api_router
from app.database import Base, get_db
from app.models.device import Device, DeviceHardwareInstallation, HardwareUnit, HardwareUnitSequence
from app.schemas.device import DeviceHardwareInstallationCreate
from app.services.device_errors import HardwareTenantMismatchError
from app.services.hardware_inventory import HardwareInventoryService
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def hardware_app():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(HardwareUnitSequence(prefix="HWU", next_value=1))
        await session.commit()

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


@pytest.fixture
def auth_context(monkeypatch):
    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": "user-1",
            "tenant_id": "SH00000001",
            "role": "org_admin",
            "plant_ids": ["PLANT-1", "PLANT-2"],
            "is_authenticated": True,
        },
    )
    monkeypatch.setattr(
        devices_api.TenantContext,
        "from_request",
        classmethod(
            lambda cls, request: TenantContext(
                tenant_id="SH00000001",
                user_id="user-1",
                role="org_admin",
                plant_ids=["PLANT-1", "PLANT-2"],
                is_super_admin=False,
            )
        ),
    )
    monkeypatch.setattr(
        devices_api,
        "_list_tenant_plants",
        AsyncMock(
            return_value=[
                {"id": "PLANT-1", "name": "Plant 1"},
                {"id": "PLANT-2", "name": "Plant 2"},
            ]
        ),
    )


async def seed_device(
    session_factory,
    *,
    device_id: str,
    tenant_id: str = "SH00000001",
    plant_id: str = "PLANT-1",
    device_name: str = "Compressor Alpha",
) -> None:
    async with session_factory() as session:
        session.add(
            Device(
                device_id=device_id,
                tenant_id=tenant_id,
                plant_id=plant_id,
                device_name=device_name,
                device_type="compressor",
                device_id_class="active",
                data_source_type="metered",
                legacy_status="active",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_hardware_unit_id_is_generated_and_unit_name_is_saved(hardware_app, auth_context):
    app, session_factory = hardware_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        create_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-1",
                "unit_type": "energy_meter",
                "unit_name": "Main Energy Meter",
                "manufacturer": "Schneider",
                "model": "PM5000",
                "serial_number": "EM-001",
                "status": "available",
            },
        )

    assert create_response.status_code == 201
    payload = create_response.json()["data"]
    assert payload["hardware_unit_id"] == "HWU00000001"
    assert payload["unit_name"] == "Main Energy Meter"
    assert "metadata_json" not in payload

    async with session_factory() as session:
        row = await session.scalar(
            select(HardwareUnit).where(HardwareUnit.hardware_unit_id == "HWU00000001")
        )

    assert row is not None
    assert row.tenant_id == "SH00000001"
    assert row.plant_id == "PLANT-1"
    assert row.unit_type == "energy_meter"
    assert row.unit_name == "Main Energy Meter"
    assert row.status == "available"


@pytest.mark.asyncio
async def test_user_cannot_submit_manual_hardware_unit_id(hardware_app, auth_context):
    app, _ = hardware_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": "MANUAL-001",
                "plant_id": "PLANT-1",
                "unit_type": "ct_sensor",
                "unit_name": "CT1",
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_metadata_json_is_not_accepted_in_hardware_unit_contract(hardware_app, auth_context):
    app, _ = hardware_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-1",
                "unit_type": "esp32",
                "unit_name": "ESP32 Main",
                "metadata_json": {"firmware": "1.0.1"},
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_unit_type_is_rejected_by_backend(hardware_app, auth_context):
    app, _ = hardware_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-1",
                "unit_type": "custom_sensor",
                "unit_name": "Custom Sensor",
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unit_name_and_unit_type_are_preserved_on_update_and_list(hardware_app, auth_context):
    app, session_factory = hardware_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first_create = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-1",
                "unit_type": "ct_sensor",
                "unit_name": "CT1",
            },
        )
        second_create = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-1",
                "unit_type": "ct_sensor",
                "unit_name": "CT2",
                "status": "retired",
            },
        )
        first_hardware_unit_id = first_create.json()["data"]["hardware_unit_id"]
        update_response = await client.put(
            f"/api/v1/devices/hardware-units/{first_hardware_unit_id}",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "unit_name": "CT1 Main",
                "manufacturer": "ABB",
                "model": "CT-200",
                "serial_number": "CT-001",
            },
        )
        list_response = await client.get(
            "/api/v1/devices/hardware-units/list",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
        )

    assert first_create.status_code == 201
    assert second_create.status_code == 201
    assert update_response.status_code == 200
    assert list_response.status_code == 200

    rows = list_response.json()["data"]
    assert [row["hardware_unit_id"] for row in rows] == ["HWU00000002", "HWU00000001"]
    assert rows[0]["unit_type"] == "ct_sensor"
    assert rows[0]["unit_name"] == "CT2"
    assert rows[1]["unit_type"] == "ct_sensor"
    assert rows[1]["unit_name"] == "CT1 Main"
    assert "metadata_json" not in rows[0]

    async with session_factory() as session:
        stored_rows = list(
            (
                await session.execute(
                    select(HardwareUnit).order_by(HardwareUnit.hardware_unit_id.asc())
                )
            ).scalars()
        )

    assert stored_rows[0].hardware_unit_id == "HWU00000001"
    assert stored_rows[0].unit_name == "CT1 Main"
    assert stored_rows[0].unit_type == "ct_sensor"
    assert stored_rows[1].hardware_unit_id == "HWU00000002"
    assert stored_rows[1].unit_name == "CT2"
    assert stored_rows[1].status == "retired"


@pytest.mark.asyncio
async def test_generated_hardware_unit_id_sequence_increments(hardware_app, auth_context):
    app, _ = hardware_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-1",
                "unit_type": "oil_sensor",
                "unit_name": "Oil Sensor A",
            },
        )
        second_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-2",
                "unit_type": "esp32",
                "unit_name": "ESP32 Main",
            },
        )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["data"]["hardware_unit_id"] == "HWU00000001"
    assert second_response.json()["data"]["hardware_unit_id"] == "HWU00000002"


@pytest.mark.asyncio
async def test_install_hardware_persists_commissioned_date_and_current_installations(hardware_app, auth_context):
    app, session_factory = hardware_app
    await seed_device(session_factory, device_id="AD00000004")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        hardware_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-1",
                "unit_type": "energy_meter",
                "unit_name": "Main Energy Meter",
            },
        )
        hardware_unit_id = hardware_response.json()["data"]["hardware_unit_id"]

        install_response = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": hardware_unit_id,
                "installation_role": "main_meter",
                "commissioned_at": "2026-04-07T09:00:00Z",
                "notes": "Primary meter",
            },
        )
        current_response = await client.get(
            "/api/v1/devices/AD00000004/hardware-installations/current",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
        )
        history_response = await client.get(
            "/api/v1/devices/hardware-installations/history",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
        )

    assert install_response.status_code == 201
    assert current_response.status_code == 200
    assert history_response.status_code == 200
    install_payload = install_response.json()["data"]
    assert install_payload["commissioned_at"].startswith("2026-04-07T09:00:00")
    assert install_payload["decommissioned_at"] is None
    assert install_payload["is_active"] is True
    assert current_response.json()["total"] == 1
    assert current_response.json()["data"][0]["hardware_unit_id"] == hardware_unit_id
    assert current_response.json()["data"][0]["is_active"] is True
    assert history_response.json()["total"] == 1

    async with session_factory() as session:
        row = await session.scalar(
            select(DeviceHardwareInstallation).where(
                DeviceHardwareInstallation.hardware_unit_id == hardware_unit_id
            )
        )

    assert row is not None
    assert row.device_id == "AD00000004"
    assert row.installation_role == "main_meter"
    assert row.decommissioned_at is None


@pytest.mark.asyncio
async def test_duplicate_active_install_for_same_hardware_unit_is_rejected(hardware_app, auth_context):
    app, session_factory = hardware_app
    await seed_device(session_factory, device_id="AD00000004")
    await seed_device(session_factory, device_id="AD00000005", device_name="Compressor Beta")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        hardware_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"plant_id": "PLANT-1", "unit_type": "esp32", "unit_name": "ESP32 Main"},
        )
        hardware_unit_id = hardware_response.json()["data"]["hardware_unit_id"]
        first_install = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"hardware_unit_id": hardware_unit_id, "installation_role": "controller"},
        )
        second_install = await client.post(
            "/api/v1/devices/AD00000005/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"hardware_unit_id": hardware_unit_id, "installation_role": "controller"},
        )

    assert first_install.status_code == 201
    assert second_install.status_code == 409
    assert second_install.json()["code"] == "HARDWARE_INSTALLATION_CONFLICT"


@pytest.mark.asyncio
async def test_duplicate_active_role_on_same_device_is_rejected(hardware_app, auth_context):
    app, session_factory = hardware_app
    await seed_device(session_factory, device_id="AD00000004")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first_unit = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"plant_id": "PLANT-1", "unit_type": "ct_sensor", "unit_name": "CT1"},
        )
        second_unit = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"plant_id": "PLANT-1", "unit_type": "ct_sensor", "unit_name": "CT2"},
        )
        first_install = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": first_unit.json()["data"]["hardware_unit_id"],
                "installation_role": "ct1",
            },
        )
        second_install = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": second_unit.json()["data"]["hardware_unit_id"],
                "installation_role": "ct1",
            },
        )

    assert first_install.status_code == 201
    assert second_install.status_code == 409
    assert second_install.json()["code"] == "HARDWARE_INSTALLATION_CONFLICT"


@pytest.mark.asyncio
async def test_invalid_installation_role_is_rejected_by_backend(hardware_app, auth_context):
    app, session_factory = hardware_app
    await seed_device(session_factory, device_id="AD00000004")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        hardware_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"plant_id": "PLANT-1", "unit_type": "esp32", "unit_name": "ESP32 Main"},
        )
        hardware_unit_id = hardware_response.json()["data"]["hardware_unit_id"]
        install_response = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": hardware_unit_id,
                "installation_role": "custom_slot",
            },
        )

    assert install_response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unit_type", "unit_name", "installation_role"),
    [
        ("esp32", "ESP32 Main", "main_meter"),
        ("ct_sensor", "CT1", "controller"),
        ("energy_meter", "Main Energy Meter", "ct1"),
    ],
)
async def test_invalid_hardware_role_pairing_is_rejected(
    hardware_app,
    auth_context,
    unit_type,
    unit_name,
    installation_role,
):
    app, session_factory = hardware_app
    await seed_device(session_factory, device_id="AD00000004")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        hardware_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"plant_id": "PLANT-1", "unit_type": unit_type, "unit_name": unit_name},
        )
        hardware_unit_id = hardware_response.json()["data"]["hardware_unit_id"]

        install_response = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": hardware_unit_id,
                "installation_role": installation_role,
            },
        )

    assert install_response.status_code == 400
    assert install_response.json()["code"] == "HARDWARE_INSTALLATION_COMPATIBILITY_INVALID"


@pytest.mark.asyncio
async def test_decommission_preserves_history_and_reinstallation_creates_new_row(hardware_app, auth_context):
    app, session_factory = hardware_app
    await seed_device(session_factory, device_id="AD00000004")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        hardware_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"plant_id": "PLANT-1", "unit_type": "energy_meter", "unit_name": "Main Energy Meter"},
        )
        hardware_unit_id = hardware_response.json()["data"]["hardware_unit_id"]
        install_response = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": hardware_unit_id,
                "installation_role": "main_meter",
                "commissioned_at": "2026-04-07T09:00:00Z",
            },
        )
        installation_id = install_response.json()["data"]["id"]
        decommission_response = await client.post(
            f"/api/v1/devices/hardware-installations/{installation_id}/decommission",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "decommissioned_at": "2026-04-08T09:00:00Z",
                "notes": "Replaced meter",
            },
        )
        reinstall_response = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": hardware_unit_id,
                "installation_role": "main_meter",
                "commissioned_at": "2026-04-09T09:00:00Z",
            },
        )
        device_history_response = await client.get(
            "/api/v1/devices/AD00000004/hardware-installations/history",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
        )
        decommissioned_history_response = await client.get(
            "/api/v1/devices/hardware-installations/history?state=decommissioned",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
        )

    assert decommission_response.status_code == 200
    assert decommission_response.json()["data"]["decommissioned_at"].startswith("2026-04-08T09:00:00")
    assert decommission_response.json()["data"]["is_active"] is False
    assert reinstall_response.status_code == 201
    assert reinstall_response.json()["data"]["id"] != installation_id
    assert reinstall_response.json()["data"]["is_active"] is True
    history_rows = device_history_response.json()["data"]
    assert [row["is_active"] for row in history_rows] == [True, False]
    assert decommissioned_history_response.json()["total"] == 1

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(DeviceHardwareInstallation)
                    .where(DeviceHardwareInstallation.hardware_unit_id == hardware_unit_id)
                    .order_by(DeviceHardwareInstallation.id.asc())
                )
            ).scalars()
        )

        assert len(rows) == 2


    assert rows[0].decommissioned_at is not None
    assert rows[0].active_hardware_unit_key is None
    assert rows[1].decommissioned_at is None
    assert rows[1].active_hardware_unit_key == hardware_unit_id


@pytest.mark.asyncio
async def test_current_hardware_mapping_projection_returns_readable_active_rows(hardware_app, auth_context):
    app, session_factory = hardware_app
    await seed_device(session_factory, device_id="AD00000004")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        hardware_response = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "plant_id": "PLANT-1",
                "unit_type": "ct_sensor",
                "unit_name": "CT1",
                "manufacturer": "ABB",
                "model": "CT-200",
                "serial_number": "CT-001",
            },
        )
        hardware_unit_id = hardware_response.json()["data"]["hardware_unit_id"]
        install_response = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": hardware_unit_id,
                "installation_role": "ct1",
            },
        )
        mapping_response = await client.get(
            "/api/v1/devices/hardware-mappings?device_id=AD00000004",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
        )

    assert install_response.status_code == 201
    assert mapping_response.status_code == 200
    payload = mapping_response.json()
    assert payload["total"] == 1
    row = payload["data"][0]
    assert row["device_id"] == "AD00000004"
    assert row["plant_name"] == "Plant 1"
    assert row["installation_role"] == "ct1"
    assert row["installation_role_label"] == "CT1"
    assert row["hardware_unit_id"] == hardware_unit_id
    assert row["hardware_type"] == "ct_sensor"
    assert row["hardware_type_label"] == "CT Sensor"
    assert row["hardware_name"] == "CT1"
    assert row["manufacturer"] == "ABB"
    assert row["model"] == "CT-200"
    assert row["serial_number"] == "CT-001"
    assert row["status"] == "Active"


@pytest.mark.asyncio
async def test_installation_rejects_tenant_and_plant_mismatch(hardware_app, auth_context):
    app, session_factory = hardware_app
    await seed_device(session_factory, device_id="AD00000004", tenant_id="SH00000001", plant_id="PLANT-1")

    async with session_factory() as session:
        session.add(
            Device(
                device_id="AD99999999",
                tenant_id="SH00000002",
                plant_id="PLANT-9",
                device_name="Other Tenant Device",
                device_type="compressor",
                device_id_class="active",
                data_source_type="metered",
                legacy_status="active",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        plant_two_hardware = await client.post(
            "/api/v1/devices/hardware-units",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={"plant_id": "PLANT-2", "unit_type": "ct_sensor", "unit_name": "CT2"},
        )
        mismatch_response = await client.post(
            "/api/v1/devices/AD00000004/hardware-installations",
            headers={"X-Tenant-Id": "SH00000001", "Authorization": "Bearer token"},
            json={
                "hardware_unit_id": plant_two_hardware.json()["data"]["hardware_unit_id"],
                "installation_role": "ct2",
            },
        )

    assert mismatch_response.status_code == 400
    assert mismatch_response.json()["code"] == "HARDWARE_PLANT_MISMATCH"


@pytest.mark.asyncio
async def test_installation_service_rejects_tenant_mismatch():
    session = AsyncMock()
    ctx = TenantContext(
        tenant_id="SH00000001",
        user_id="user-1",
        role="org_admin",
        plant_ids=["PLANT-1"],
        is_super_admin=False,
    )
    service = HardwareInventoryService(session, ctx)
    service._require_device = AsyncMock(
        return_value=SimpleNamespace(
            device_id="AD00000004",
            tenant_id="SH00000001",
            plant_id="PLANT-1",
        )
    )
    service._require_hardware_unit = AsyncMock(
        return_value=SimpleNamespace(
            hardware_unit_id="HWU00000001",
            tenant_id="SH00000002",
            plant_id="PLANT-1",
            status="available",
        )
    )

    with pytest.raises(HardwareTenantMismatchError):
        await service.install_hardware(
            "AD00000004",
            DeviceHardwareInstallationCreate(
                hardware_unit_id="HWU00000001",
                installation_role="controller",
            ),
        )
