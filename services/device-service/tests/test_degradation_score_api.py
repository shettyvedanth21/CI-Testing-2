from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
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
from app.models.device import Device, MachineHealthLatest, MachineHealthHistory
from services.shared.tenant_context import TenantContext
from services.shared.feature_entitlements import build_feature_entitlement_state


async def _build_degradation_app(monkeypatch: pytest.MonkeyPatch, seed_latest: list | None = None):
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
                    device_name="No Latest Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
            ]
        )
        if seed_latest:
            session.add_all(seed_latest)
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


def _scored_latest(**overrides) -> MachineHealthLatest:
    defaults = dict(
        device_id="DEVICE-A",
        tenant_id="TENANT-A",
        score=1.0,
        status="healthy",
        confidence=0.9,
        baseline_version=1,
        baseline_quality="high",
        top_reasons_json=None,
        contributions_json=json.dumps([
            {"signal": "current_variability_drift", "weight": 0.25, "drift": 0.0, "available": True},
            {"signal": "power_factor_drop", "weight": 0.25, "drift": 0.0, "available": True},
            {"signal": "abnormal_power_draw", "weight": 0.20, "drift": 0.0, "available": True},
            {"signal": "phase_imbalance_drift", "weight": 0.15, "drift": 0.0, "available": True},
            {"signal": "trend_worsening", "weight": 0.15, "drift": 0.0, "available": True},
        ]),
        computed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    defaults.update(overrides)
    return MachineHealthLatest(**defaults)


@pytest.mark.asyncio
async def test_scored_row_returns_full_payload(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest()],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == "DEVICE-A"
    assert body["available"] is True
    assert body["state"] == "scored"
    assert body["score"] == 1.0
    assert body["status"] == "healthy"
    assert body["confidence"] == 0.9
    assert body["baseline_quality"] == "high"
    assert body["top_reasons"] == []
    assert len(body["contributions"]) == 5
    assert body["computed_at"] is not None
    assert body["updated_minutes_ago"] is not None


@pytest.mark.asyncio
async def test_missing_row_returns_unavailable(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-NONE/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == "DEVICE-NONE"
    assert body["available"] is False
    assert body["state"] == "unavailable"
    assert body["score"] is None
    assert body["top_reasons"] == []
    assert body["contributions"] == []


@pytest.mark.asyncio
async def test_learning_row_returns_learning_state(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(status="learning", score=None, confidence=0.1)],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["state"] == "learning"
    assert body["status"] == "learning"


@pytest.mark.asyncio
async def test_stale_row_returns_stale_state(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(computed_at=datetime.now(timezone.utc) - timedelta(hours=2))],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["state"] == "stale"
    assert body["updated_minutes_ago"] > 60


@pytest.mark.asyncio
async def test_malformed_json_does_not_crash(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(
            top_reasons_json="{broken",
            contributions_json="not-json",
        )],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["state"] == "scored"
    assert body["top_reasons"] == []
    assert body["contributions"] == []


@pytest.mark.asyncio
async def test_wrong_tenant_gets_404(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest()],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-B", "X-Plant-Id": "PLANT-B"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_nonexistent_device_gets_404(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-NOEXIST/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_endpoint_reads_latest_only_not_telemetry(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest()],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 1.0
    assert body["state"] == "scored"


@pytest.mark.asyncio
async def test_insufficient_signals_returns_learning_state(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(
            status="insufficient_signals",
            score=None,
            confidence=0.1,
            signal_completeness=1 / 3,
            top_reasons_json=json.dumps(["insufficient_signal_coverage:power_factor_drop,current_variability_drift"]),
            contributions_json=json.dumps([
                {"signal": "current_variability_drift", "weight": 0.25, "drift": 0, "available": False},
                {"signal": "power_factor_drop", "weight": 0.25, "drift": 0, "available": False},
                {"signal": "abnormal_power_draw", "weight": 0.20, "drift": 0.3, "available": True},
            ]),
        )],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["state"] == "learning"
    assert body["status"] == "insufficient_signals"
    assert body["score"] is None


@pytest.mark.asyncio
async def test_legacy_learning_with_insufficient_marker_returns_learning_state(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(
            status="learning",
            score=None,
            confidence=0.1,
            signal_completeness=None,
            top_reasons_json=json.dumps(["insufficient_signal_coverage:power_factor_drop"]),
            contributions_json=json.dumps([
                {"signal": "current_variability_drift", "weight": 0.25, "drift": 0, "available": False},
                {"signal": "power_factor_drop", "weight": 0.25, "drift": 0, "available": False},
                {"signal": "abnormal_power_draw", "weight": 0.20, "drift": 0.3, "available": True},
            ]),
        )],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["state"] == "learning"
    assert body["status"] == "learning"


@pytest.mark.asyncio
async def test_low_confidence_returns_learning_state(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(
            status="healthy",
            score=1.2,
            confidence=0.15,
        )],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["state"] == "learning"
    assert body["confidence"] < 0.3


@pytest.mark.asyncio
async def test_scored_response_includes_signal_completeness(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest()],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["signal_completeness"] is not None
    assert 0.0 <= body["signal_completeness"] <= 1.0


@pytest.mark.asyncio
async def test_healthy_zero_drift_signals_completeness_is_full(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest()],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["signal_completeness"] == 1.0
    assert body["state"] == "scored"
    assert body["score"] == 1.0
    assert body["status"] == "healthy"


@pytest.mark.asyncio
async def test_partial_signals_completeness_reflects_available(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(
            score=1.5,
            status="healthy",
            confidence=0.54,
            contributions_json=json.dumps([
                {"signal": "current_variability_drift", "weight": 0.25, "drift": 0.0, "available": True},
                {"signal": "power_factor_drop", "weight": 0.25, "drift": 0.0, "available": True},
                {"signal": "abnormal_power_draw", "weight": 0.20, "drift": 0.1, "available": True},
                {"signal": "phase_imbalance_drift", "weight": 0.15, "drift": 0.0, "available": False},
                {"signal": "trend_worsening", "weight": 0.15, "drift": 0.0, "available": False},
            ]),
        )],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["signal_completeness"] == pytest.approx(3 / 5)


@pytest.mark.asyncio
async def test_insufficient_signals_completeness_from_available_flag(monkeypatch: pytest.MonkeyPatch):
    app, _sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(
            status="insufficient_signals",
            score=None,
            confidence=0.1,
            signal_completeness=3 / 5,
            top_reasons_json=json.dumps(["insufficient_signal_coverage:power_factor_drop,current_variability_drift"]),
            contributions_json=json.dumps([
                {"signal": "current_variability_drift", "weight": 0.25, "drift": 0.0, "available": False},
                {"signal": "power_factor_drop", "weight": 0.25, "drift": 0.0, "available": False},
                {"signal": "abnormal_power_draw", "weight": 0.20, "drift": 0.3, "available": True},
                {"signal": "phase_imbalance_drift", "weight": 0.15, "drift": 0.0, "available": True},
                {"signal": "trend_worsening", "weight": 0.15, "drift": 0.0, "available": True},
            ]),
        )],
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["state"] == "learning"
    assert body["status"] == "insufficient_signals"
    assert body["signal_completeness"] == pytest.approx(3 / 5)


@pytest.mark.asyncio
async def test_trend_contributions_omitted_by_default(monkeypatch: pytest.MonkeyPatch):
    now = datetime.now(timezone.utc)
    history_rows = [
        MachineHealthHistory(
            device_id="DEVICE-A",
            tenant_id="TENANT-A",
            computed_at=now - timedelta(days=i),
            score=2.0 + i * 0.5,
            status="healthy",
            contributions_json=json.dumps([
                {"signal": "current_variability_drift", "weight": 0.25, "drift": 0.5, "available": True},
                {"signal": "power_factor_drop", "weight": 0.25, "drift": 0.0, "available": True},
            ]),
        )
        for i in range(3, 0, -1)
    ]
    app, sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest()],
    )
    async with sf() as session:
        session.add_all(history_rows)
        await session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    for point in body["score_trend"]:
        assert point.get("contributions") is None


