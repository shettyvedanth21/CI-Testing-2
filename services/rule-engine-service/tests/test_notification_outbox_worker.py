from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

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
from app.notifications.adapter import NotificationAdapter
from app.queue.notification_queue import InMemoryNotificationQueue, NotificationQueueItem
from app.services.notification_delivery import NotificationDeliveryAuditService
from app.services.notification_executor import NotificationExecutor
from app.services.notification_outbox import NotificationContent, NotificationOutboxService
from app.workers import notification_worker as notification_worker_module
from app.workers.notification_worker import NotificationWorker
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


def _make_rule(*, rule_id: str, recipients: list[dict], channels: list[str]) -> Rule:
    now = datetime.now(timezone.utc)
    return Rule(
        rule_id=rule_id,
        tenant_id="TENANT-A",
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


class _FakeSMTP:
    sent_messages: list[dict] = []

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self, context=None):
        return None

    def ehlo(self):
        return None

    def login(self, username, password):
        self.username = username
        self.password = password

    def sendmail(self, from_address, recipients, message):
        self.sent_messages.append({"from": from_address, "recipients": list(recipients), "message": message})
        return {}


async def _all_logs(session: AsyncSession) -> list[NotificationDeliveryLog]:
    result = await session.execute(select(NotificationDeliveryLog).order_by(NotificationDeliveryLog.attempted_at.asc()))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_outbox_service_creates_durable_intents_and_queued_ledger(session_factory, monkeypatch):
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)

    async with session_factory() as session:
        rule = _make_rule(
            rule_id="rule-outbox-1",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
        )
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
            content=NotificationContent(
                subject="Alert",
                message="Threshold exceeded",
                alert_context={"device_name": "Device 1"},
            ),
        )
        await session.commit()

        outbox_rows = list((await session.execute(select(NotificationOutbox))).scalars().all())
        logs = await _all_logs(session)

    assert len(outbox_rows) == 1
    assert outbox_rows[0].status == NotificationDeliveryStatus.QUEUED.value
    assert len(logs) == 1
    assert logs[0].status == NotificationDeliveryStatus.QUEUED.value
    queued_item = await queue.get()
    assert queued_item is not None
    assert queued_item.outbox_id == outbox_rows[0].id


@pytest.mark.asyncio
async def test_worker_processes_outbox_and_updates_existing_ledger(session_factory, monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)
    monkeypatch.setattr(notification_worker_module, "get_notification_queue", lambda: queue)
    monkeypatch.setattr(notification_worker_module, "WorkerSessionLocal", session_factory)
    worker = NotificationWorker(concurrency=1)

    async with session_factory() as session:
        rule = _make_rule(
            rule_id="rule-worker-email",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
        )
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
        email_adapter = service._adapter._adapters["email"]
        email_adapter._enabled = True
        email_adapter._smtp_host = "smtp.example.com"
        email_adapter._smtp_port = 587
        email_adapter._smtp_username = "user"
        email_adapter._smtp_password = "pass"
        email_adapter._from_address = "alerts@example.com"
        await service.enqueue_alert_notifications(
            rule=rule,
            device_id="DEVICE-1",
            alert_id=str(alert.alert_id),
            content=NotificationContent(
                subject="Alert",
                message="Threshold exceeded",
                alert_context={"device_name": "Device 1", "device_id": "DEVICE-1"},
            ),
        )
        await session.commit()
        outbox_row = (await session.execute(select(NotificationOutbox))).scalar_one()

    async with session_factory() as session:
        adapter = NotificationAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        email_adapter = adapter._adapters["email"]
        email_adapter._enabled = True
        email_adapter._smtp_host = "smtp.example.com"
        email_adapter._smtp_port = 587
        email_adapter._smtp_username = "user"
        email_adapter._smtp_password = "pass"
        email_adapter._from_address = "alerts@example.com"

    monkeypatch.setattr("app.services.notification_executor.NotificationAdapter", lambda audit_service=None: adapter)
    await queue.enqueue(NotificationQueueItem(outbox_id=outbox_row.id, tenant_id="TENANT-A", channel="email"))
    item = await queue.get()
    assert item is not None
    await worker._process_item(item, slot=0)

    async with session_factory() as session:
        logs = await _all_logs(session)
        refreshed_outbox = (await session.execute(select(NotificationOutbox))).scalar_one()

    assert len(logs) == 1
    assert logs[0].status == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value
    assert refreshed_outbox.status == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value
    assert len(_FakeSMTP.sent_messages) == 1


