from __future__ import annotations

import csv
import io
import os
import sys
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

RULE_SERVICE_ROOT = "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/rule-engine-service"
SERVICES_ROOT = "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services"
if RULE_SERVICE_ROOT not in sys.path:
    sys.path.insert(0, RULE_SERVICE_ROOT)
if SERVICES_ROOT not in sys.path:
    sys.path.insert(1, SERVICES_ROOT)

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.database import Base
from app.models.rule import (
    Alert,
    NotificationChannelSetting,
    NotificationDeliveryLog,
    NotificationDeliveryStatus,
    NotificationOutbox,
    Rule,
    RuleTriggerState,
    RuleScope,
    RuleStatus,
    RuleType,
)
from app.notifications.adapter import NotificationAdapter
from app.queue.notification_queue import InMemoryNotificationQueue, NotificationQueueItem
from app.repositories.rule import RuleRepository
from app.schemas.rule import TelemetryPayload
from app.services.evaluator import RuleEvaluator
from app.services.notification_delivery import NotificationDeliveryAuditService
from app.services.notification_outbox import NotificationOutboxService
from app.services import notification_delivery as notification_delivery_module
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


def _super_admin_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=None,
        user_id="super-admin",
        role="super_admin",
        plant_ids=[],
        is_super_admin=True,
    )


def _make_rule(
    *,
    rule_id: str,
    tenant_id: str = "TENANT-A",
    recipients: list[dict],
    channels: list[str],
    device_ids: list[str] | None = None,
    cooldown_mode: str = "interval",
    cooldown_seconds: int = 900,
) -> Rule:
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
        cooldown_mode=cooldown_mode,
        cooldown_seconds=cooldown_seconds,
        notification_channels=channels,
        notification_recipients=recipients,
        device_ids=device_ids or ["DEVICE-1"],
        created_at=now,
        updated_at=now,
    )


class _FakeSMTP:
    sent_messages: list[dict] = []
    refusal_map: dict[str, tuple[int, bytes]] = {}
    raise_error: Exception | None = None

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
        if self.raise_error is not None:
            raise self.raise_error
        self.sent_messages.append({"from": from_address, "recipients": list(recipients), "message": message})
        return {recipient: self.refusal_map[recipient] for recipient in recipients if recipient in self.refusal_map}


