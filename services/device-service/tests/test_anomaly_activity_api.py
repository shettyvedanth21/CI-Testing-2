from __future__ import annotations

import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import os
import sys

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
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

from app.api.v1 import devices as devices_api
from app.api.v1.router import api_router
from app.database import Base, get_db
from app.models.device import (
    Device,
    MachineAnomalyBaseline,
    MachineAnomalyDailyCount,
    MachineAnomalyWeeklyCount,
    MachineAnomalyEvent,
)
from services.shared.tenant_context import TenantContext
from services.shared.feature_entitlements import build_feature_entitlement_state
from app.services.anomaly.tz import local_today


def _today() -> date:
    return local_today()


def _week_start() -> date:
    today = _today()
    return today - timedelta(days=today.weekday())


async def _build_anomaly_app(monkeypatch: pytest.MonkeyPatch, seed_extra: list | None = None):
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
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-A",
                    device_name="Tenant A Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
                Device(
                    device_id="DEVICE-B",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-B",
                    device_name="Tenant B Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
                Device(
                    device_id="DEVICE-NONE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-A",
                    device_name="No Baseline Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
            ]
        )
        if seed_extra:
            session.add_all(seed_extra)
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")
        role = request.headers.get("X-Role", "org_admin")
        entitlements = build_feature_entitlement_state(
            role=role,
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id,
            user_id=request.headers.get("X-User-Id", "user-1"),
            role=role,
            plant_ids=[request.headers.get("X-Plant-Id", "PLANT-A")],
            is_super_admin=role == "super_admin",
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        request.state.role = role
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": request.state.tenant_context.user_id,
            "tenant_id": request.state.tenant_context.tenant_id,
            "role": request.state.tenant_context.role,
            "plant_ids": list(request.state.tenant_context.plant_ids),
            "is_authenticated": True,
        },
    )

    return app, session_factory, engine


def _active_baseline(**overrides) -> MachineAnomalyBaseline:
    defaults = dict(
        tenant_id="TENANT-A",
        device_id="DEVICE-A",
        field_name="current_avg",
        time_window="5min",
        baseline_mean=10.0,
        baseline_std=1.0,
        status="active",
        baseline_version=1,
    )
    defaults.update(overrides)
    return MachineAnomalyBaseline(**defaults)


def _daily_count(**overrides) -> MachineAnomalyDailyCount:
    defaults = dict(
        tenant_id="TENANT-A",
        device_id="DEVICE-A",
        date=_today(),
        total_count=5,
        mild_count=3,
        strong_count=1,
        severe_count=1,
        supply_related_count=1,
        top_signal="current_avg",
        avg_confidence=0.85,
    )
    defaults.update(overrides)
    return MachineAnomalyDailyCount(**defaults)


def _weekly_count(**overrides) -> MachineAnomalyWeeklyCount:
    defaults = dict(
        tenant_id="TENANT-A",
        device_id="DEVICE-A",
        week_start_date=_week_start(),
        total_count=12,
        mild_count=7,
        strong_count=3,
        severe_count=2,
        supply_related_count=2,
        top_signal="current_avg",
        avg_confidence=0.88,
        week_over_week_change=3,
    )
    defaults.update(overrides)
    return MachineAnomalyWeeklyCount(**defaults)


