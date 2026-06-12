from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

RULE_SERVICE_ROOT = "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/rule-engine-service"
SERVICES_ROOT = "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services"
if RULE_SERVICE_ROOT not in sys.path:
    sys.path.insert(0, RULE_SERVICE_ROOT)
if SERVICES_ROOT not in sys.path:
    sys.path.insert(1, SERVICES_ROOT)

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.database import Base
from app.models.rule import Alert, NotificationDeliveryLog, NotificationDeliveryStatus, NotificationOutbox, Rule, RuleScope, RuleStatus, RuleType
from app.queue.notification_queue import InMemoryNotificationQueue, NotificationQueueItem
from app.repositories.notification_outbox import NotificationOutboxRepository
from app.services.notification_outbox import NotificationContent, NotificationOutboxService
from app.workers import notification_worker as notification_worker_module
from app.workers.notification_worker import NotificationWorker, recover_stale_attempted_on_startup
from services.shared.tenant_context import TenantContext


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


def _ctx(tenant_id: str = "TENANT-A") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id="test-user",
        role="tenant_admin",
        plant_ids=["PLANT-1"],
        is_super_admin=False,
    )


def _make_rule(*, rule_id: str, tenant_id: str = "TENANT-A", recipients: list[dict], channels: list[str]) -> Rule:
    now = datetime.now(timezone.utc)
    return Rule(
        rule_id=rule_id,
        tenant_id=tenant_id,
        rule_name=f"Rule {rule_id}",
        description=None,
        scope=RuleScope.SELECTED_DEVICES.value,
        property="power",
        condition=">",
        threshold=10.0,
        rule_type=RuleType.THRESHOLD.value,
        status=RuleStatus.ACTIVE.value,
        notification_channels=channels,
        notification_recipients=recipients,
        device_ids=["DEVICE-1"],
        created_at=now,
        updated_at=now,
    )


async def _insert_outbox_row(
    session: AsyncSession,
    *,
    tenant_id: str = "TENANT-A",
    status: str = NotificationDeliveryStatus.QUEUED.value,
    next_attempt_at: datetime | None = None,
    processing_started_at: datetime | None = None,
    worker_id: str | None = None,
    retry_count: int = 0,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> NotificationOutbox:
    na = next_attempt_at or datetime.now(timezone.utc)
    if na.tzinfo is not None:
        na = na.replace(tzinfo=None)
    ps = processing_started_at
    if ps is not None and ps.tzinfo is not None:
        ps = ps.replace(tzinfo=None)
    row = NotificationOutbox(
        tenant_id=tenant_id,
        alert_id=None,
        rule_id=None,
        ledger_log_id=None,
        device_id="DEVICE-1",
        event_type="threshold_alert",
        channel="email",
        provider_name="smtp",
        recipient_raw="ops@example.com",
        recipient_masked="o**@example.com",
        recipient_hash="hash1",
        subject="Test",
        message="Test message",
        payload_json={},
        status=status,
        next_attempt_at=na,
        processing_started_at=ps,
        worker_id=worker_id,
        retry_count=retry_count,
        failure_code=failure_code,
        failure_message=failure_message,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_tenant_pending_cap_rejects_when_saturated(session_factory, monkeypatch):
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_TENANT_MAX_PENDING_NOTIFICATIONS", 3)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_QUEUE_REJECT_THRESHOLD", 10000)
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)

    async with session_factory() as session:
        for i in range(3):
            await _insert_outbox_row(session, status=NotificationDeliveryStatus.QUEUED.value)
        await session.commit()

        rule = _make_rule(rule_id="rule-cap-test", recipients=[{"channel": "email", "value": "ops@example.com"}], channels=["email"])
        session.add(rule)
        alert = Alert(
            tenant_id="TENANT-A",
            rule_id=str(rule.rule_id),
            device_id="DEVICE-1",
            severity="high",
            message="Alert",
            actual_value=25.0,
            threshold_value=10.0,
        )
        session.add(alert)
        await session.flush()

        service = NotificationOutboxService(session, _ctx())
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as excinfo:
            await service.enqueue_alert_notifications(
                rule=rule,
                device_id="DEVICE-1",
                alert_id=str(alert.alert_id),
                content=NotificationContent(subject="Alert", message="Threshold exceeded", alert_context={}),
            )
        assert excinfo.value.status_code == 429
        assert excinfo.value.detail["error"] == "TENANT_PENDING_CAP_EXCEEDED"