class _FakeResponse:
    def __init__(self, status_code: int = 201, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeAsyncClient:
    requests: list[dict] = []
    responses: list[_FakeResponse] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.is_closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, headers=None):
        self.requests.append(
            {
                "url": url,
                "data": dict(data or {}),
                "headers": dict(headers or {}),
                "auth": self.kwargs.get("auth"),
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected extra HTTP call")
        return self.responses.pop(0)


async def _all_logs(session: AsyncSession) -> list[NotificationDeliveryLog]:
    result = await session.execute(select(NotificationDeliveryLog).order_by(NotificationDeliveryLog.attempted_at.asc()))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_one_email_recipient_creates_one_audit_row(session_factory, monkeypatch):
    _FakeSMTP.sent_messages = []
    _FakeSMTP.refusal_map = {}
    _FakeSMTP.raise_error = None
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    async with session_factory() as session:
        adapter = NotificationAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        email_adapter = adapter._adapters["email"]
        email_adapter._enabled = True
        email_adapter._smtp_host = "smtp.example.com"
        email_adapter._smtp_port = 587
        email_adapter._smtp_username = "user"
        email_adapter._smtp_password = "pass"
        email_adapter._from_address = "alerts@example.com"

        rule = _make_rule(
            rule_id="rule-email",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
        )

        sent = await adapter.send_alert(
            channel="email",
            subject="Alert",
            message="Threshold exceeded",
            rule=rule,
            device_id="DEVICE-1",
            alert_type="threshold_alert",
        )
        await session.commit()

        rows = await _all_logs(session)

    assert sent is True
    assert len(rows) == 1
    assert rows[0].tenant_id == "TENANT-A"
    assert rows[0].channel == "email"
    assert rows[0].status == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value
    assert rows[0].recipient_masked != "ops@example.com"
    assert rows[0].recipient_hash
    assert rows[0].billable_units == 1


@pytest.mark.asyncio
async def test_two_sms_recipients_create_two_audit_rows(session_factory, monkeypatch):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.responses = [
        _FakeResponse(201, {"sid": "SM-1"}),
        _FakeResponse(201, {"sid": "SM-2"}),
    ]
    _fake_twilio_client = _FakeAsyncClient()
    monkeypatch.setattr("app.shared_http.get_twilio_http_client", lambda: _fake_twilio_client)
    monkeypatch.setattr("app.notifications.adapter.get_twilio_http_client", lambda: _fake_twilio_client)

    async with session_factory() as session:
        adapter = NotificationAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        sms_adapter = adapter._adapters["sms"]
        sms_adapter._enabled = True
        sms_adapter._account_sid = "AC123"
        sms_adapter._auth_token = "secret"
        sms_adapter._from_number = "+15550000000"

        rule = _make_rule(
            rule_id="rule-sms",
            recipients=[
                {"channel": "sms", "value": "+1 (555) 123-4567"},
                {"channel": "sms", "value": "+1 (555) 234-5678"},
            ],
            channels=["sms"],
        )

        sent = await adapter.send_alert(
            channel="sms",
            subject="SMS alert",
            message="Threshold exceeded",
            rule=rule,
            device_id="DEVICE-1",
        )
        await session.commit()
        rows = await _all_logs(session)

    assert sent is True
    assert len(rows) == 2
    assert {row.provider_message_id for row in rows} == {"SM-1", "SM-2"}
    assert all(row.status == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value for row in rows)


@pytest.mark.asyncio
async def test_whatsapp_success_and_failure_are_captured(session_factory, monkeypatch):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.responses = [
        _FakeResponse(201, {"sid": "WA-1"}),
        _FakeResponse(400, {"code": 63016, "message": "Template rejected"}, text="Template rejected"),
    ]
    _fake_twilio_client = _FakeAsyncClient()
    monkeypatch.setattr("app.shared_http.get_twilio_http_client", lambda: _fake_twilio_client)
    monkeypatch.setattr("app.notifications.adapter.get_twilio_http_client", lambda: _fake_twilio_client)

    async with session_factory() as session:
        adapter = NotificationAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        whatsapp_adapter = adapter._adapters["whatsapp"]
        whatsapp_adapter._enabled = True
        whatsapp_adapter._account_sid = "AC456"
        whatsapp_adapter._auth_token = "secret"
        whatsapp_adapter._from_number = "+15550000001"

        rule = _make_rule(
            rule_id="rule-whatsapp",
            recipients=[
                {"channel": "whatsapp", "value": "+1 (555) 987-6543"},
                {"channel": "whatsapp", "value": "+1 (555) 876-5432"},
            ],
            channels=["whatsapp"],
        )

        sent = await adapter.send_alert(
            channel="whatsapp",
            subject="WhatsApp alert",
            message="Threshold exceeded",
            rule=rule,
            device_id="DEVICE-1",
        )
        await session.commit()
        rows = await _all_logs(session)

    assert sent is False
    assert len(rows) == 2
    assert sorted((row.status, row.provider_message_id, row.failure_code) for row in rows) == [
        (NotificationDeliveryStatus.FAILED.value, None, "63016"),
        (NotificationDeliveryStatus.PROVIDER_ACCEPTED.value, "WA-1", None),
    ]


@pytest.mark.asyncio
async def test_failed_sends_persist_failure_status_and_reason(session_factory, monkeypatch):
    _FakeSMTP.sent_messages = []
    _FakeSMTP.refusal_map = {}
    _FakeSMTP.raise_error = RuntimeError("smtp down")
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    async with session_factory() as session:
        adapter = NotificationAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        email_adapter = adapter._adapters["email"]
        email_adapter._enabled = True
        email_adapter._smtp_host = "smtp.example.com"
        email_adapter._smtp_port = 587
        email_adapter._smtp_username = "user"
        email_adapter._smtp_password = "pass"
        email_adapter._from_address = "alerts@example.com"

        rule = _make_rule(
            rule_id="rule-email-failed",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
        )

        sent = await adapter.send_alert(
            channel="email",
            subject="Alert",
            message="Threshold exceeded",
            rule=rule,
            device_id="DEVICE-1",
        )
        await session.commit()
        rows = await _all_logs(session)

    assert sent is False
    assert len(rows) == 1
    assert rows[0].status == NotificationDeliveryStatus.FAILED.value
    assert rows[0].failure_code == "RuntimeError"
    assert rows[0].failure_message == "smtp down"


@pytest.mark.asyncio
async def test_tenant_scoping_is_preserved(session_factory):
    async with session_factory() as session:
        tenant_a = NotificationDeliveryAuditService(session, _ctx("TENANT-A"))
        tenant_b = NotificationDeliveryAuditService(session, _ctx("TENANT-B"))

        row_a = await tenant_a.create_send_attempt(
            channel="email",
            raw_recipient="ops-a@example.com",
            provider_name="smtp",
            event_type="threshold_alert",
            rule_id="rule-a",
            device_id="DEVICE-A",
        )
        await tenant_a.mark_provider_accepted(row_a.id, provider_message_id="EMAIL-A")

        row_b = await tenant_b.create_send_attempt(
            channel="sms",
            raw_recipient="+15550123456",
            provider_name="twilio",
            event_type="threshold_alert",
            rule_id="rule-b",
            device_id="DEVICE-B",
        )
        await tenant_b.mark_failed(row_b.id, failure_code="400", failure_message="Rejected")
        await session.commit()

        tenant_a_rows = await tenant_a.list_for_month(year=datetime.now(timezone.utc).year, month=datetime.now(timezone.utc).month)
        tenant_b_rows = await tenant_b.list_for_month(year=datetime.now(timezone.utc).year, month=datetime.now(timezone.utc).month)

    assert [row.tenant_id for row in tenant_a_rows] == ["TENANT-A"]
    assert [row.tenant_id for row in tenant_b_rows] == ["TENANT-B"]


@pytest.mark.asyncio
async def test_monthly_summary_query_returns_grouped_counts(session_factory):
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        service = NotificationDeliveryAuditService(session, _ctx())

        email_row = await service.create_send_attempt(
            channel="email",
            raw_recipient="ops@example.com",
            provider_name="smtp",
            event_type="threshold_alert",
            rule_id="rule-email",
            device_id="DEVICE-1",
            attempted_at=now,
        )
        await service.mark_provider_accepted(email_row.id, provider_message_id="EMAIL-1", accepted_at=now)

        sms_row = await service.create_send_attempt(
            channel="sms",
            raw_recipient="+15550123456",
            provider_name="twilio",
            event_type="threshold_alert",
            rule_id="rule-sms",
            device_id="DEVICE-1",
            attempted_at=now,
        )
        await service.mark_failed(sms_row.id, failure_code="300", failure_message="Carrier rejected", failed_at=now)
        await session.commit()

        summary = await service.summarize_for_month(year=now.year, month=now.month)

    assert [(row.channel, row.attempted_count, row.accepted_count, row.failed_count, row.billable_count) for row in summary] == [
        ("email", 1, 1, 0, 1),
        ("sms", 1, 0, 1, 0),
    ]


@pytest.mark.asyncio
async def test_email_uses_tenant_notification_settings_and_counts_stay_billable_safe(session_factory, monkeypatch):
    _FakeSMTP.sent_messages = []
    _FakeSMTP.refusal_map = {}
    _FakeSMTP.raise_error = None
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    async with session_factory() as session:
        session.add_all(
            [
                NotificationChannelSetting(tenant_id="TENANT-A", channel_type="email", value="ops@example.com", is_active=True),
                NotificationChannelSetting(tenant_id="TENANT-A", channel_type="email", value="guard@example.com", is_active=True),
            ]
        )
        await session.commit()

        adapter = NotificationAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        email_adapter = adapter._adapters["email"]
        email_adapter._enabled = True
        email_adapter._smtp_host = "smtp.example.com"
        email_adapter._smtp_port = 587
        email_adapter._smtp_username = "user"
        email_adapter._smtp_password = "pass"
        email_adapter._from_address = "alerts@example.com"

        rule = _make_rule(
            rule_id="rule-settings-email",
            recipients=[],
            channels=["email"],
        )

        sent = await adapter.send_alert(
            channel="email",
            subject="Alert",
            message="Threshold exceeded",
            rule=rule,
            device_id="DEVICE-1",
        )
        await session.commit()

        rows = await _all_logs(session)
        summary = await NotificationDeliveryAuditService(session, _ctx()).summarize_for_month(
            year=datetime.now(timezone.utc).year,
            month=datetime.now(timezone.utc).month,
        )

    assert sent is True
    assert len(rows) == 2
    assert all(row.status == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value for row in rows)
    assert [(row.channel, row.attempted_count, row.accepted_count, row.failed_count, row.skipped_count, row.billable_count) for row in summary] == [
        ("email", 2, 2, 0, 0, 2),
    ]


@pytest.mark.asyncio
async def test_no_recipient_dispatch_is_recorded_as_skipped_and_not_success(session_factory, monkeypatch):
    _FakeSMTP.sent_messages = []
    _FakeSMTP.refusal_map = {}
    _FakeSMTP.raise_error = None
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    async with session_factory() as session:
        adapter = NotificationAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        email_adapter = adapter._adapters["email"]
        email_adapter._enabled = True
        email_adapter._smtp_host = "smtp.example.com"
        email_adapter._smtp_port = 587
        email_adapter._smtp_username = "user"
        email_adapter._smtp_password = "pass"
        email_adapter._from_address = "alerts@example.com"

        rule = _make_rule(
            rule_id="rule-no-recipients",
            recipients=[],
            channels=["email"],
        )

        sent = await adapter.send_alert(
            channel="email",
            subject="Alert",
            message="Threshold exceeded",
            rule=rule,
            device_id="DEVICE-1",
        )
        await session.commit()

        rows = await _all_logs(session)
        summary = await NotificationDeliveryAuditService(session, _ctx()).summarize_for_month(
            year=datetime.now(timezone.utc).year,
            month=datetime.now(timezone.utc).month,
        )

    assert sent is False
    assert _FakeSMTP.sent_messages == []
    assert len(rows) == 1
    assert rows[0].status == NotificationDeliveryStatus.SKIPPED.value
    assert rows[0].failure_code == "NO_ACTIVE_RECIPIENTS"
    assert rows[0].failure_message == "No active recipients configured for email channel."
    assert rows[0].recipient_masked == ""
    assert [(row.channel, row.attempted_count, row.accepted_count, row.failed_count, row.skipped_count, row.billable_count) for row in summary] == [
        ("email", 1, 0, 0, 1, 0),
    ]


@pytest.mark.asyncio
async def test_rule_evaluation_enqueues_notification_intents_without_inline_provider_send(session_factory, monkeypatch):
    _FakeSMTP.sent_messages = []
    _FakeSMTP.refusal_map = {}
    _FakeSMTP.raise_error = None
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    async with session_factory() as session:
        rule = _make_rule(
            rule_id="11111111-1111-1111-1111-111111111111",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
        )
        session.add(rule)
        await session.flush()

        queue = InMemoryNotificationQueue()
        monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: queue)
        evaluator = RuleEvaluator(session, _ctx())

        total, triggered, _ = await evaluator.evaluate_telemetry(
            TelemetryPayload(
                device_id="DEVICE-1",
                timestamp=datetime.now(timezone.utc),
                power=25.0,
            )
        )

        alerts = list((await session.execute(select(Alert))).scalars().all())
        logs = await _all_logs(session)
        outbox_rows = list((await session.execute(select(NotificationOutbox))).scalars().all())

    assert total == 1
    assert triggered == 1
    assert len(alerts) == 1
    assert len(logs) == 1
    assert len(outbox_rows) == 1
    assert logs[0].alert_id == alerts[0].alert_id
    assert logs[0].status == NotificationDeliveryStatus.QUEUED.value
    assert _FakeSMTP.sent_messages == []
    queued_item = await queue.get()
    assert queued_item is not None
    assert queued_item.outbox_id == outbox_rows[0].id


@pytest.mark.asyncio
async def test_multi_device_rule_cooldown_is_enforced_per_device(session_factory, monkeypatch):
    async with session_factory() as session:
        rule = _make_rule(
            rule_id="22222222-2222-2222-2222-222222222222",
            recipients=[
                {"channel": "email", "value": "ops1@example.com"},
                {"channel": "email", "value": "ops2@example.com"},
            ],
            channels=["email"],
            device_ids=["DEVICE-1", "DEVICE-2"],
            cooldown_seconds=300,
        )
        session.add(rule)
        await session.flush()

        monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: InMemoryNotificationQueue())
        evaluator = RuleEvaluator(session, _ctx())

        first_total, first_triggered, _ = await evaluator.evaluate_telemetry(
            TelemetryPayload(
                device_id="DEVICE-1",
                timestamp=datetime.now(timezone.utc),
                power=25.0,
            )
        )
        second_total, second_triggered, _ = await evaluator.evaluate_telemetry(
            TelemetryPayload(
                device_id="DEVICE-2",
                timestamp=datetime.now(timezone.utc),
                power=25.0,
            )
        )

        alerts = list((await session.execute(select(Alert).order_by(Alert.device_id.asc()))).scalars().all())
        logs = await _all_logs(session)
        trigger_states = list(
            (
                await session.execute(
                    select(RuleTriggerState).order_by(RuleTriggerState.device_id.asc())
                )
            ).scalars().all()
        )

    assert first_total == 1
    assert second_total == 1
    assert first_triggered == 1
    assert second_triggered == 1
    assert [alert.device_id for alert in alerts] == ["DEVICE-1", "DEVICE-2"]
    assert len(logs) == 4
    assert sorted({log.device_id for log in logs}) == ["DEVICE-1", "DEVICE-2"]
    assert sorted((state.device_id, state.triggered_once) for state in trigger_states) == [
        ("DEVICE-1", False),
        ("DEVICE-2", False),
    ]


