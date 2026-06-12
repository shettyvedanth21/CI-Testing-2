from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

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
from app.models.device import Device, DeviceMQTTACL, DeviceMQTTCredential
from services.shared.tenant_context import TenantContext


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
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="AD00000012",
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    device_name="Scoped Compressor",
                    device_type="compressor",
                    device_id_class="active",
                    data_source_type="metered",
                    legacy_status="active",
                ),
                Device(
                    device_id="AD00000099",
                    tenant_id="ORG-2",
                    plant_id="PLANT-9",
                    device_name="Other Tenant Device",
                    device_type="compressor",
                    device_id_class="active",
                    data_source_type="metered",
                    legacy_status="active",
                ),
            ]
        )
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


@pytest.mark.asyncio
async def test_register_device_mqtt_credential_creates_hash_and_acl(device_app, monkeypatch):
    app, session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={"chip_id": "ESP32-001"},
        )

    assert response.status_code == 201
    body = response.json()["data"]
    plaintext = body["mqtt_password"]
    credential = body["credential"]

    assert plaintext
    assert credential["mqtt_username"] == "device:ORG-1:AD00000012"
    assert credential["publish_topic"] == "ORG-1/devices/AD00000012/telemetry"
    assert credential["status_topic"] == "ORG-1/devices/AD00000012/status"
    assert credential["subscribe_topic"] == "ORG-1/devices/AD00000012/cmd"
    assert credential["subscribe_topics"] == [
        "ORG-1/devices/AD00000012/cmd",
        "ORG-1/devices/AD00000012/config",
        "ORG-1/devices/AD00000012/ota",
    ]
    assert credential["chip_id"] == "ESP32-001"
    assert len(credential["acl_entries"]) == 7
    acl_entries = {
        (entry["access"], entry["permission"], entry["topic"])
        for entry in credential["acl_entries"]
    }
    assert acl_entries == {
        ("publish", "allow", "ORG-1/devices/AD00000012/telemetry"),
        ("publish", "allow", "ORG-1/devices/AD00000012/status"),
        ("subscribe", "allow", "ORG-1/devices/AD00000012/cmd"),
        ("subscribe", "allow", "ORG-1/devices/AD00000012/config"),
        ("subscribe", "allow", "ORG-1/devices/AD00000012/ota"),
        ("publish", "deny", "#"),
        ("subscribe", "deny", "#"),
    }

    async with session_factory() as session:
        stored = await session.get(DeviceMQTTCredential, credential["id"])
        acl_rows = (await session.execute(DeviceMQTTACL.__table__.select())).all()

    assert stored is not None
    assert stored.password_hash == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert stored.password_hash != plaintext
    assert stored.password_algorithm == "sha256"
    assert len(acl_rows) == 7


@pytest.mark.asyncio
async def test_onboard_device_returns_one_time_mqtt_bundle(device_app, monkeypatch):
    app, session_factory = device_app
    _apply_auth(monkeypatch)
    async def _allow_plant_access(*args, **kwargs):
        return None

    monkeypatch.setattr(devices_api, "_validate_org_plant_access", _allow_plant_access)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_id": "AD00000013",
                "device_name": "Onboarded Compressor",
                "device_type": "compressor",
                "device_id_class": "active",
                "phase_type": "three",
                "data_source_type": "metered",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 201
    body = response.json()["data"]
    device = body["device"]
    mqtt = body["mqtt"]

    assert device["device_id"] == mqtt["device_id"]
    assert mqtt == {
        "broker_host": "mqtt.test.local",
        "broker_port": 1883,
        "tenant_id": "ORG-1",
        "device_id": device["device_id"],
        "username": f"device:ORG-1:{device['device_id']}",
        "password": mqtt["password"],
        "publish_topic": f"ORG-1/devices/{device['device_id']}/telemetry",
        "status_topic": f"ORG-1/devices/{device['device_id']}/status",
        "subscribe_topics": [
            f"ORG-1/devices/{device['device_id']}/cmd",
            f"ORG-1/devices/{device['device_id']}/config",
            f"ORG-1/devices/{device['device_id']}/ota",
        ],
    }
    assert mqtt["password"]

    async with session_factory() as session:
        stored = (
            await session.execute(
                select(DeviceMQTTCredential).where(DeviceMQTTCredential.device_id == mqtt["device_id"])
            )
        ).scalar_one_or_none()

    assert stored is not None
    assert stored.password_hash == hashlib.sha256(mqtt["password"].encode("utf-8")).hexdigest()
    assert stored.password_hash != mqtt["password"]