@pytest.mark.asyncio
async def test_trend_contributions_included_when_flag_set(monkeypatch: pytest.MonkeyPatch):
    now = datetime.now(timezone.utc)
    contribs = [
        {"signal": "current_variability_drift", "weight": 0.25, "drift": 1.5, "available": True,
         "observed_value": 12.0, "baseline_value": 10.0, "raw_drift": 0.2},
        {"signal": "power_factor_drop", "weight": 0.25, "drift": 0.0, "available": True,
         "observed_value": 0.95, "baseline_value": 0.95, "raw_drift": 0.0},
    ]
    history_rows = [
        MachineHealthHistory(
            device_id="DEVICE-A",
            tenant_id="TENANT-A",
            computed_at=now - timedelta(days=i),
            score=3.0 + i,
            status="watch",
            contributions_json=json.dumps(contribs),
        )
        for i in range(3, 0, -1)
    ]
    app, sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest()],
    )
    async with sf() as session:
        session.add_all(history_rows)
        await session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score?include_trend_contributions=true",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["score_trend"]) == 3
    for point in body["score_trend"]:
        assert point["contributions"] is not None
        assert len(point["contributions"]) == 2
        assert point["contributions"][0]["signal"] == "current_variability_drift"
        assert point["contributions"][0]["drift"] == 1.5
        assert point["contributions"][0]["observed_value"] == 12.0
        assert point["contributions"][1]["signal"] == "power_factor_drop"


@pytest.mark.asyncio
async def test_score_trend_uses_latest_168_points_within_7_day_window(monkeypatch: pytest.MonkeyPatch):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    history_rows = []
    for i in range(200):
        computed_at = now - timedelta(hours=199 - i)
        history_rows.append(
            MachineHealthHistory(
                device_id="DEVICE-A",
                tenant_id="TENANT-A",
                computed_at=computed_at,
                score=1.0 + i / 1000.0,
                status="healthy",
            )
        )

    app, sf, engine = await _build_degradation_app(
        monkeypatch,
        seed_latest=[_scored_latest(computed_at=now)],
    )
    async with sf() as session:
        session.add_all(history_rows)
        await session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.get(
                "/api/v1/devices/DEVICE-A/degradation-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["score_trend"]) == 168

    first_expected = (now - timedelta(hours=167)).isoformat().replace("+00:00", "Z")
    last_expected = now.isoformat().replace("+00:00", "Z")
    assert body["score_trend"][0]["computed_at"] == first_expected
    assert body["score_trend"][-1]["computed_at"] == last_expected