@pytest.mark.asyncio
async def test_multi_device_no_repeat_is_isolated_per_device(session_factory, monkeypatch):
    async with session_factory() as session:
        rule = _make_rule(
            rule_id="33333333-3333-3333-3333-333333333333",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
            device_ids=["DEVICE-1", "DEVICE-2"],
            cooldown_mode="no_repeat",
            cooldown_seconds=0,
        )
        session.add(rule)
        await session.flush()

        monkeypatch.setattr("app.services.notification_outbox.get_notification_queue", lambda: InMemoryNotificationQueue())
        evaluator = RuleEvaluator(session, _ctx())

        _, triggered_device_1_first, _ = await evaluator.evaluate_telemetry(
            TelemetryPayload(device_id="DEVICE-1", timestamp=datetime.now(timezone.utc), power=25.0)
        )
        _, triggered_device_1_second, _ = await evaluator.evaluate_telemetry(
            TelemetryPayload(device_id="DEVICE-1", timestamp=datetime.now(timezone.utc), power=25.0)
        )
        _, triggered_device_2_first, _ = await evaluator.evaluate_telemetry(
            TelemetryPayload(device_id="DEVICE-2", timestamp=datetime.now(timezone.utc), power=25.0)
        )

        alerts = list((await session.execute(select(Alert).order_by(Alert.device_id.asc()))).scalars().all())
        trigger_states = list(
            (
                await session.execute(
                    select(RuleTriggerState).order_by(RuleTriggerState.device_id.asc())
                )
            ).scalars().all()
        )

    assert triggered_device_1_first == 1
    assert triggered_device_1_second == 0
    assert triggered_device_2_first == 1
    assert [alert.device_id for alert in alerts] == ["DEVICE-1", "DEVICE-2"]
    assert sorted((state.device_id, state.triggered_once) for state in trigger_states) == [
        ("DEVICE-1", True),
        ("DEVICE-2", True),
    ]