def _event(**overrides) -> MachineAnomalyEvent:
    event_time = datetime.combine(_today(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=10)
    defaults = dict(
        tenant_id="TENANT-A",
        device_id="DEVICE-A",
        signal_field="current_avg",
        signal_value=14.0,
        baseline_mean=10.0,
        baseline_std=1.0,
        z_score=4.0,
        anomaly_type="deviation",
        severity="severe",
        confidence=0.9,
        supply_related=False,
        startup_adjacent=False,
        mode_change=False,
        recurring=False,
        time_window="5min",
        duration_seconds=300,
        occurred_at=event_time,
    )
    defaults.update(overrides)
    return MachineAnomalyEvent(**defaults)


@pytest.mark.asyncio
async def test_active_baseline_with_daily_and_weekly(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _daily_count(),
            _weekly_count(),
            _event(),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == "DEVICE-A"
    assert body["available"] is True
    assert body["state"] == "available"
    assert body["baseline_status"] == "active"
    assert body["baseline_field_count"] == 3
    assert body["today_counts"]["total"] == 5
    assert body["today_counts"]["mild"] == 3
    assert body["today_counts"]["strong"] == 1
    assert body["today_counts"]["severe"] == 1
    assert body["today_counts"]["supply_related"] == 1
    assert body["this_week_counts"]["total"] == 12
    assert body["this_week_counts"]["supply_related"] == 2
    assert body["week_over_week_change"] == 3
    assert body["top_signal"] == "current_avg"
    assert body["avg_confidence"] == pytest.approx(0.88)
    assert body["last_anomaly"]["signal_field"] == "current_avg"
    assert body["last_anomaly"]["severity"] == "severe"
    assert body["last_anomaly"]["anomaly_type"] == "deviation"
    assert body["last_anomaly"]["signal_value"] == 14.0
    assert body["last_anomaly"]["baseline_mean"] == 10.0
    assert body["last_anomaly"]["z_score"] == 4.0
    assert body["last_anomaly"]["duration_seconds"] == 300
    assert body["last_anomaly"]["ended_at"] is None
    assert body["last_anomaly"]["confidence"] == 0.9
    assert body["last_anomaly"]["supply_related"] is False
    assert body["last_anomaly"]["startup_adjacent"] is False
    assert body["last_anomaly"]["mode_change"] is False
    assert body["last_anomaly"]["recurring"] is False


@pytest.mark.asyncio
async def test_no_baseline_returns_unavailable(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-NONE/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["state"] == "unavailable"
    assert body["baseline_status"] == "none"
    assert body["today_counts"] is None
    assert body["this_week_counts"] is None


@pytest.mark.asyncio
async def test_candidate_baseline_returns_learning(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            MachineAnomalyBaseline(
                tenant_id="TENANT-A",
                device_id="DEVICE-A",
                field_name="current_avg",
                time_window="5min",
                status="candidate",
                baseline_version=1,
            ),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["state"] == "learning"
    assert body["baseline_status"] == "candidate"
    assert body["baseline_field_count"] == 1
    assert body["today_counts"] is None
    assert body["this_week_counts"] is None


@pytest.mark.asyncio
async def test_active_baseline_no_rows_returns_zero_objects(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["state"] == "available"
    assert body["today_counts"] is not None
    assert body["today_counts"]["total"] == 0
    assert body["today_counts"]["mild"] == 0
    assert body["today_counts"]["strong"] == 0
    assert body["today_counts"]["severe"] == 0
    assert body["today_counts"]["supply_related"] == 0
    assert body["this_week_counts"] is not None
    assert body["this_week_counts"]["total"] == 0
    assert body["last_anomaly"] is None


@pytest.mark.asyncio
async def test_active_baseline_daily_no_weekly(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _daily_count(),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["today_counts"]["total"] == 5
    assert body["this_week_counts"] is not None
    assert body["this_week_counts"]["total"] == 0
    assert body["week_over_week_change"] is None


@pytest.mark.asyncio
async def test_active_baseline_weekly_no_daily(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _weekly_count(),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["today_counts"] is not None
    assert body["today_counts"]["total"] == 0
    assert body["this_week_counts"]["total"] == 12
    assert body["week_over_week_change"] == 3


@pytest.mark.asyncio
async def test_stale_state(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    stale_created = datetime.now(timezone.utc) - timedelta(hours=3)
    stale_updated = datetime.now(timezone.utc) - timedelta(hours=3)
    async with session_factory() as session:
        session.add_all([
            Device(
                device_id="DEVICE-A", tenant_id="TENANT-A", plant_id="PLANT-A",
                device_name="Tenant A Device", device_type="compressor", device_id_class="active",
            ),
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
        ])
        await session.commit()

    async with session_factory() as session:
        session.add(MachineAnomalyDailyCount(
            tenant_id="TENANT-A", device_id="DEVICE-A", date=_today(),
            total_count=5, mild_count=3, strong_count=1, severe_count=1,
            supply_related_count=0, top_signal="current_avg", avg_confidence=0.85,
            created_at=stale_created, updated_at=stale_updated,
        ))
        await session.commit()

    async with session_factory() as session:
        session.add(MachineAnomalyWeeklyCount(
            tenant_id="TENANT-A", device_id="DEVICE-A", week_start_date=_week_start(),
            total_count=12, mild_count=7, strong_count=3, severe_count=2,
            week_over_week_change=3, created_at=stale_created, updated_at=stale_updated,
        ))
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")
        role = request.headers.get("X-Role", "org_admin")
        entitlements = build_feature_entitlement_state(
            role=role,
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id, user_id=request.headers.get("X-User-Id", "user-1"),
            role=role, plant_ids=[request.headers.get("X-Plant-Id", "PLANT-A")],
            is_super_admin=role == "super_admin",
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        request.state.role = role
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        devices_api, "get_auth_state",
        lambda request: {
            "user_id": request.state.tenant_context.user_id,
            "tenant_id": request.state.tenant_context.tenant_id,
            "role": request.state.tenant_context.role,
            "plant_ids": list(request.state.tenant_context.plant_ids),
            "is_authenticated": True,
        },
    )

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["state"] == "stale"
    assert body["updated_minutes_ago"] is not None
    assert body["updated_minutes_ago"] > 60


@pytest.mark.asyncio
async def test_wrong_tenant_gets_404(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-B", "X-Plant-Id": "PLANT-B"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_nonexistent_device_gets_404(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-NOEXIST/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_endpoint_reads_precomputed_only(monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import AsyncMock, patch

    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _daily_count(),
            _weekly_count(),
            _event(),
        ],
    )
    with patch("app.services.anomaly.service.detect_device_anomalies", new_callable=AsyncMock) as mock_detect, \
         patch("app.services.anomaly.service.aggregate_daily_counts_for_device", new_callable=AsyncMock) as mock_agg:
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                resp = await client.get(
                    "/api/v1/devices/DEVICE-A/anomaly-activity",
                    headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
                )
        finally:
            await engine.dispose()

        mock_detect.assert_not_called()
        mock_agg.assert_not_called()

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_fewer_than_three_active_fields_returns_learning(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["state"] == "learning"
    assert body["baseline_status"] == "partial"
    assert body["baseline_field_count"] == 2


@pytest.mark.asyncio
async def test_three_active_fields_returns_available(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["state"] == "available"
    assert body["baseline_field_count"] == 3


@pytest.mark.asyncio
async def test_anomaly_events_endpoint_returns_paginated_list(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _event(),
            _event(
                signal_field="power",
                severity="mild",
                z_score=1.8,
                occurred_at=datetime.combine(_today(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=9),
            ),
            _event(
                signal_field="power_factor",
                severity="strong",
                z_score=3.2,
                occurred_at=datetime.combine(_today(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=8),
                supply_related=True,
                startup_adjacent=True,
                recurring=True,
            ),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-events",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert len(body["items"]) == 3
    assert body["items"][0]["signal_field"] == "current_avg"
    assert body["items"][0]["severity"] == "severe"
    assert body["items"][0]["signal_value"] == 14.0
    assert body["items"][0]["baseline_mean"] == 10.0
    assert body["items"][0]["z_score"] == 4.0
    assert body["items"][0]["confidence"] == 0.9
    assert body["items"][0]["startup_adjacent"] is False
    assert body["items"][0]["recurring"] is False
    last = body["items"][-1]
    assert last["supply_related"] is True
    assert last["startup_adjacent"] is True
    assert last["recurring"] is True


@pytest.mark.asyncio
async def test_anomaly_events_endpoint_pagination(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _event(),
            _event(signal_field="power", severity="mild", occurred_at=datetime.combine(_today(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-events?limit=1&offset=0",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1


@pytest.mark.asyncio
async def test_anomaly_events_endpoint_no_events(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-events",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_anomaly_events_endpoint_wrong_tenant(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _event(),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-events",
                headers={"X-Tenant-Id": "TENANT-B", "X-Plant-Id": "PLANT-B"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_last_anomaly_includes_context_flags_and_ongoing(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _event(
                signal_field="power",
                severity="strong",
                z_score=2.5,
                supply_related=True,
                startup_adjacent=True,
                mode_change=True,
                recurring=True,
                ended_at=None,
                occurred_at=datetime.combine(_today(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=12),
            ),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    la = resp.json()["last_anomaly"]
    assert la is not None
    assert la["signal_field"] == "power"
    assert la["severity"] == "strong"
    assert la["supply_related"] is True
    assert la["startup_adjacent"] is True
    assert la["mode_change"] is True
    assert la["recurring"] is True
    assert la["ended_at"] is None
    assert la["confidence"] == 0.9


@pytest.mark.asyncio
async def test_last_anomaly_with_ended_at_is_resolved(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _event(
                ended_at=datetime.combine(_today(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=10, minute=5),
            ),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    la = resp.json()["last_anomaly"]
    assert la is not None
    assert la["ended_at"] is not None
    assert la["ended_at"].endswith("Z") or la["ended_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_weekly_supply_related_from_weekly_row(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _daily_count(supply_related_count=1),
            _weekly_count(supply_related_count=3),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    week = body["this_week_counts"]
    assert week is not None, f"this_week_counts is None, body={body}"
    assert week["supply_related"] == 3


@pytest.mark.asyncio
async def test_weekly_top_signal_and_avg_confidence_from_weekly_row(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _daily_count(top_signal="power", avg_confidence=0.7),
            _weekly_count(top_signal="current_avg", avg_confidence=0.88),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["top_signal"] == "current_avg"
    assert body["avg_confidence"] == pytest.approx(0.88)


@pytest.mark.asyncio
async def test_weekly_top_signal_falls_back_to_daily_when_weekly_missing(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_anomaly_app(
        monkeypatch,
        seed_extra=[
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
            _daily_count(top_signal="power", avg_confidence=0.7),
        ],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["top_signal"] == "power"
    assert body["avg_confidence"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_fresh_state_uses_updated_at_not_created_at(monkeypatch: pytest.MonkeyPatch):
    old_created = datetime.now(timezone.utc) - timedelta(hours=72)
    recent_updated = datetime.now(timezone.utc) - timedelta(minutes=5)
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
        session.add_all([
            Device(
                device_id="DEVICE-A", tenant_id="TENANT-A", plant_id="PLANT-A",
                device_name="Tenant A Device", device_type="compressor", device_id_class="active",
            ),
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
        ])
        await session.commit()

    async with session_factory() as session:
        session.add(MachineAnomalyDailyCount(
            tenant_id="TENANT-A", device_id="DEVICE-A", date=_today(),
            total_count=5, mild_count=3, strong_count=1, severe_count=1,
            supply_related_count=0, top_signal="current_avg", avg_confidence=0.85,
            created_at=old_created, updated_at=recent_updated,
        ))
        await session.commit()

    async with session_factory() as session:
        session.add(MachineAnomalyWeeklyCount(
            tenant_id="TENANT-A", device_id="DEVICE-A", week_start_date=_week_start(),
            total_count=12, mild_count=7, strong_count=3, severe_count=2,
            week_over_week_change=3, created_at=old_created, updated_at=recent_updated,
        ))
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")
        role = request.headers.get("X-Role", "org_admin")
        entitlements = build_feature_entitlement_state(
            role=role,
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id, user_id=request.headers.get("X-User-Id", "user-1"),
            role=role, plant_ids=[request.headers.get("X-Plant-Id", "PLANT-A")],
            is_super_admin=role == "super_admin",
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        request.state.role = role
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        devices_api, "get_auth_state",
        lambda request: {
            "user_id": request.state.tenant_context.user_id,
            "tenant_id": request.state.tenant_context.tenant_id,
            "role": request.state.tenant_context.role,
            "plant_ids": list(request.state.tenant_context.plant_ids),
            "is_authenticated": True,
        },
    )

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["state"] == "available"
    assert body["updated_minutes_ago"] is not None
    assert body["updated_minutes_ago"] < 60


@pytest.mark.asyncio
async def test_stale_when_updated_at_old_despite_recent_created_at(monkeypatch: pytest.MonkeyPatch):
    recent_created = datetime.now(timezone.utc) - timedelta(minutes=5)
    stale_updated = datetime.now(timezone.utc) - timedelta(hours=3)
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
        session.add_all([
            Device(
                device_id="DEVICE-A", tenant_id="TENANT-A", plant_id="PLANT-A",
                device_name="Tenant A Device", device_type="compressor", device_id_class="active",
            ),
            _active_baseline(field_name="current_avg"),
            _active_baseline(field_name="power"),
            _active_baseline(field_name="voltage_avg"),
        ])
        await session.commit()

    async with session_factory() as session:
        session.add(MachineAnomalyDailyCount(
            tenant_id="TENANT-A", device_id="DEVICE-A", date=_today(),
            total_count=5, mild_count=3, strong_count=1, severe_count=1,
            supply_related_count=0, top_signal="current_avg", avg_confidence=0.85,
            created_at=recent_created, updated_at=stale_updated,
        ))
        await session.commit()

    async with session_factory() as session:
        session.add(MachineAnomalyWeeklyCount(
            tenant_id="TENANT-A", device_id="DEVICE-A", week_start_date=_week_start(),
            total_count=12, mild_count=7, strong_count=3, severe_count=2,
            week_over_week_change=3, created_at=recent_created, updated_at=stale_updated,
        ))
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")
        role = request.headers.get("X-Role", "org_admin")
        entitlements = build_feature_entitlement_state(
            role=role,
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id, user_id=request.headers.get("X-User-Id", "user-1"),
            role=role, plant_ids=[request.headers.get("X-Plant-Id", "PLANT-A")],
            is_super_admin=role == "super_admin",
            entitlements=entitlements,
        )
        request.state.feature_entitlements = entitlements
        request.state.role = role
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        devices_api, "get_auth_state",
        lambda request: {
            "user_id": request.state.tenant_context.user_id,
            "tenant_id": request.state.tenant_context.tenant_id,
            "role": request.state.tenant_context.role,
            "plant_ids": list(request.state.tenant_context.plant_ids),
            "is_authenticated": True,
        },
    )

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/anomaly-activity",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["state"] == "stale"
    assert body["updated_minutes_ago"] is not None
    assert body["updated_minutes_ago"] > 60
