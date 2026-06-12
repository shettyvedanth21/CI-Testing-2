from __future__ import annotations

import os
import sys
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "rule-engine-service"
SERVICES_ROOT = ROOT / "services"
for path in (ROOT, SERVICE_ROOT, SERVICES_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.database import Base
from app.models.rule import Alert, Rule, RuleScope, RuleStatus
from app.queue.notification_queue import InMemoryNotificationQueue
from app.schemas.rule import ConditionOperator, NotificationChannel, RuleUpdate
from app.services.evaluator import RuleEvaluator
from app.services.notification_delivery import NotificationDeliveryAuditService
from app.services.rule import DuplicateRuleError, RuleService
from services.shared.tenant_context import TenantContext
from app.schemas.rule import RuleCreate, TelemetryPayload, CooldownMode


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
        user_id="org-admin-1",
        role="org_admin",
        plant_ids=["P1"],
        is_super_admin=False,
    )


@pytest.mark.asyncio
async def test_rule_update_blocks_duplicate_active_signature(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                Rule(
                    rule_id="rule-1",
                    tenant_id="TENANT-A",
                    rule_name="Duplicate Guard",
                    scope=RuleScope.SELECTED_DEVICES.value,
                    device_ids=["P1"],
                    property="power",
                    condition=">",
                    threshold=5.0,
                    status=RuleStatus.ACTIVE.value,
                    notification_channels=["email"],
                ),
                Rule(
                    rule_id="rule-2",
                    tenant_id="TENANT-A",
                    rule_name="Managed Rule",
                    scope=RuleScope.SELECTED_DEVICES.value,
                    device_ids=["P1"],
                    property="power",
                    condition=">",
                    threshold=9.0,
                    status=RuleStatus.ACTIVE.value,
                    notification_channels=["email"],
                ),
            ]
        )
        await session.commit()

        service = RuleService(session, _ctx())
        with pytest.raises(DuplicateRuleError, match="An identical active rule already exists"):
            await service.update_rule(
                "rule-2",
                RuleUpdate(
                    rule_name="Duplicate Guard",
                    threshold=5.0,
                    condition=ConditionOperator.GREATER_THAN,
                    notification_channels=[NotificationChannel.EMAIL],
                ),
                accessible_device_ids=["P1"],
            )


@pytest.mark.asyncio
async def test_rule_update_is_idempotent_for_same_payload(session_factory):
    async with session_factory() as session:
        session.add(
            Rule(
                rule_id="rule-idempotent",
                tenant_id="TENANT-A",
                rule_name="Idempotent Rule",
                scope=RuleScope.SELECTED_DEVICES.value,
                device_ids=["P1"],
                property="power",
                condition=">",
                threshold=7.5,
                status=RuleStatus.ACTIVE.value,
                notification_channels=["email"],
            )
        )
        await session.commit()

        service = RuleService(session, _ctx())
        updated = await service.update_rule(
            "rule-idempotent",
            RuleUpdate(
                rule_name="Idempotent Rule",
                threshold=7.5,
                condition=ConditionOperator.GREATER_THAN,
                notification_channels=[NotificationChannel.EMAIL],
            ),
            accessible_device_ids=["P1"],
        )

    assert updated is not None
    assert str(updated.rule_id) == "rule-idempotent"
    assert updated.rule_name == "Idempotent Rule"
    assert updated.threshold == 7.5


@pytest.mark.asyncio
async def test_rule_soft_delete_archives_rule_and_hides_it_from_active_listing(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="Soft Delete Rule",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["P1"],
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=6.0,
                notification_channels=[NotificationChannel.EMAIL],
            )
        )

        deleted = await service.delete_rule(created.rule_id, soft=True, accessible_device_ids=["P1"])
        stored = await session.get(Rule, str(created.rule_id))
        visible_rows, total = await service.list_rules(accessible_device_ids=["P1"])

    assert deleted is True
    assert stored is not None
    assert stored.status == RuleStatus.ARCHIVED.value
    assert stored.deleted_at is not None
    assert total == 0
    assert visible_rows == []