@pytest.mark.asyncio
async def test_per_device_cooldown_handles_naive_db_timestamps(session_factory):
    async with session_factory() as session:
        rule = _make_rule(
            rule_id="44444444-4444-4444-4444-444444444444",
            recipients=[{"channel": "email", "value": "ops@example.com"}],
            channels=["email"],
            device_ids=["DEVICE-1"],
            cooldown_seconds=300,
        )
        session.add(rule)
        await session.flush()

        session.add(
            RuleTriggerState(
                tenant_id="TENANT-A",
                rule_id=str(rule.rule_id),
                device_id="DEVICE-1",
                last_triggered_at=datetime.now(),
                triggered_once=False,
            )
        )
        await session.commit()

        repository = RuleRepository(session, _ctx())
        acquired = await repository.try_acquire_trigger_slot(
            rule_id=str(rule.rule_id),
            device_id="DEVICE-1",
            cooldown_mode="interval",
            cooldown_seconds=300,
        )

    assert acquired is False


@pytest.mark.asyncio
async def test_status_progression_is_idempotent_and_does_not_move_backward(session_factory):
    now = datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        service = NotificationDeliveryAuditService(session, _ctx())
        row = await service.create_send_attempt(
            channel="sms",
            raw_recipient="+15550123456",
            provider_name="twilio",
            event_type="threshold_alert",
            attempted_at=now,
        )
        accepted = await service.mark_provider_accepted(row.id, provider_message_id="MSG-1", accepted_at=now)
        accepted_again = await service.mark_provider_accepted(
            row.id,
            provider_message_id="MSG-1",
            accepted_at=datetime(2026, 4, 8, 11, 0, tzinfo=timezone.utc),
        )
        delivered = await service.mark_delivered(row.id, delivered_at=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc))
        after_backwards_attempt = await service.mark_provider_accepted(
            row.id,
            accepted_at=datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc),
        )
        after_failed_attempt = await service.mark_failed(
            row.id,
            failure_code="500",
            failure_message="late provider error",
            failed_at=datetime(2026, 4, 8, 14, 0, tzinfo=timezone.utc),
        )
        await session.commit()

    assert accepted is not None
    assert accepted_again is not None
    assert accepted.accepted_at == accepted_again.accepted_at
    assert delivered is not None
    assert delivered.status == NotificationDeliveryStatus.DELIVERED.value
    assert delivered.billable_units == 1
    assert after_backwards_attempt is not None
    assert after_backwards_attempt.status == NotificationDeliveryStatus.DELIVERED.value
    assert after_failed_attempt is not None
    assert after_failed_attempt.status == NotificationDeliveryStatus.DELIVERED.value
    assert after_failed_attempt.failure_code is None