@pytest.mark.asyncio
async def test_global_backlog_guard_rejects_when_overloaded(session_factory, monkeypatch):
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_TENANT_MAX_PENDING_NOTIFICATIONS", 10000)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_QUEUE_REJECT_THRESHOLD", 5)
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)

    async with session_factory() as session:
        for i in range(5):
            await _insert_outbox_row(session, status=NotificationDeliveryStatus.QUEUED.value)
        await session.commit()

        rule = _make_rule(rule_id="rule-global-cap", recipients=[{"channel": "email", "value": "ops@example.com"}], channels=["email"])
        session.add(rule)
        alert = Alert(
            tenant_id="TENANT-A",
            rule_id=str(rule.rule_id),
            device_id="DEVICE-1",
            severity="high",
            message="Alert",
            actual_value=25.0,
            threshold_value=10.0,
        )
        session.add(alert)
        await session.flush()

        service = NotificationOutboxService(session, _ctx())
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as excinfo:
            await service.enqueue_alert_notifications(
                rule=rule,
                device_id="DEVICE-1",
                alert_id=str(alert.alert_id),
                content=NotificationContent(subject="Alert", message="Threshold exceeded", alert_context={}),
            )
        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["error"] == "NOTIFICATION_QUEUE_OVERLOADED"


@pytest.mark.asyncio
async def test_admission_allows_when_under_limits(session_factory, monkeypatch):
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_TENANT_MAX_PENDING_NOTIFICATIONS", 100)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_QUEUE_REJECT_THRESHOLD", 1000)
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)

    async with session_factory() as session:
        await _insert_outbox_row(session, status=NotificationDeliveryStatus.QUEUED.value)
        await session.commit()

        rule = _make_rule(rule_id="rule-admit-ok", recipients=[{"channel": "email", "value": "ops@example.com"}], channels=["email"])
        session.add(rule)
        alert = Alert(
            tenant_id="TENANT-A",
            rule_id=str(rule.rule_id),
            device_id="DEVICE-1",
            severity="high",
            message="Alert",
            actual_value=25.0,
            threshold_value=10.0,
        )
        session.add(alert)
        await session.flush()

        service = NotificationOutboxService(session, _ctx())
        await service.enqueue_alert_notifications(
            rule=rule,
            device_id="DEVICE-1",
            alert_id=str(alert.alert_id),
            content=NotificationContent(subject="Alert", message="Threshold exceeded", alert_context={}),
        )
        await session.commit()

        all_outbox = list((await session.execute(select(NotificationOutbox))).scalars().all())
        queued_count = sum(1 for r in all_outbox if r.status == NotificationDeliveryStatus.QUEUED.value)
        assert queued_count == 2


@pytest.mark.asyncio
async def test_claim_outbox_entry_reclaims_stale_attempted_with_null_processing_started(session_factory):
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        row = await _insert_outbox_row(
            session,
            status=NotificationDeliveryStatus.ATTEMPTED.value,
            processing_started_at=None,
            worker_id="old-worker",
        )
        await session.commit()

        claimed = await repo.claim_outbox_entry(
            outbox_id=row.id,
            worker_id="new-worker",
            stale_after=timedelta(seconds=30),
        )
        assert claimed is True

        refreshed = await repo.get_by_outbox_id(row.id)
        assert refreshed is not None
        assert refreshed.status == NotificationDeliveryStatus.ATTEMPTED.value
        assert refreshed.worker_id == "new-worker"


@pytest.mark.asyncio
async def test_claim_outbox_entry_reclaims_stale_attempted_with_expired_processing(session_factory):
    old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        row = await _insert_outbox_row(
            session,
            status=NotificationDeliveryStatus.ATTEMPTED.value,
            processing_started_at=old_time,
            worker_id="old-worker",
        )
        await session.commit()

        claimed = await repo.claim_outbox_entry(
            outbox_id=row.id,
            worker_id="new-worker",
            stale_after=timedelta(seconds=30),
        )
        assert claimed is True

        refreshed = await repo.get_by_outbox_id(row.id)
        assert refreshed is not None
        assert refreshed.worker_id == "new-worker"


@pytest.mark.asyncio
async def test_claim_outbox_entry_refuses_fresh_attempted(session_factory):
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        row = await _insert_outbox_row(
            session,
            status=NotificationDeliveryStatus.ATTEMPTED.value,
            processing_started_at=datetime.now(timezone.utc),
            worker_id="current-worker",
        )
        await session.commit()

        claimed = await repo.claim_outbox_entry(
            outbox_id=row.id,
            worker_id="other-worker",
            stale_after=timedelta(seconds=30),
        )
        assert claimed is False