@pytest.mark.asyncio
async def test_status_endpoint_never_returns_plaintext(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        create_response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )
        status_response = await client.get(
            "/api/v1/devices/AD00000012/mqtt-credential",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
        )

    assert create_response.status_code == 201
    assert status_response.status_code == 200
    status_body = status_response.json()["data"]
    assert "mqtt_password" not in status_response.text
    assert "mqtt_password" not in status_body
    assert status_body["mqtt_username"] == "device:ORG-1:AD00000012"


@pytest.mark.asyncio
async def test_revoke_disables_credential(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )
        revoke_response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/revoke",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
        )

    assert revoke_response.status_code == 200
    body = revoke_response.json()["data"]
    assert body["is_active"] is False
    assert body["revoked_at"] is not None
    assert all(entry["is_active"] is False for entry in body["acl_entries"])


@pytest.mark.asyncio
async def test_rotate_changes_hash_and_returns_new_plaintext(device_app, monkeypatch):
    app, session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        create_response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )
        rotate_response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/rotate",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={"chip_id": "ESP32-ROTATED"},
        )

    assert create_response.status_code == 201
    assert rotate_response.status_code == 200

    old_plaintext = create_response.json()["data"]["mqtt_password"]
    rotated = rotate_response.json()["data"]
    new_plaintext = rotated["mqtt_password"]

    assert new_plaintext != old_plaintext
    assert rotated["credential"]["rotated_at"] is not None
    assert rotated["credential"]["chip_id"] == "ESP32-ROTATED"

    async with session_factory() as session:
        stored = await session.get(DeviceMQTTCredential, rotated["credential"]["id"])

    assert stored is not None
    assert stored.password_hash == hashlib.sha256(new_plaintext.encode("utf-8")).hexdigest()
    assert stored.password_hash != hashlib.sha256(old_plaintext.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_rotate_reactivates_revoked_credential_and_acl_entries(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )
        revoke_response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/revoke",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
        )
        rotate_response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/rotate",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )

    assert revoke_response.status_code == 200
    assert rotate_response.status_code == 200
    rotated = rotate_response.json()["data"]["credential"]
    assert rotated["is_active"] is True
    assert rotated["revoked_at"] is None
    assert rotated["rotated_at"] is not None
    assert all(entry["is_active"] is True for entry in rotated["acl_entries"])


@pytest.mark.asyncio
async def test_wrong_tenant_device_cannot_be_managed(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch, tenant_id="ORG-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/AD00000099/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/devices/AD00000099/mqtt-credential"),
        ("post", "/api/v1/devices/AD00000099/mqtt-credential/revoke"),
        ("post", "/api/v1/devices/AD00000099/mqtt-credential/rotate"),
    ],
)
async def test_wrong_tenant_device_mqtt_routes_return_404(device_app, monkeypatch, method, path):
    app, _session_factory = device_app
    _apply_auth(monkeypatch, tenant_id="ORG-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        request = getattr(client, method)
        kwargs = {"headers": {"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"}}
        if method == "post":
            kwargs["json"] = {}
        response = await request(path, **kwargs)

    assert response.status_code == 404
    assert response.json()["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_plant_manager_cannot_manage_mqtt_credentials(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch, role="plant_manager")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_register_conflicts_when_credential_already_exists(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )
        second = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "DEVICE_MQTT_CREDENTIAL_ALREADY_EXISTS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/devices/AD00000012/mqtt-credential"),
        ("post", "/api/v1/devices/AD00000012/mqtt-credential/revoke"),
        ("post", "/api/v1/devices/AD00000012/mqtt-credential/rotate"),
    ],
)
async def test_missing_device_mqtt_credential_returns_404(device_app, monkeypatch, method, path):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        request = getattr(client, method)
        kwargs = {"headers": {"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"}}
        if method == "post":
            kwargs["json"] = {}
        response = await request(path, **kwargs)

    assert response.status_code == 404
    assert response.json()["code"] == "DEVICE_MQTT_CREDENTIAL_NOT_FOUND"


@pytest.mark.asyncio
async def test_status_after_revoke_returns_inactive_credential_without_plaintext(device_app, monkeypatch):
    app, _session_factory = device_app
    _apply_auth(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        create_response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/register",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={},
        )
        revoke_response = await client.post(
            "/api/v1/devices/AD00000012/mqtt-credential/revoke",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
        )
        status_response = await client.get(
            "/api/v1/devices/AD00000012/mqtt-credential",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
        )

    assert create_response.status_code == 201
    assert revoke_response.status_code == 200
    assert status_response.status_code == 200
    body = status_response.json()["data"]
    assert body["is_active"] is False
    assert body["revoked_at"] is not None
    assert "mqtt_password" not in status_response.text