@pytest.mark.asyncio
async def test_provider_message_id_updates_target_correct_tenant_row(session_factory):
    now = datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        service_a = NotificationDeliveryAuditService(session, _ctx("TENANT-A"))
        service_b = NotificationDeliveryAuditService(session, _ctx("TENANT-B"))
        super_service = NotificationDeliveryAuditService(session, _super_admin_ctx())

        row_a = await service_a.create_send_attempt(
            channel="sms",
            raw_recipient="+15550111111",
            provider_name="twilio",
            event_type="threshold_alert",
            attempted_at=now,
        )
        row_b = await service_b.create_send_attempt(
            channel="sms",
            raw_recipient="+15550222222",
            provider_name="twilio",
            event_type="threshold_alert",
            attempted_at=now,
        )
        await service_a.mark_provider_accepted(row_a.id, provider_message_id="MSG-DUPE", accepted_at=now)
        await service_b.mark_provider_accepted(row_b.id, provider_message_id="MSG-DUPE", accepted_at=now)

        updated = await super_service.mark_failed_by_message_id(
            tenant_id="TENANT-A",
            provider_message_id="MSG-DUPE",
            failure_code="300",
            failure_message="carrier rejected",
        )
        await session.commit()

        refreshed_a = await service_a.list_for_month(year=2026, month=4)
        refreshed_b = await service_b.list_for_month(year=2026, month=4)

    assert updated is not None
    assert updated.tenant_id == "TENANT-A"
    assert refreshed_a[0].status == NotificationDeliveryStatus.FAILED.value
    assert refreshed_b[0].status == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value


