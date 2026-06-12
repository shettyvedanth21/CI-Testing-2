from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


RULE_ENGINE_ROOT = Path(__file__).resolve().parents[1] / "services" / "rule-engine-service"
SERVICES_ROOT = Path(__file__).resolve().parents[1] / "services"
for path in (RULE_ENGINE_ROOT, SERVICES_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("REPORTING_SERVICE_URL", "http://reporting-service")
os.environ.setdefault("EMAIL_SMTP_HOST", "smtp.test.local")
os.environ.setdefault("EMAIL_SMTP_USERNAME", "mailer")
os.environ.setdefault("EMAIL_SMTP_PASSWORD", "secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.notifications.adapter import EmailAdapter


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *, calls: list[dict], payload_by_tenant: dict[str, dict]) -> None:
        self._calls = calls
        self._payload_by_tenant = payload_by_tenant

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers: dict[str, str]):
        self._calls.append({"url": url, "headers": headers})
        tenant_id = headers.get("X-Tenant-Id")
        return _FakeResponse(self._payload_by_tenant.get(tenant_id, {"email": []}))


class _FakeSMTP:
    sent_messages: list[dict] = []

    def __init__(self, host: str, port: int) -> None:
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

    def login(self, username: str, password: str):
        self.username = username
        self.password = password

    def sendmail(self, from_address: str, recipients: list[str], message: str):
        self.sent_messages.append(
            {
                "from": from_address,
                "to": list(recipients),
                "message": message,
            }
        )


def _rule(*, tenant_id: str | None):
    return SimpleNamespace(
        tenant_id=tenant_id,
        rule_name="Power Alert",
        rule_id="rule-1",
        rule_type="threshold",
        property="power",
        condition=">",
        threshold=5.0,
        time_window_start=None,
        time_window_end=None,
        notification_channels=["email"],
        status="active",
        created_at=None,
    )


@pytest.mark.asyncio
async def test_email_delivery_fetches_only_request_tenant_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    payload_by_tenant = {
        "ORG-A": {"email": [{"value": "alerts-a@example.com", "is_active": True}]},
        "ORG-B": {"email": [{"value": "alerts-b@example.com", "is_active": True}]},
    }
    fake_smtp_class = type("FakeSMTPClass", (_FakeSMTP,), {"sent_messages": []})

    monkeypatch.setattr(
        "app.notifications.adapter.httpx.AsyncClient",
        lambda timeout=5.0: _FakeAsyncClient(calls=calls, payload_by_tenant=payload_by_tenant),
    )
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", fake_smtp_class)

    adapter = EmailAdapter()
    adapter._enabled = True
    adapter._smtp_host = "smtp.test.local"
    adapter._smtp_port = 587
    adapter._smtp_username = "mailer"
    adapter._smtp_password = "secret"
    adapter._from_address = "alerts@test.local"

    sent = await adapter.send_alert(
        subject="Alert",
        message="Threshold exceeded",
        rule=_rule(tenant_id="ORG-A"),
        device_id="DEV-1",
    )

    assert sent is True
    assert calls == [
        {
            "url": "http://reporting-service/api/v1/settings/notifications",
            "headers": {
                "X-Internal-Service": "rule-engine-service",
                "X-Tenant-Id": "ORG-A",
            },
        }
    ]
    assert fake_smtp_class.sent_messages[0]["to"] == ["alerts-a@example.com"]
    assert "alerts-b@example.com" not in fake_smtp_class.sent_messages[0]["message"]


@pytest.mark.asyncio
async def test_email_delivery_fails_closed_without_rule_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.notifications.adapter.httpx.AsyncClient",
        lambda timeout=5.0: _FakeAsyncClient(calls=calls, payload_by_tenant={}),
    )

    adapter = EmailAdapter()
    adapter._enabled = True
    adapter._smtp_host = "smtp.test.local"
    adapter._smtp_port = 587
    adapter._smtp_username = "mailer"
    adapter._smtp_password = "secret"

    with pytest.raises(ValueError, match="tenant scope is required"):
        await adapter.send_alert(
            subject="Alert",
            message="Threshold exceeded",
            rule=_rule(tenant_id=None),
            device_id="DEV-1",
        )

    assert calls == []


@pytest.mark.asyncio
async def test_email_delivery_same_tenant_success_path_remains_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    payload_by_tenant = {
        "ORG-A": {
            "email": [
                {"value": "alerts-a@example.com", "is_active": True},
                {"value": "alerts-a-2@example.com", "is_active": True},
            ]
        }
    }
    fake_smtp_class = type("FakeSMTPClass", (_FakeSMTP,), {"sent_messages": []})

    monkeypatch.setattr(
        "app.notifications.adapter.httpx.AsyncClient",
        lambda timeout=5.0: _FakeAsyncClient(calls=calls, payload_by_tenant=payload_by_tenant),
    )
    monkeypatch.setattr("app.notifications.adapter.smtplib.SMTP", fake_smtp_class)

    adapter = EmailAdapter()
    adapter._enabled = True
    adapter._smtp_host = "smtp.test.local"
    adapter._smtp_port = 587
    adapter._smtp_username = "mailer"
    adapter._smtp_password = "secret"
    adapter._from_address = "alerts@test.local"

    sent = await adapter.send(
        message="Threshold exceeded",
        rule=_rule(tenant_id="ORG-A"),
        device_id="DEV-1",
    )

    assert sent is True
    assert calls[0]["headers"]["X-Tenant-Id"] == "ORG-A"
    assert sorted(fake_smtp_class.sent_messages[0]["to"]) == [
        "alerts-a-2@example.com",
        "alerts-a@example.com",
    ]