@pytest.mark.asyncio
async def test_retry_enqueue_before_ack_prevents_silent_loss(session_factory, monkeypatch):
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr(notification_worker_module, "get_notification_queue", lambda: queue)
    monkeypatch.setattr(notification_worker_module, "WorkerSessionLocal", session_factory)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_OUTBOX_MAX_RETRIES", 4)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_BACKOFF_BASE_SECONDS", 1)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_BACKOFF_MAX_SECONDS", 1)
    worker = NotificationWorker(concurrency=1)

    async def _boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.services.notification_executor.NotificationExecutor.execute_outbox_delivery", _boom)

    async with session_factory() as session:
        row = await _insert_outbox_row(session, status=NotificationDeliveryStatus.QUEUED.value)
        await session.commit()
        outbox_id = row.id

    await queue.enqueue(NotificationQueueItem(outbox_id=outbox_id, tenant_id="TENANT-A", channel="email"))
    item = await queue.get()
    assert item is not None
    await worker._process_item(item, slot=0)

    metrics = await queue.metrics()
    assert metrics["queue_depth"] == 1, "Retry should be enqueued before ack, so queue should have 1 item"

    async with session_factory() as session:
        repo = NotificationOutboxRepository(session, _ctx())
        refreshed = await repo.get_by_outbox_id(outbox_id)
        assert refreshed is not None
        assert refreshed.status == NotificationDeliveryStatus.QUEUED.value
        assert refreshed.retry_count == 1


@pytest.mark.asyncio
async def test_enqueue_failure_marks_row_for_recovery(session_factory, monkeypatch):
    queue = InMemoryNotificationQueue()

    async def _failing_enqueue(item):
        raise RuntimeError("Redis connection refused")

    queue.enqueue = _failing_enqueue
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)

    async with session_factory() as session:
        rule = _make_rule(rule_id="rule-enq-fail", recipients=[{"channel": "email", "value": "ops@example.com"}], channels=["email"])
        session.add(rule)
        alert = Alert(
            tenant_id="TENANT-A",
            rule_id=str(rule.rule_id),
            device_id="DEVICE-1",
            severity="high",
            message="Alert",
            actual_value=25.0,
            threshold_value=10.0,
        )
        session.add(alert)
        await session.flush()

        service = NotificationOutboxService(session, _ctx())
        await service.enqueue_alert_notifications(
            rule=rule,
            device_id="DEVICE-1",
            alert_id=str(alert.alert_id),
            content=NotificationContent(subject="Alert", message="Threshold exceeded", alert_context={}),
        )
        await session.commit()

        outbox_rows = list((await session.execute(select(NotificationOutbox))).scalars().all())
        assert len(outbox_rows) == 1
        assert outbox_rows[0].status == NotificationDeliveryStatus.QUEUED.value
        assert outbox_rows[0].failure_code == "ENQUEUE_FAILED"


@pytest.mark.asyncio
async def test_recover_stale_attempted_on_startup_requeues_rows(session_factory, monkeypatch):
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_DELIVERY_TIMEOUT_SECONDS", 30)
    old_time = datetime.now(timezone.utc) - timedelta(seconds=120)

    async with session_factory() as session:
        await _insert_outbox_row(
            session,
            status=NotificationDeliveryStatus.ATTEMPTED.value,
            processing_started_at=old_time,
            worker_id="crashed-worker",
        )
        await _insert_outbox_row(
            session,
            status=NotificationDeliveryStatus.ATTEMPTED.value,
            processing_started_at=None,
            worker_id="crashed-worker-2",
        )
        await _insert_outbox_row(
            session,
            status=NotificationDeliveryStatus.QUEUED.value,
        )
        await _insert_outbox_row(
            session,
            status=NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
        )
        await session.commit()

    monkeypatch.setattr(notification_worker_module, "WorkerSessionLocal", session_factory)
    recovered = await recover_stale_attempted_on_startup()
    assert recovered == 2

    async with session_factory() as session:
        all_rows = list((await session.execute(select(NotificationOutbox))).scalars().all())
        attempted_remaining = [r for r in all_rows if r.status == NotificationDeliveryStatus.ATTEMPTED.value]
        assert len(attempted_remaining) == 0

        recovered_rows = [r for r in all_rows if r.failure_code == "STALE_ATTEMPT_RECOVERED"]
        assert len(recovered_rows) == 2
        for r in recovered_rows:
            assert r.status == NotificationDeliveryStatus.QUEUED.value
            assert r.worker_id is None
            assert r.processing_started_at is None


@pytest.mark.asyncio
async def test_tenant_fair_requeue_interleaves_across_tenants(session_factory):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        for i in range(10):
            await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.QUEUED.value, next_attempt_at=now)
        for i in range(2):
            await _insert_outbox_row(session, tenant_id="TENANT-B", status=NotificationDeliveryStatus.QUEUED.value, next_attempt_at=now)
        await session.commit()

        due_rows = await repo.list_due_queued(limit=6, now=now)
        assert len(due_rows) == 6

        tenant_ids = [row.tenant_id for row in due_rows]
        b_count = tenant_ids.count("TENANT-B")
        a_count = tenant_ids.count("TENANT-A")
        assert b_count == 2, f"TENANT-B should get both its rows in first 6, got {b_count}"
        assert a_count == 4, f"TENANT-A should get 4 of 10 rows in first 6, got {a_count}"

        assert tenant_ids[0] != tenant_ids[1] or tenant_ids[1] != tenant_ids[2], \
            "First 3 rows must not all be from the same tenant — round-robin must interleave"

        b_positions = [i for i, t in enumerate(tenant_ids) if t == "TENANT-B"]
        assert b_positions[0] <= 1, "TENANT-B must appear in the first 2 positions (round-robin depth 0 or 1)"
        assert b_positions[1] <= 3, "TENANT-B second row must appear by position 3 (depth 1)"


