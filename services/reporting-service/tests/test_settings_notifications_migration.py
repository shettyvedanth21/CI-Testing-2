from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest
sys.path.insert(0, "/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/services/reporting-service")
sys.path.insert(1, "/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/services")

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from src.handlers import settings as settings_handler


class _FakeChannel:
    def __init__(self, channel_id: int, value: str, is_active: bool = True):
        self.id = channel_id
        self.value = value
        self.is_active = is_active


class _FakeSettingsRepo:
    def __init__(self):
        self.deleted_channel_id = None

    async def list_active_channels(self, channel_type: str):
        assert channel_type == "email"
        return [
            _FakeChannel(1, "legacy-ops@tenant.com"),
            _FakeChannel(2, "legacy-guard@tenant.com"),
        ]

    async def add_email_channel(self, email: str):
        return _FakeChannel(3, email)

    async def disable_email_channel(self, channel_id: int):
        self.deleted_channel_id = channel_id
        return True


@pytest.mark.asyncio
async def test_get_notifications_returns_active_channels(monkeypatch):
    monkeypatch.setattr(settings_handler, "_settings_repo", lambda request, db: _FakeSettingsRepo())

    payload = await settings_handler.get_notifications(
        request=SimpleNamespace(),
        db=SimpleNamespace(),
    )

    assert payload["email"] == [
        {"id": 1, "value": "legacy-ops@tenant.com", "is_active": True},
        {"id": 2, "value": "legacy-guard@tenant.com", "is_active": True},
    ]


@pytest.mark.asyncio
async def test_add_notification_email_creates_channel(monkeypatch):
    monkeypatch.setattr(settings_handler, "_settings_repo", lambda request, db: _FakeSettingsRepo())
    payload = settings_handler.NotificationEmailRequest(email="Ops@Example.com")

    result = await settings_handler.add_notification_email(
        payload=payload,
        request=SimpleNamespace(),
        db=SimpleNamespace(),
    )

    assert result == {"id": 3, "value": "ops@example.com", "is_active": True}


@pytest.mark.asyncio
async def test_delete_notification_email_disables_channel(monkeypatch):
    repo = _FakeSettingsRepo()
    monkeypatch.setattr(settings_handler, "_settings_repo", lambda request, db: repo)

    result = await settings_handler.delete_notification_email(
        channel_id=1,
        request=SimpleNamespace(),
        db=SimpleNamespace(),
    )

    assert result == {"success": True, "id": 1}
    assert repo.deleted_channel_id == 1