@pytest.mark.asyncio
async def test_mark_delivered_by_message_id_is_idempotent(session_factory):
    now = datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        tenant_service = NotificationDeliveryAuditService(session, _ctx("TENANT-A"))
        super_service = NotificationDeliveryAuditService(session, _super_admin_ctx())

        row = await tenant_service.create_send_attempt(
            channel="whatsapp",
            raw_recipient="+15550333333",
            provider_name="twilio",
            event_type="threshold_alert",
            attempted_at=now,
        )
        await tenant_service.mark_provider_accepted(row.id, provider_message_id="MSG-WA-IDEMPOTENT", accepted_at=now)

        first = await super_service.mark_delivered_by_message_id(
            tenant_id="TENANT-A",
            provider_message_id="MSG-WA-IDEMPOTENT",
            delivered_at=datetime(2026, 4, 9, 10, 5, tzinfo=timezone.utc),
            provider_name="twilio",
            channel="whatsapp",
        )
        first_delivered_at = first.delivered_at if first else None
        second = await super_service.mark_delivered_by_message_id(
            tenant_id="TENANT-A",
            provider_message_id="MSG-WA-IDEMPOTENT",
            delivered_at=datetime(2026, 4, 9, 10, 10, tzinfo=timezone.utc),
            provider_name="twilio",
            channel="whatsapp",
        )
        await session.commit()

    assert first is not None
    assert second is not None
    assert first.status == NotificationDeliveryStatus.DELIVERED.value
    assert second.status == NotificationDeliveryStatus.DELIVERED.value
    assert first_delivered_at == second.delivered_at


