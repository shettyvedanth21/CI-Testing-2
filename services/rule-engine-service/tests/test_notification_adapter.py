from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from email import message_from_string
from pathlib import Path

import pytest
import pytest_asyncio

RULE_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = RULE_SERVICE_ROOT.parent
REPO_ROOT = RULE_SERVICE_ROOT.parent.parent
sys.path = [p for p in sys.path if p not in {str(RULE_SERVICE_ROOT), str(SERVICES_ROOT), str(REPO_ROOT)}]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SERVICES_ROOT))
sys.path.insert(0, str(RULE_SERVICE_ROOT))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.database import Base
from app.models.rule import NotificationChannelSetting, Rule, RuleScope, RuleStatus, RuleType
from app.notifications.adapter import EmailAdapter, NotificationAdapter, SmsAdapter, WhatsAppAdapter
from app.services.notification_delivery import NotificationDeliveryAuditService
from services.shared.tenant_context import TenantContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _make_rule(*, rule_id: str, recipients: list[dict]) -> Rule:
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
        notification_channels=["email"],
        notification_recipients=recipients,
        device_ids=["P1"],
        created_at=now,
        updated_at=now,
    )


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
        user_id="test-user",
        role="tenant_admin",
        plant_ids=["PLANT-1"],
        is_super_admin=False,
    )


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


class _FakeResponse:
    def __init__(self, status_code: int = 201):
        self.status_code = status_code


class _FakeAsyncClient:
    requests: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.args = args
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
        return _FakeResponse()


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
        self.sent_messages.append(
            {
                "from": from_address,
                "recipients": list(recipients),
                "message": message,
            }
        )