@pytest.mark.asyncio
async def test_count_pending_for_tenant_counts_queued_and_attempted(session_factory):
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.QUEUED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.ATTEMPTED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.PROVIDER_ACCEPTED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-B", status=NotificationDeliveryStatus.QUEUED.value)
        await session.commit()

        count_a = await repo.count_pending_for_tenant("TENANT-A")
        count_b = await repo.count_pending_for_tenant("TENANT-B")
        assert count_a == 2
        assert count_b == 1


@pytest.mark.asyncio
async def test_count_pending_global_counts_all_tenants(session_factory):
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.QUEUED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-B", status=NotificationDeliveryStatus.ATTEMPTED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.FAILED.value)
        await session.commit()

        global_count = await repo.count_pending_global()
        assert global_count == 2


@pytest.mark.asyncio
async def test_tenant_cap_does_not_block_other_tenants(session_factory, monkeypatch):
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_TENANT_MAX_PENDING_NOTIFICATIONS", 2)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_QUEUE_REJECT_THRESHOLD", 10000)
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)

    async with session_factory() as session:
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.QUEUED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.QUEUED.value)
        await session.commit()

        rule_b = _make_rule(rule_id="rule-b", tenant_id="TENANT-B", recipients=[{"channel": "email", "value": "ops-b@example.com"}], channels=["email"])
        session.add(rule_b)
        alert_b = Alert(
            tenant_id="TENANT-B",
            rule_id=str(rule_b.rule_id),
            device_id="DEVICE-1",
            severity="high",
            message="Alert",
            actual_value=25.0,
            threshold_value=10.0,
        )
        session.add(alert_b)
        await session.flush()

        ctx_b = _ctx(tenant_id="TENANT-B")
        service = NotificationOutboxService(session, ctx_b)
        await service.enqueue_alert_notifications(
            rule=rule_b,
            device_id="DEVICE-1",
            alert_id=str(alert_b.alert_id),
            content=NotificationContent(subject="Alert", message="Threshold exceeded", alert_context={}),
        )
        await session.commit()

        outbox_b = list(
            (await session.execute(select(NotificationOutbox).where(NotificationOutbox.tenant_id == "TENANT-B"))).scalars().all()
        )
        queued_b = [r for r in outbox_b if r.status == NotificationDeliveryStatus.QUEUED.value]
        assert len(queued_b) == 1


@pytest.mark.asyncio
async def test_terminal_statuses_not_counted_as_pending(session_factory):
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.FAILED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.PROVIDER_ACCEPTED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.SKIPPED.value)
        await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.DELIVERED.value)
        await session.commit()

        count = await repo.count_pending_for_tenant("TENANT-A")
        global_count = await repo.count_pending_global()
        assert count == 0
        assert global_count == 0


@pytest.mark.asyncio
async def test_tenant_fair_requeue_with_three_tenants(session_factory):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        for i in range(8):
            await _insert_outbox_row(session, tenant_id="TENANT-A", status=NotificationDeliveryStatus.QUEUED.value, next_attempt_at=now)
        for i in range(4):
            await _insert_outbox_row(session, tenant_id="TENANT-B", status=NotificationDeliveryStatus.QUEUED.value, next_attempt_at=now)
        for i in range(1):
            await _insert_outbox_row(session, tenant_id="TENANT-C", status=NotificationDeliveryStatus.QUEUED.value, next_attempt_at=now)
        await session.commit()

        due_rows = await repo.list_due_queued(limit=9, now=now)
        assert len(due_rows) == 9

        tenant_ids = [row.tenant_id for row in due_rows]
        a_count = tenant_ids.count("TENANT-A")
        b_count = tenant_ids.count("TENANT-B")
        c_count = tenant_ids.count("TENANT-C")

        assert c_count == 1, f"TENANT-C must get its only row in first 9, got {c_count}"
        assert b_count >= 3, f"TENANT-B must get at least 3 rows in first 9, got {b_count}"

        c_position = tenant_ids.index("TENANT-C")
        assert c_position <= 2, f"TENANT-C must appear in one of the first 3 positions (depth 0), got position {c_position}"

        first_three = set(tenant_ids[:3])
        assert len(first_three) == 3, "First 3 rows must all be from different tenants (depth 0 round-robin)"