@pytest.mark.asyncio
async def test_billable_unit_invariants_enforced_by_service(session_factory):
    now = datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        service = NotificationDeliveryAuditService(session, _ctx())
        row = await service.create_send_attempt(
            channel="email",
            raw_recipient="ops@example.com",
            provider_name="smtp",
            event_type="threshold_alert",
            attempted_at=now,
        )
        accepted = await service.mark_provider_accepted(row.id, provider_message_id="EMAIL-1")
        accepted_units = accepted.billable_units if accepted else None
        failed = await service.mark_failed(row.id, failure_code="500", failure_message="downstream error")
        await session.commit()

    assert accepted is not None
    assert accepted_units == 1
    assert failed is not None
    assert failed.billable_units == 0


@pytest.mark.asyncio
async def test_csv_export_escapes_commas_and_newlines_correctly(session_factory):
    now = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        service = NotificationDeliveryAuditService(session, _ctx())
        row = await service.create_send_attempt(
            channel="whatsapp",
            raw_recipient="+15550123456",
            provider_name="twilio",
            event_type="threshold_alert",
            attempted_at=now,
            metadata_json={"k": "v"},
        )
        await service.mark_failed(
            row.id,
            failure_code="63016",
            failure_message="Template rejected,\ncontains newline",
            failed_at=now,
        )
        await session.commit()
        csv_text = await service.export_month_logs_csv(tenant_id="TENANT-A", month="2026-04")

    parsed = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(parsed) == 1
    assert parsed[0]["Failure Code"] == "63016"
    assert parsed[0]["Failure Message"] == "Template rejected,\ncontains newline"