@pytest.mark.asyncio
async def test_no_recipient_case_records_skipped_without_success(session_factory, monkeypatch):
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)

    async with session_factory() as session:
        rule = _make_rule(rule_id="rule-no-recips", recipients=[], channels=["sms"])
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

        logs = await _all_logs(session)
        outbox_rows = list((await session.execute(select(NotificationOutbox))).scalars().all())

    assert len(logs) == 1
    assert logs[0].status == NotificationDeliveryStatus.SKIPPED.value
    assert logs[0].failure_code == "NO_ACTIVE_RECIPIENTS"
    assert len(outbox_rows) == 1
    assert outbox_rows[0].status == NotificationDeliveryStatus.SKIPPED.value
    assert (await queue.metrics())["queue_depth"] == 0


@pytest.mark.asyncio
async def test_worker_retries_without_creating_duplicate_ledger_rows(session_factory, monkeypatch):
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)
    monkeypatch.setattr(notification_worker_module, "get_notification_queue", lambda: queue)
    monkeypatch.setattr(notification_worker_module, "WorkerSessionLocal", session_factory)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_OUTBOX_MAX_RETRIES", 2)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_BACKOFF_BASE_SECONDS", 1)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_BACKOFF_MAX_SECONDS", 1)
    worker = NotificationWorker(concurrency=1)

    async def _boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.services.notification_executor.NotificationExecutor.execute_outbox_delivery", _boom)

    async with session_factory() as session:
        rule = _make_rule(
            rule_id="rule-retry",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
        )
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
        outbox_row = (await session.execute(select(NotificationOutbox))).scalar_one()

    await queue.enqueue(NotificationQueueItem(outbox_id=outbox_row.id, tenant_id="TENANT-A", channel="email"))
    first_item = await queue.get()
    assert first_item is not None
    await worker._process_item(first_item, slot=0)

    async with session_factory() as session:
        logs = await _all_logs(session)
        refreshed_outbox = (await session.execute(select(NotificationOutbox))).scalar_one()

    assert len(logs) == 1
    assert logs[0].status == NotificationDeliveryStatus.ATTEMPTED.value
    assert refreshed_outbox.status == NotificationDeliveryStatus.QUEUED.value
    assert refreshed_outbox.retry_count == 1


@pytest.mark.asyncio
async def test_worker_dead_letters_after_retry_budget(session_factory, monkeypatch):
    queue = InMemoryNotificationQueue()
    monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)
    monkeypatch.setattr(notification_worker_module, "get_notification_queue", lambda: queue)
    monkeypatch.setattr(notification_worker_module, "WorkerSessionLocal", session_factory)
    monkeypatch.setattr(notification_worker_module.settings, "NOTIFICATION_OUTBOX_MAX_RETRIES", 1)
    worker = NotificationWorker(concurrency=1)

    async def _boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.services.notification_executor.NotificationExecutor.execute_outbox_delivery", _boom)

    async with session_factory() as session:
        rule = _make_rule(
            rule_id="rule-dlq",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
        )
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
        outbox_row = (await session.execute(select(NotificationOutbox))).scalar_one()

    await queue.enqueue(NotificationQueueItem(outbox_id=outbox_row.id, tenant_id="TENANT-A", channel="email"))
    item = await queue.get()
    assert item is not None
    await worker._process_item(item, slot=0)

    async with session_factory() as session:
        logs = await _all_logs(session)
        refreshed_outbox = (await session.execute(select(NotificationOutbox))).scalar_one()

    assert len(logs) == 1
    assert logs[0].status == NotificationDeliveryStatus.FAILED.value
    assert refreshed_outbox.status == NotificationDeliveryStatus.FAILED.value
    assert refreshed_outbox.dead_lettered_at is not None