@pytest.mark.asyncio
async def test_email_adapter_sends_only_rule_attached_recipients(monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    adapter = EmailAdapter()
    adapter._enabled = True
    adapter._smtp_host = "smtp.example.com"
    adapter._smtp_port = 587
    adapter._smtp_username = "user"
    adapter._smtp_password = "pass"
    adapter._from_address = "alerts@example.com"

    plant_a_rule = _make_rule(
        rule_id="rule-plant-a",
        recipients=[
            {"channel": "email", "value": "opsA@example.com"},
            {"channel": "email", "value": "opsA@example.com"},
            {"channel": "email", "value": "guardA@example.com"},
        ],
    )
    _plant_b_rule = _make_rule(
        rule_id="rule-plant-b",
        recipients=[
            {"channel": "email", "value": "opsB@example.com"},
        ],
    )

    sent = await adapter.send_alert(
        subject="Plant A alert",
        message="Alert fired",
        rule=plant_a_rule,
        device_id="P1",
    )

    assert sent is True
    assert len(_FakeSMTP.sent_messages) == 2
    assert sorted(message["recipients"] for message in _FakeSMTP.sent_messages) == [
        ["guarda@example.com"],
        ["opsa@example.com"],
    ]
    combined_messages = "\n".join(entry["message"].lower() for entry in _FakeSMTP.sent_messages)
    assert "opsb@example.com" not in combined_messages
    assert all("message-id:" in entry["message"].lower() for entry in _FakeSMTP.sent_messages)
    assert "to: guarda@example.com" in _FakeSMTP.sent_messages[0]["message"].lower() or "to: guarda@example.com" in _FakeSMTP.sent_messages[1]["message"].lower()
    assert "to: opsa@example.com" in _FakeSMTP.sent_messages[0]["message"].lower() or "to: opsa@example.com" in _FakeSMTP.sent_messages[1]["message"].lower()


@pytest.mark.asyncio
async def test_email_adapter_uses_tenant_settings_recipients_when_rule_has_no_direct_recipients(session_factory, monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    async with session_factory() as session:
        session.add_all(
            [
                NotificationChannelSetting(tenant_id="TENANT-A", channel_type="email", value="ops@example.com", is_active=True),
                NotificationChannelSetting(tenant_id="TENANT-A", channel_type="email", value="guard@example.com", is_active=True),
            ]
        )
        await session.commit()

        adapter = EmailAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        adapter._enabled = True
        adapter._smtp_host = "smtp.example.com"
        adapter._smtp_port = 587
        adapter._smtp_username = "user"
        adapter._smtp_password = "pass"
        adapter._from_address = "alerts@example.com"

        rule = _make_rule(rule_id="rule-settings-recipients", recipients=[])

        sent = await adapter.send_alert(
            subject="Tenant recipients",
            message="Alert fired",
            rule=rule,
            device_id="P1",
        )

    assert sent is True
    assert len(_FakeSMTP.sent_messages) == 2
    assert sorted(message["recipients"] for message in _FakeSMTP.sent_messages) == [
        ["guard@example.com"],
        ["ops@example.com"],
    ]


@pytest.mark.asyncio
async def test_email_adapter_skips_send_when_no_email_recipients_exist(session_factory, monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    async with session_factory() as session:
        adapter = EmailAdapter(audit_service=NotificationDeliveryAuditService(session, _ctx()))
        adapter._enabled = True
        adapter._smtp_host = "smtp.example.com"
        adapter._smtp_port = 587
        adapter._smtp_username = "user"
        adapter._smtp_password = "pass"
        adapter._from_address = "alerts@example.com"

        rule = _make_rule(rule_id="rule-no-recipients", recipients=[])

        sent = await adapter.send_alert(
            subject="No recipients",
            message="Alert fired",
            rule=rule,
            device_id="P1",
        )

    assert sent is False
    assert _FakeSMTP.sent_messages == []


@pytest.mark.asyncio
async def test_sms_adapter_sends_normalized_phone_recipients(monkeypatch):
    _FakeAsyncClient.requests = []
    fake = _FakeAsyncClient()
    monkeypatch.setattr("app.shared_http.get_twilio_http_client", lambda: fake)
    monkeypatch.setattr("app.notifications.adapter.get_twilio_http_client", lambda: fake)

    adapter = SmsAdapter()
    adapter._enabled = True
    adapter._account_sid = "AC123"
    adapter._auth_token = "secret"
    adapter._from_number = "+15550000000"

    rule = _make_rule(
        rule_id="rule-sms",
        recipients=[
            {"channel": "sms", "value": "+1 (555) 123-4567"},
            {"channel": "sms", "value": "1 555 123 4567"},
            {"channel": "email", "value": "ops@example.com"},
        ],
    )

    sent = await adapter.send_alert(
        subject="SMS alert",
        message="Threshold exceeded",
        rule=rule,
        device_id="P1",
        alert_context={
            "rule_name": "Current Guard",
            "device_name": "Compressor A",
            "device_id": "P1",
            "device_location": "Line 1",
            "property_label": "Current",
            "condition_label": "greater than 1.0",
            "actual_value": "1.234",
            "triggered_at": "16 Apr 2026, 03:15 PM IST",
        },
    )

    assert sent is True
    assert len(_FakeAsyncClient.requests) == 1
    request = _FakeAsyncClient.requests[0]
    assert request["url"].endswith("/Messages.json")
    assert request["data"]["From"] == "+15550000000"
    assert request["data"]["To"] == "+15551234567"
    assert request["data"]["Body"] == "\n".join(
        [
            "Energy Alert",
            "Rule: Current Guard",
            "Device: Compressor A (P1)",
            "Location: Line 1",
            "Condition: greater than 1.0",
            "Actual: 1.234",
            "Time: 16 Apr 2026, 03:15 PM IST",
        ]
    )


@pytest.mark.asyncio
async def test_whatsapp_adapter_prefixes_phone_recipients(monkeypatch):
    _FakeAsyncClient.requests = []
    fake = _FakeAsyncClient()
    monkeypatch.setattr("app.shared_http.get_twilio_http_client", lambda: fake)
    monkeypatch.setattr("app.notifications.adapter.get_twilio_http_client", lambda: fake)

    adapter = WhatsAppAdapter()
    adapter._enabled = True
    adapter._account_sid = "AC456"
    adapter._auth_token = "secret"
    adapter._from_number = "+15550000001"

    rule = _make_rule(
        rule_id="rule-whatsapp",
        recipients=[
            {"channel": "whatsapp", "value": "+1 (555) 987-6543"},
        ],
    )

    sent = await adapter.send_alert(
        subject="WhatsApp alert",
        message="Threshold exceeded",
        rule=rule,
        device_id="P1",
        alert_context={
            "rule_name": "Current Guard",
            "device_name": "Compressor A",
            "device_id": "P1",
            "device_location": "Line 1",
            "property_label": "Current",
            "condition_label": "greater than 1.0",
            "actual_value": "1.234",
            "triggered_at": "16 Apr 2026, 03:15 PM IST",
        },
    )

    assert sent is True
    assert len(_FakeAsyncClient.requests) == 1
    request = _FakeAsyncClient.requests[0]
    assert request["data"]["From"] == "whatsapp:+15550000001"
    assert request["data"]["To"] == "whatsapp:+15559876543"
    assert request["data"]["Body"] == "\n".join(
        [
            "Energy Alert",
            "Rule Name: Current Guard",
            "Device Name: Compressor A",
            "Device ID: P1",
            "Device Location: Line 1",
            "Property: Current",
            "Condition: greater than 1.0",
            "Actual Value: 1.234",
            "Triggered Time: 16 Apr 2026, 03:15 PM IST",
        ]
    )


@pytest.mark.asyncio
async def test_sms_adapter_fails_closed_when_enabled_without_credentials(monkeypatch):
    _FakeAsyncClient.requests = []
    fake = _FakeAsyncClient()
    monkeypatch.setattr("app.shared_http.get_twilio_http_client", lambda: fake)
    monkeypatch.setattr("app.notifications.adapter.get_twilio_http_client", lambda: fake)

    adapter = SmsAdapter()
    adapter._enabled = True
    adapter._account_sid = None
    adapter._auth_token = None
    adapter._from_number = None

    rule = _make_rule(
        rule_id="rule-sms-missing-creds",
        recipients=[{"channel": "sms", "value": "+1 (555) 123-4567"}],
    )

    sent = await adapter.send_alert(
        subject="SMS alert",
        message="Threshold exceeded",
        rule=rule,
        device_id="P1",
    )

    assert sent is False
    assert _FakeAsyncClient.requests == []


def test_notification_adapter_supported_channels_are_email_sms_whatsapp():
    adapter = NotificationAdapter()
    assert adapter.get_supported_channels() == ["email", "sms", "whatsapp"]


@pytest.mark.asyncio
async def test_alert_email_html_is_structured_and_excludes_legacy_raw_message(monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    adapter = EmailAdapter()
    adapter._enabled = True
    adapter._smtp_host = "smtp.example.com"
    adapter._smtp_port = 587
    adapter._smtp_username = "user"
    adapter._smtp_password = "pass"
    adapter._from_address = "alerts@example.com"

    rule = _make_rule(
        rule_id="rule-email-structured",
        recipients=[{"channel": "email", "value": "ops@example.com"}],
    )

    sent = await adapter.send_alert(
        subject="Alert",
        message="🚨 Alert: raw blob should never be visible",
        rule=rule,
        device_id="AD00000001",
        alert_context={
            "rule_name": "OP A",
            "device_name": "Compressor A",
            "device_id": "AD00000001",
            "device_location": "Shop Floor 1",
            "property": "Current",
            "condition": "greater than 1.0",
            "actual_value": "1.001",
            "triggered_at": "16 Apr 2026, 03:15 PM IST",
        },
    )

    assert sent is True
    assert len(_FakeSMTP.sent_messages) == 1
    raw_email = _FakeSMTP.sent_messages[0]["message"]
    parsed = message_from_string(raw_email)
    payloads = parsed.get_payload()
    plain_part = payloads[0].get_payload(decode=True).decode("utf-8", errors="replace")
    html_part = payloads[1].get_payload(decode=True).decode("utf-8", errors="replace")

    for label in [
        "Rule Name",
        "Device Name",
        "Device ID",
        "Device Location",
        "Property",
        "Condition",
        "Actual Value",
        "Triggered Time",
    ]:
        assert label in html_part
        assert label in plain_part

    assert "Compressor A" in html_part
    assert "Shop Floor 1" in html_part
    assert "Message:" not in html_part
    assert "raw blob should never be visible" not in html_part


@pytest.mark.asyncio
async def test_alert_email_handles_missing_location_and_name_fallback(monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    adapter = EmailAdapter()
    adapter._enabled = True
    adapter._smtp_host = "smtp.example.com"
    adapter._smtp_port = 587
    adapter._smtp_username = "user"
    adapter._smtp_password = "pass"
    adapter._from_address = "alerts@example.com"

    rule = _make_rule(
        rule_id="rule-email-fallback",
        recipients=[{"channel": "email", "value": "ops@example.com"}],
    )

    sent = await adapter.send_alert(
        subject="Alert",
        message="Threshold exceeded",
        rule=rule,
        device_id="AD00000001",
        alert_context={
            "rule_name": "OP B",
            "device_name": "AD00000001",
            "device_id": "AD00000001",
            "property": "Current",
            "condition": "greater than 1.0",
            "actual_value": "1.001",
            "triggered_at": "16 Apr 2026, 03:15 PM IST",
        },
    )

    assert sent is True
    raw_email = _FakeSMTP.sent_messages[0]["message"]
    parsed = message_from_string(raw_email)
    html_part = parsed.get_payload()[1].get_payload(decode=True).decode("utf-8", errors="replace")
    plain_part = parsed.get_payload()[0].get_payload(decode=True).decode("utf-8", errors="replace")

    assert "Device Name" in html_part and "AD00000001" in html_part
    assert "Not specified" in html_part
    assert "Not specified" in plain_part


@pytest.mark.asyncio
async def test_rule_created_email_all_devices_scope_never_shows_na(monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    adapter = EmailAdapter()
    adapter._enabled = True
    adapter._smtp_host = "smtp.example.com"
    adapter._smtp_port = 587
    adapter._smtp_username = "user"
    adapter._smtp_password = "pass"
    adapter._from_address = "alerts@example.com"

    rule = _make_rule(
        rule_id="rule-created-all-devices",
        recipients=[{"channel": "email", "value": "ops@example.com"}],
    )
    rule.scope = RuleScope.ALL_DEVICES.value
    rule.device_ids = []

    sent = await adapter.send_alert(
        subject="Rule Created",
        message="Rule created successfully",
        rule=rule,
        device_id="All Machines",
        device_names="All accessible machines",
        scope_label="All Machines",
        alert_type="rule_created",
    )

    assert sent is True
    raw_email = _FakeSMTP.sent_messages[0]["message"]
    parsed = message_from_string(raw_email)
    html_part = parsed.get_payload()[1].get_payload(decode=True).decode("utf-8", errors="replace")
    assert "Scope:</span> All Machines" in html_part
    assert "Devices:</span> All accessible machines" in html_part
    assert "Devices:</span> N/A" not in html_part


@pytest.mark.asyncio
async def test_rule_created_email_selected_devices_scope_shows_device_targets(monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", _FakeSMTP)

    adapter = EmailAdapter()
    adapter._enabled = True
    adapter._smtp_host = "smtp.example.com"
    adapter._smtp_port = 587
    adapter._smtp_username = "user"
    adapter._smtp_password = "pass"
    adapter._from_address = "alerts@example.com"

    rule = _make_rule(
        rule_id="rule-created-selected-devices",
        recipients=[{"channel": "email", "value": "ops@example.com"}],
    )
    rule.scope = RuleScope.SELECTED_DEVICES.value
    rule.device_ids = ["AD00000001", "AD00000002"]

    sent = await adapter.send_alert(
        subject="Rule Created",
        message="Rule created successfully",
        rule=rule,
        device_id="AD00000001, AD00000002",
        device_names="AD00000001, AD00000002",
        scope_label="Selected Machines",
        alert_type="rule_created",
    )

    assert sent is True
    raw_email = _FakeSMTP.sent_messages[0]["message"]
    parsed = message_from_string(raw_email)
    html_part = parsed.get_payload()[1].get_payload(decode=True).decode("utf-8", errors="replace")
    assert "Scope:</span> Selected Machines" in html_part
    assert "Devices:</span> AD00000001, AD00000002" in html_part


def test_sms_formatter_shortens_deterministically_and_preserves_priority_fields():
    context = {
        "rule_name": "Very Long Rule Name " * 6,
        "device_name": "Very Long Device Name " * 6,
        "device_id": "AD00000001",
        "device_location": "Very Long Device Location " * 8,
        "property": "Current",
        "condition": "greater than 1.0 and sustained beyond configured sampling window tolerance",
        "actual_value": "12345.6789",
        "triggered_at": "16 Apr 2026, 03:15 PM IST",
    }

    body = SmsAdapter._format_sms_alert_message(context, max_len=180)
    assert len(body) <= 180
    assert "Rule:" in body
    assert "Device:" in body
    assert "Condition:" in body
    assert "Actual:" in body
    assert "Time:" in body
    assert "🚨 Alert:" not in body
    assert "Message:" not in body


@pytest.mark.asyncio
async def test_sms_and_whatsapp_fallback_metadata_is_clean(monkeypatch):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.responses = [
        _FakeResponse(),
        _FakeResponse(),
    ]
    fake = _FakeAsyncClient()
    monkeypatch.setattr("app.shared_http.get_twilio_http_client", lambda: fake)
    monkeypatch.setattr("app.notifications.adapter.get_twilio_http_client", lambda: fake)

    sms_adapter = SmsAdapter()
    sms_adapter._enabled = True
    sms_adapter._account_sid = "AC123"
    sms_adapter._auth_token = "secret"
    sms_adapter._from_number = "+15550000000"

    wa_adapter = WhatsAppAdapter()
    wa_adapter._enabled = True
    wa_adapter._account_sid = "AC456"
    wa_adapter._auth_token = "secret"
    wa_adapter._from_number = "+15550000001"

    sms_rule = _make_rule(
        rule_id="rule-sms-fallback",
        recipients=[{"channel": "sms", "value": "+1 (555) 123-4567"}],
    )
    wa_rule = _make_rule(
        rule_id="rule-wa-fallback",
        recipients=[{"channel": "whatsapp", "value": "+1 (555) 987-6543"}],
    )

    await sms_adapter.send_alert(
        subject="SMS fallback",
        message="Threshold exceeded",
        rule=sms_rule,
        device_id="AD00000001",
        alert_context={
            "rule_name": "Current Guard",
            "device_name": "AD00000001",
            "device_id": "AD00000001",
            "device_location": "Not specified",
            "condition_label": "greater than 1.0",
            "actual_value": "1.234",
            "triggered_at": "16 Apr 2026, 03:15 PM IST",
        },
    )
    await wa_adapter.send_alert(
        subject="WA fallback",
        message="Threshold exceeded",
        rule=wa_rule,
        device_id="AD00000001",
        alert_context={
            "rule_name": "Current Guard",
            "device_name": "AD00000001",
            "device_id": "AD00000001",
            "device_location": "Not specified",
            "condition_label": "greater than 1.0",
            "actual_value": "1.234",
            "triggered_at": "16 Apr 2026, 03:15 PM IST",
        },
    )

    assert len(_FakeAsyncClient.requests) == 2
    sms_body = _FakeAsyncClient.requests[0]["data"]["Body"]
    wa_body = _FakeAsyncClient.requests[1]["data"]["Body"]
    assert "Device: AD00000001" in sms_body
    assert "Location: Not specified" not in sms_body
    assert "Device Name: AD00000001" in wa_body
    assert "Device Location: Not specified" in wa_body