@pytest.mark.asyncio
async def test_export_ordering_is_stable_by_attempted_at_then_id(session_factory):
    same_time = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        service = NotificationDeliveryAuditService(session, _ctx())
        row_1 = await service.create_send_attempt(
            channel="email",
            raw_recipient="a@example.com",
            provider_name="smtp",
            event_type="threshold_alert",
            attempted_at=same_time,
        )
        row_2 = await service.create_send_attempt(
            channel="email",
            raw_recipient="b@example.com",
            provider_name="smtp",
            event_type="threshold_alert",
            attempted_at=same_time,
        )
        await session.commit()

        page = await service.list_month_logs(tenant_id="TENANT-A", month="2026-04", page=1, page_size=50)

    assert [row.id for row in page.rows] == sorted([row_1.id, row_2.id])


@pytest.mark.asyncio
async def test_retention_policy_is_safe_by_default_and_opt_in_when_enabled(session_factory, monkeypatch):
    async with session_factory() as session:
        service = NotificationDeliveryAuditService(session, _ctx())
        old_row = await service.create_send_attempt(
            channel="sms",
            raw_recipient="+15550000001",
            provider_name="twilio",
            event_type="threshold_alert",
            attempted_at=datetime(2024, 12, 15, 0, 0, tzinfo=timezone.utc),
        )
        recent_row = await service.create_send_attempt(
            channel="sms",
            raw_recipient="+15550000002",
            provider_name="twilio",
            event_type="threshold_alert",
            attempted_at=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
        )
        await session.commit()

        monkeypatch.setattr(notification_delivery_module.settings, "NOTIFICATION_DELIVERY_RETENTION_ENABLED", False)
        disabled_deleted = await service.apply_retention_policy(now=datetime(2026, 4, 16, tzinfo=timezone.utc))
        await session.commit()

        monkeypatch.setattr(notification_delivery_module.settings, "NOTIFICATION_DELIVERY_RETENTION_ENABLED", True)
        monkeypatch.setattr(notification_delivery_module.settings, "NOTIFICATION_DELIVERY_RETENTION_MONTHS", 12)
        enabled_deleted = await service.apply_retention_policy(now=datetime(2026, 4, 16, tzinfo=timezone.utc))
        await session.commit()

        rows_after = await service.list_for_month(year=2026, month=4)

    assert disabled_deleted == 0
    assert enabled_deleted == 1
    assert rows_after and rows_after[0].id == recent_row.id
    assert old_row.id != recent_row.id


@pytest.mark.asyncio
async def test_db_rejects_status_billable_mismatch(session_factory):
    async with session_factory() as session:
        bad_row = NotificationDeliveryLog(
            id="mismatch-1",
            tenant_id="TENANT-A",
            alert_id=None,
            rule_id=None,
            device_id="D1",
            event_type="threshold_alert",
            channel="sms",
            recipient_masked="******1111",
            recipient_hash="h-bad",
            provider_name="twilio",
            provider_message_id="MSG-BAD",
            status=NotificationDeliveryStatus.FAILED.value,
            billable_units=1,
            attempted_at=datetime(2026, 4, 16, 0, 0, tzinfo=timezone.utc),
            accepted_at=None,
            delivered_at=None,
            failed_at=datetime(2026, 4, 16, 0, 1, tzinfo=timezone.utc),
            failure_code="500",
            failure_message="bad write",
            metadata_json={"source": "test"},
            created_at=datetime(2026, 4, 16, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 16, 0, 1, tzinfo=timezone.utc),
        )
        session.add(bad_row)
        with pytest.raises(IntegrityError):
            await session.commit()