@pytest.mark.asyncio
async def test_rule_hard_delete_removes_row_completely(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="Hard Delete Rule",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["P1"],
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=6.5,
                notification_channels=[NotificationChannel.EMAIL],
            )
        )

        deleted = await service.delete_rule(created.rule_id, soft=False, accessible_device_ids=["P1"])
        stored = await session.get(Rule, str(created.rule_id))

    assert deleted is True
    assert stored is None


@pytest.mark.asyncio
async def test_rule_delete_returns_false_when_rule_is_out_of_scope(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="Scoped Delete Rule",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["P2"],
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=8.0,
                notification_channels=[NotificationChannel.EMAIL],
            )
        )

        deleted = await service.delete_rule(created.rule_id, soft=True, accessible_device_ids=["P1"])
        stored = await session.get(Rule, str(created.rule_id))

    assert deleted is False
    assert stored is not None
    assert stored.deleted_at is None
    assert stored.status == RuleStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_failed_delivery_stays_non_billable_in_notification_usage_summary(session_factory):
    async with session_factory() as session:
        service = NotificationDeliveryAuditService(session, _ctx())
        log = await service.create_send_attempt(
            channel="sms",
            raw_recipient="+15551234567",
            provider_name="twilio",
            event_type="threshold_alert",
            rule_id="rule-1",
            device_id="DEV-1",
            attempted_at=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
        )
        accepted = await service.mark_provider_accepted(
            log.id,
            provider_message_id="MSG-FAILED-LATER",
            accepted_at=datetime(2026, 5, 1, 8, 1, tzinfo=timezone.utc),
        )
        accepted_billable_units = accepted.billable_units if accepted is not None else None
        failed = await service.mark_failed_by_message_id(
            tenant_id="TENANT-A",
            provider_message_id="MSG-FAILED-LATER",
            failure_code="provider_rejected",
            failure_message="Carrier rejected message",
            failed_at=datetime(2026, 5, 1, 8, 2, tzinfo=timezone.utc),
        )
        await session.commit()

        summary = await service.summarize_month(
            tenant_id="TENANT-A",
            month="2026-05",
        )

    assert accepted is not None
    assert accepted_billable_units == 1
    assert failed is not None
    assert failed.status == "failed"
    assert failed.billable_units == 0
    assert summary.totals.failed_count == 1
    assert summary.totals.accepted_count == 0
    assert summary.totals.billable_count == 0


@pytest.mark.asyncio
async def test_concurrent_no_repeat_evaluations_emit_single_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_outbox.get_notification_queue",
        lambda: InMemoryNotificationQueue(),
    )
    db_path = tmp_path / "rule-no-repeat-concurrency.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        service = RuleService(session, _ctx())
        rule = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="No Repeat Concurrent Rule",
                description="prove concurrent no-repeat evaluations do not double-trigger",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["P1"],
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=5.0,
                notification_channels=[NotificationChannel.EMAIL],
                cooldown_mode=CooldownMode.NO_REPEAT,
            )
        )

    telemetry = TelemetryPayload(
        device_id="P1",
        timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc),
        schema_version="v1",
        enrichment_status="success",
        power=12.0,
    )

    async def _evaluate_once() -> tuple[int, int, list]:
        async with session_factory() as session:
            evaluator = RuleEvaluator(session, _ctx())
            return await evaluator.evaluate_telemetry(telemetry)

    first, second = await asyncio.gather(_evaluate_once(), _evaluate_once())

    async with session_factory() as session:
        alerts = list((await session.execute(select(Alert).where(Alert.rule_id == str(rule.rule_id)))).scalars().all())
        stored_rule = await session.get(Rule, str(rule.rule_id))

    assert sorted([first[1], second[1]]) == [0, 1]
    assert len(alerts) == 1
    assert stored_rule is not None
    assert stored_rule.triggered_once is True
    await engine.dispose()
