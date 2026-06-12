from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = PROJECT_ROOT / "services"
RULE_ENGINE_DIR = SERVICES_DIR / "rule-engine-service"
for path in (PROJECT_ROOT, SERVICES_DIR, RULE_ENGINE_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.models.rule import Rule
from app.schemas.rule import RuleCreate, RuleScope, RuleType, TelemetryPayload
from app.services.evaluator import RuleEvaluator
from app.services.rule import RuleService
from services.shared.tenant_context import TenantContext
from app.database import Base


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
        user_id="tester",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )


def _evaluator() -> RuleEvaluator:
    return RuleEvaluator(SimpleNamespace(), _ctx())


def test_rule_create_requires_duration_minutes_for_continuous_idle_rule() -> None:
    with pytest.raises(ValueError, match="duration_minutes is required for continuous idle duration rules"):
        RuleCreate(
            tenant_id="TENANT-A",
            rule_name="Idle Too Long",
            scope=RuleScope.SELECTED_DEVICES,
            device_ids=["DEVICE-1"],
            rule_type=RuleType.CONTINUOUS_IDLE_DURATION,
            notification_channels=["email"],
        )


def test_rule_create_accepts_duration_minutes_for_continuous_idle_rule() -> None:
    rule = RuleCreate(
        tenant_id="TENANT-A",
        rule_name="Idle Too Long",
        scope=RuleScope.SELECTED_DEVICES,
        device_ids=["DEVICE-1"],
        rule_type=RuleType.CONTINUOUS_IDLE_DURATION,
        duration_minutes=40,
        notification_channels=["email"],
    )

    assert rule.duration_minutes == 40
    assert rule.property is None
    assert rule.time_window_start is None


@pytest.mark.asyncio
async def test_rule_service_persists_duration_minutes_for_continuous_idle_rule(session_factory) -> None:
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="Idle Too Long",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["DEVICE-1"],
                rule_type=RuleType.CONTINUOUS_IDLE_DURATION,
                duration_minutes=40,
                notification_channels=["email"],
            ),
            accessible_device_ids=["DEVICE-1"],
        )

    assert created.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value
    assert created.duration_minutes == 40


def test_continuous_idle_rule_triggers_only_on_post_projection_streak_state() -> None:
    evaluator = _evaluator()
    rule = Rule(
        rule_id="rule-1",
        tenant_id="TENANT-A",
        rule_name="Continuous Idle",
        rule_type=RuleType.CONTINUOUS_IDLE_DURATION.value,
        duration_minutes=40,
        scope="selected_devices",
        device_ids=["DEVICE-1"],
        notification_channels=["email"],
        notification_recipients=[],
        cooldown_mode="interval",
        cooldown_unit="minutes",
        cooldown_minutes=15,
        cooldown_seconds=900,
        status="active",
    )
    telemetry = TelemetryPayload(
        device_id="DEVICE-1",
        timestamp=datetime(2026, 4, 12, 10, 40, 0, tzinfo=timezone.utc),
        projected_load_state="idle",
        idle_streak_duration_sec=40 * 60,
    )

    triggered, actual_value = evaluator._evaluate_continuous_idle_rule(rule, telemetry)

    assert triggered is True
    assert actual_value == pytest.approx(40.0)


def test_continuous_idle_rule_does_not_trigger_when_projection_state_is_non_idle() -> None:
    evaluator = _evaluator()
    rule = Rule(
        rule_id="rule-2",
        tenant_id="TENANT-A",
        rule_name="Continuous Idle",
        rule_type=RuleType.CONTINUOUS_IDLE_DURATION.value,
        duration_minutes=40,
        scope="selected_devices",
        device_ids=["DEVICE-1"],
        notification_channels=["email"],
        notification_recipients=[],
        cooldown_mode="interval",
        cooldown_unit="minutes",
        cooldown_minutes=15,
        cooldown_seconds=900,
        status="active",
    )
    telemetry = TelemetryPayload(
        device_id="DEVICE-1",
        timestamp=datetime(2026, 4, 12, 10, 40, 0, tzinfo=timezone.utc),
        projected_load_state="running",
        idle_streak_duration_sec=50 * 60,
    )

    triggered, actual_value = evaluator._evaluate_continuous_idle_rule(rule, telemetry)

    assert triggered is False
    assert actual_value == pytest.approx(50.0)


def test_time_based_rule_semantics_remain_wall_clock_running_window() -> None:
    evaluator = _evaluator()
    rule = Rule(
        rule_id="rule-3",
        tenant_id="TENANT-A",
        rule_name="Restricted Runtime",
        rule_type=RuleType.TIME_BASED.value,
        time_window_start="20:00",
        time_window_end="06:00",
        scope="selected_devices",
        device_ids=["DEVICE-1"],
        notification_channels=["email"],
        notification_recipients=[],
        cooldown_mode="interval",
        cooldown_unit="minutes",
        cooldown_minutes=15,
        cooldown_seconds=900,
        status="active",
    )
    telemetry = TelemetryPayload(
        device_id="DEVICE-1",
        timestamp=datetime(2026, 4, 12, 21, 0, 0, tzinfo=timezone.utc),
        power=120.0,
        projected_load_state="idle",
        idle_streak_duration_sec=120 * 60,
    )

    triggered, actual_value = evaluator._evaluate_time_based_rule(rule, telemetry)

    assert triggered is True
    assert actual_value == 1.0
