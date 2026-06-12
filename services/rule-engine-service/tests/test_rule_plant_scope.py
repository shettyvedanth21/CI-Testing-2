from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "rule-engine-service"
SERVICES_ROOT = ROOT / "services"
for path in (ROOT, SERVICE_ROOT, SERVICES_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.database import Base
from app.models.rule import ActivityEvent, Alert, Rule, RuleScope, RuleStatus
from app.repositories.rule import ActivityEventRepository, AlertRepository, RuleRepository
from app.schemas.rule import (
    ConditionOperator,
    NotificationChannel,
    NotificationRecipientTarget,
    RuleCreate,
    RuleUpdate,
)
from app.services.rule import DuplicateRuleError, RuleService
from app.services.device_scope import DeviceScopeService
from app.config import settings
from app.api.v1.alerts import _resolve_accessible_device_ids as resolve_alert_accessible_device_ids
from app.api.v1.rules import _resolve_accessible_device_ids as resolve_rule_accessible_device_ids
from services.shared.feature_entitlements import build_feature_entitlement_state
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


def _ctx(
    role: str = "plant_manager",
    *,
    user_id: str = "plant-manager-1",
    plant_ids: list[str] | None = None,
    is_super_admin: bool = False,
    premium_feature_grants: list[str] | None = None,
) -> TenantContext:
    entitlements = build_feature_entitlement_state(
        role=role,
        premium_feature_grants=[] if premium_feature_grants is None else premium_feature_grants,
        role_feature_matrix={},
    )
    return TenantContext(
        tenant_id="TENANT-A",
        user_id=user_id,
        role=role,
        plant_ids=["PLANT-1"] if plant_ids is None else plant_ids,
        is_super_admin=is_super_admin,
        entitlements=entitlements,
    )


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://device-service")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse], calls: list[tuple[str, dict | None, dict | None]]):
        self._responses = list(responses)
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params=None, headers=None):
        self._calls.append((url, params, headers))
        if not self._responses:
            raise AssertionError("Unexpected extra HTTP call")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_device_scope_service_fetches_devices_by_accessible_plant(monkeypatch):
    calls: list[tuple[str, dict | None, dict | None]] = []
    responses = [
        _FakeResponse(
            200,
            {"data": [{"device_id": "P1", "plant_id": "PLANT-1"}], "total_pages": 1},
        )
    ]

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, calls),
    )
    monkeypatch.setattr(settings, "DEVICE_SERVICE_URL", "http://device-service:8001")

    device_ids = await DeviceScopeService(_ctx()).resolve_accessible_device_ids()

    assert device_ids == ["P1"]
    assert calls[0][1] == {"page": 1, "page_size": 100, "plant_id": "PLANT-1"}


@pytest.mark.asyncio
async def test_alert_scope_resolution_returns_503_on_device_service_connect_error(monkeypatch):
    async def _raise_connect_error(self):
        request = httpx.Request("GET", "http://device-service:8001/api/v1/devices")
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(DeviceScopeService, "resolve_accessible_device_ids", _raise_connect_error)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_alert_accessible_device_ids(_ctx())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "DEVICE_SCOPE_UNAVAILABLE",
        "message": "Unable to validate device access right now.",
    }


@pytest.mark.asyncio
async def test_rule_scope_resolution_returns_503_on_device_service_connect_error(monkeypatch):
    async def _raise_connect_error(self):
        request = httpx.Request("GET", "http://device-service:8001/api/v1/devices")
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(DeviceScopeService, "resolve_accessible_device_ids", _raise_connect_error)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_rule_accessible_device_ids(_ctx())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "DEVICE_SCOPE_UNAVAILABLE",
        "message": "Unable to validate device access right now.",
    }


@pytest.mark.asyncio
async def test_rule_repository_filters_to_accessible_devices(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                Rule(
                    rule_id="rule-all",
                    tenant_id="TENANT-A",
                    rule_name="All Devices",
                    scope=RuleScope.ALL_DEVICES.value,
                    device_ids=[],
                    property="power",
                    condition=">",
                    threshold=10.0,
                    status=RuleStatus.ACTIVE.value,
                    notification_channels=["email"],
                ),
                Rule(
                    rule_id="rule-p1",
                    tenant_id="TENANT-A",
                    rule_name="Plant One",
                    scope=RuleScope.SELECTED_DEVICES.value,
                    device_ids=["P1"],
                    property="power",
                    condition=">",
                    threshold=11.0,
                    status=RuleStatus.ACTIVE.value,
                    notification_channels=["email"],
                ),
                Rule(
                    rule_id="rule-all-p1",
                    tenant_id="TENANT-A",
                    rule_name="All Plant One Devices",
                    scope=RuleScope.ALL_DEVICES.value,
                    device_ids=["P1"],
                    property="power",
                    condition=">",
                    threshold=10.5,
                    status=RuleStatus.ACTIVE.value,
                    notification_channels=["email"],
                ),
                Rule(
                    rule_id="rule-p2",
                    tenant_id="TENANT-A",
                    rule_name="Plant Two",
                    scope=RuleScope.SELECTED_DEVICES.value,
                    device_ids=["P2"],
                    property="power",
                    condition=">",
                    threshold=12.0,
                    status=RuleStatus.ACTIVE.value,
                    notification_channels=["email"],
                ),
            ]
        )
        await session.commit()

        repo = RuleRepository(session, _ctx())
        rows, total = await repo.list_rules(accessible_device_ids=["P1"], page=1, page_size=20)
        filtered_rule = await repo.get_by_id("rule-p2", accessible_device_ids=["P1"])
        legacy_rule = await repo.get_by_id("rule-all", accessible_device_ids=["P1"])
        scoped_all_devices_rule = await repo.get_by_id("rule-all-p1", accessible_device_ids=["P1"])
        matching_rows, matching_total = await repo.list_rules(
            device_id="P2",
            accessible_device_ids=["P1"],
            page=1,
            page_size=20,
        )

    assert total == 2
    assert [row.rule_id for row in rows] == ["rule-p1", "rule-all-p1"]
    assert filtered_rule is None
    assert legacy_rule is None
    assert scoped_all_devices_rule is not None
    assert scoped_all_devices_rule.applies_to_device("P1") is True
    assert scoped_all_devices_rule.applies_to_device("P2") is False
    assert matching_total == 0
    assert matching_rows == []


@pytest.mark.asyncio
async def test_alert_and_activity_repositories_filter_to_accessible_devices(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                Alert(
                    alert_id="alert-p1",
                    tenant_id="TENANT-A",
                    rule_id="rule-p1",
                    device_id="P1",
                    severity="high",
                    message="Plant 1 alert",
                    actual_value=10.0,
                    threshold_value=5.0,
                    status="open",
                ),
                Alert(
                    alert_id="alert-p2",
                    tenant_id="TENANT-A",
                    rule_id="rule-p2",
                    device_id="P2",
                    severity="high",
                    message="Plant 2 alert",
                    actual_value=10.0,
                    threshold_value=5.0,
                    status="acknowledged",
                ),
                ActivityEvent(
                    event_id="event-p1",
                    tenant_id="TENANT-A",
                    device_id="P1",
                    event_type="rule_triggered",
                    title="P1",
                    message="Plant 1 event",
                    is_read=False,
                ),
                ActivityEvent(
                    event_id="event-p2",
                    tenant_id="TENANT-A",
                    device_id="P2",
                    event_type="rule_triggered",
                    title="P2",
                    message="Plant 2 event",
                    is_read=False,
                ),
                ActivityEvent(
                    event_id="event-global",
                    tenant_id="TENANT-A",
                    device_id=None,
                    event_type="rule_created",
                    title="Global",
                    message="Global event",
                    is_read=False,
                ),
            ]
        )
        await session.commit()

        alert_repo = AlertRepository(session, _ctx())
        event_repo = ActivityEventRepository(session, _ctx())

        alerts, total_alerts = await alert_repo.list_alerts(accessible_device_ids=["P1"], page=1, page_size=20)
        status_counts = await alert_repo.count_by_status(accessible_device_ids=["P1"])
        hidden_alert = await alert_repo.get_by_id("alert-p2", accessible_device_ids=["P1"])

        events, total_events = await event_repo.list_events(accessible_device_ids=["P1"], page=1, page_size=20)
        unread = await event_repo.unread_count(accessible_device_ids=["P1"])
        event_counts = await event_repo.count_by_event_types(
            ["rule_triggered", "rule_created"],
            accessible_device_ids=["P1"],
        )
        deleted = await event_repo.clear_history(accessible_device_ids=["P1"])
        await session.commit()

        remaining_events, remaining_total = await event_repo.list_events(page=1, page_size=20)

    assert total_alerts == 1
    assert [row.alert_id for row in alerts] == ["alert-p1"]
    assert status_counts == {"open": 1}
    assert hidden_alert is None

    assert total_events == 1
    assert [row.event_id for row in events] == ["event-p1"]
    assert unread == 1
    assert event_counts == {"rule_triggered": 1}
    assert deleted == 1
    assert remaining_total == 2
    assert sorted(row.event_id for row in remaining_events) == ["event-global", "event-p2"]


@pytest.mark.asyncio
async def test_rule_service_scopes_all_devices_to_accessible_devices(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="Scoped Fleet Rule",
                scope=RuleScope.ALL_DEVICES,
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=5.0,
                notification_channels=[NotificationChannel.EMAIL],
            ),
            accessible_device_ids=["P1", "P3"],
        )

    assert created.scope == RuleScope.ALL_DEVICES.value
    assert created.device_ids == ["P1", "P3"]
    assert created.applies_to_device("P1") is True
    assert created.applies_to_device("P3") is True
    assert created.applies_to_device("P9") is False


@pytest.mark.asyncio
async def test_rule_service_persists_structured_notification_recipients(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="Scoped Recipients",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["P1"],
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=5.0,
                notification_channels=[NotificationChannel.EMAIL],
                notification_recipients=[
                    NotificationRecipientTarget(channel=NotificationChannel.EMAIL, value="OPS@PlantA.com"),
                    NotificationRecipientTarget(channel=NotificationChannel.EMAIL, value="ops@planta.com"),
                    NotificationRecipientTarget(channel=NotificationChannel.EMAIL, value="guard@planta.com"),
                ],
            ),
            accessible_device_ids=["P1"],
        )
        repo = RuleRepository(session, _ctx())
        listed, total = await repo.list_rules(accessible_device_ids=["P1"], page=1, page_size=20)

    assert created.notification_recipients == [
        {"channel": "email", "value": "ops@planta.com"},
        {"channel": "email", "value": "guard@planta.com"},
    ]
    assert total == 1
    assert listed[0].notification_recipients == created.notification_recipients


@pytest.mark.asyncio
async def test_rule_service_rejects_recipient_channel_not_enabled(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx(premium_feature_grants=["notification_whatsapp"]))
        with pytest.raises(
            ValueError,
            match="Recipient target channel 'email' must also be enabled in notification_channels",
        ):
            await service.create_rule(
                RuleCreate(
                    tenant_id="TENANT-A",
                    rule_name="Mismatched Recipients",
                    scope=RuleScope.SELECTED_DEVICES,
                    device_ids=["P1"],
                    property="power",
                    condition=ConditionOperator.GREATER_THAN,
                    threshold=5.0,
                    notification_channels=[NotificationChannel.WHATSAPP],
                    notification_recipients=[
                        NotificationRecipientTarget(channel=NotificationChannel.EMAIL, value="ops@planta.com"),
                    ],
                ),
                accessible_device_ids=["P1"],
            )


@pytest.mark.asyncio
async def test_rule_service_allows_email_without_premium_notification_entitlement(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="Email Allowed",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["P1"],
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=5.0,
                notification_channels=[NotificationChannel.EMAIL],
            ),
            accessible_device_ids=["P1"],
        )

    assert created.notification_channels == ["email"]


@pytest.mark.asyncio
async def test_rule_service_rejects_sms_without_premium_notification_entitlement(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        with pytest.raises(HTTPException) as exc_info:
            await service.create_rule(
                RuleCreate(
                    tenant_id="TENANT-A",
                    rule_name="SMS Blocked",
                    scope=RuleScope.SELECTED_DEVICES,
                    device_ids=["P1"],
                    property="power",
                    condition=ConditionOperator.GREATER_THAN,
                    threshold=5.0,
                    notification_channels=[NotificationChannel.SMS],
                    notification_recipients=[
                        NotificationRecipientTarget(channel=NotificationChannel.SMS, value="+1 (555) 123-4567"),
                    ],
                ),
                accessible_device_ids=["P1"],
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "FEATURE_DISABLED"
    assert exc_info.value.detail["required_entitlement"] == "notification_sms"


@pytest.mark.asyncio
async def test_rule_service_rejects_whatsapp_without_premium_notification_entitlement(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        with pytest.raises(HTTPException) as exc_info:
            await service.create_rule(
                RuleCreate(
                    tenant_id="TENANT-A",
                    rule_name="WhatsApp Blocked",
                    scope=RuleScope.SELECTED_DEVICES,
                    device_ids=["P1"],
                    property="power",
                    condition=ConditionOperator.GREATER_THAN,
                    threshold=5.0,
                    notification_channels=[NotificationChannel.WHATSAPP],
                    notification_recipients=[
                        NotificationRecipientTarget(channel=NotificationChannel.WHATSAPP, value="+1 (555) 987-6543"),
                    ],
                ),
                accessible_device_ids=["P1"],
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "FEATURE_DISABLED"
    assert exc_info.value.detail["required_entitlement"] == "notification_whatsapp"


@pytest.mark.asyncio
async def test_rule_service_accepts_sms_with_premium_notification_entitlement(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx(premium_feature_grants=["notification_sms"]))
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="SMS Allowed",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["P1"],
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=5.0,
                notification_channels=[NotificationChannel.SMS],
                notification_recipients=[
                    NotificationRecipientTarget(channel=NotificationChannel.SMS, value="9876543210"),
                ],
            ),
            accessible_device_ids=["P1"],
        )

    assert created.notification_channels == ["sms"]
    assert created.notification_recipients == [{"channel": "sms", "value": "+919876543210"}]


@pytest.mark.asyncio
async def test_rule_service_accepts_whatsapp_with_premium_notification_entitlement(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx(premium_feature_grants=["notification_whatsapp"]))
        created = await service.create_rule(
            RuleCreate(
                tenant_id="TENANT-A",
                rule_name="WhatsApp Allowed",
                scope=RuleScope.SELECTED_DEVICES,
                device_ids=["P1"],
                property="power",
                condition=ConditionOperator.GREATER_THAN,
                threshold=5.0,
                notification_channels=[NotificationChannel.WHATSAPP],
                notification_recipients=[
                    NotificationRecipientTarget(channel=NotificationChannel.WHATSAPP, value="9876543210"),
                ],
            ),
            accessible_device_ids=["P1"],
        )

    assert created.notification_channels == ["whatsapp"]
    assert created.notification_recipients == [{"channel": "whatsapp", "value": "+919876543210"}]


@pytest.mark.asyncio
async def test_rule_service_rejects_sms_update_without_premium_notification_entitlement(session_factory):
    async with session_factory() as session:
        session.add(
            Rule(
                rule_id="rule-managed",
                tenant_id="TENANT-A",
                rule_name="Managed Rule",
                scope=RuleScope.SELECTED_DEVICES.value,
                device_ids=["P1"],
                property="power",
                condition=">",
                threshold=9.0,
                status=RuleStatus.ACTIVE.value,
                notification_channels=["email"],
                notification_recipients=[{"channel": "email", "value": "ops@planta.com"}],
            )
        )
        await session.commit()

        service = RuleService(session, _ctx())
        with pytest.raises(HTTPException) as exc_info:
            await service.update_rule(
                "rule-managed",
                RuleUpdate(
                    notification_channels=[NotificationChannel.EMAIL, NotificationChannel.SMS],
                    notification_recipients=[
                        NotificationRecipientTarget(channel=NotificationChannel.EMAIL, value="ops@planta.com"),
                        NotificationRecipientTarget(channel=NotificationChannel.SMS, value="+1 (555) 123-4567"),
                    ],
                ),
                accessible_device_ids=["P1"],
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["required_entitlement"] == "notification_sms"


@pytest.mark.asyncio
async def test_rule_service_rejects_selected_devices_outside_scope(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        with pytest.raises(PermissionError, match="Selected devices must belong to your assigned plants"):
            await service.create_rule(
                RuleCreate(
                    tenant_id="TENANT-A",
                    rule_name="Out Of Scope",
                    scope=RuleScope.SELECTED_DEVICES,
                    device_ids=["P2"],
                    property="power",
                    condition=ConditionOperator.GREATER_THAN,
                    threshold=5.0,
                    notification_channels=[NotificationChannel.EMAIL],
                ),
                accessible_device_ids=["P1"],
            )


@pytest.mark.asyncio
async def test_rule_service_blocks_duplicate_active_rule_creation(session_factory):
    async with session_factory() as session:
        service = RuleService(session, _ctx())
        payload = RuleCreate(
            tenant_id="TENANT-A",
            rule_name="Duplicate Guard",
            scope=RuleScope.SELECTED_DEVICES,
            device_ids=["P1"],
            property="power",
            condition=ConditionOperator.GREATER_THAN,
            threshold=5.0,
            notification_channels=[NotificationChannel.EMAIL],
        )

        created = await service.create_rule(payload, accessible_device_ids=["P1"])
        await session.commit()

        with pytest.raises(DuplicateRuleError, match="An identical active rule already exists"):
            await service.create_rule(payload, accessible_device_ids=["P1"])

        repo = RuleRepository(session, _ctx())
        listed, total = await repo.list_rules(accessible_device_ids=["P1"], page=1, page_size=20)

    assert created.rule_name == "Duplicate Guard"
    assert total == 1
    assert [row.rule_name for row in listed] == ["Duplicate Guard"]


@pytest.mark.asyncio
async def test_rule_service_hides_org_wide_all_devices_rule_from_plant_scoped_updates(session_factory):
    async with session_factory() as session:
        session.add(
            Rule(
                rule_id="rule-org-wide",
                tenant_id="TENANT-A",
                rule_name="Org Wide",
                scope=RuleScope.ALL_DEVICES.value,
                device_ids=[],
                property="power",
                condition=">",
                threshold=12.0,
                status=RuleStatus.ACTIVE.value,
                notification_channels=["email"],
            )
        )
        await session.commit()

        service = RuleService(session, _ctx())
        updated = await service.update_rule(
            "rule-org-wide",
            RuleUpdate(rule_name="Blocked update"),
            accessible_device_ids=["P1"],
        )

    assert updated is None


@pytest.mark.asyncio
async def test_rule_service_converts_scoped_update_all_devices_to_accessible_devices(session_factory):
    async with session_factory() as session:
        session.add(
            Rule(
                rule_id="rule-managed",
                tenant_id="TENANT-A",
                rule_name="Managed Rule",
                scope=RuleScope.SELECTED_DEVICES.value,
                device_ids=["P1"],
                property="power",
                condition=">",
                threshold=9.0,
                status=RuleStatus.ACTIVE.value,
                notification_channels=["email"],
            )
        )
        await session.commit()

        service = RuleService(session, _ctx())
        updated = await service.update_rule(
            "rule-managed",
            RuleUpdate(scope=RuleScope.ALL_DEVICES),
            accessible_device_ids=["P1", "P3"],
        )

    assert updated is not None
    assert updated.scope == RuleScope.ALL_DEVICES.value
    assert updated.device_ids == ["P1", "P3"]


@pytest.mark.asyncio
async def test_rule_service_updates_structured_notification_recipients(session_factory):
    async with session_factory() as session:
        session.add(
            Rule(
                rule_id="rule-managed",
                tenant_id="TENANT-A",
                rule_name="Managed Rule",
                scope=RuleScope.SELECTED_DEVICES.value,
                device_ids=["P1"],
                property="power",
                condition=">",
                threshold=9.0,
                status=RuleStatus.ACTIVE.value,
                notification_channels=["email"],
                notification_recipients=[{"channel": "email", "value": "old@planta.com"}],
            )
        )
        await session.commit()

        service = RuleService(session, _ctx())
        updated = await service.update_rule(
            "rule-managed",
            RuleUpdate(
                notification_recipients=[
                    NotificationRecipientTarget(channel=NotificationChannel.EMAIL, value="new@planta.com"),
                    NotificationRecipientTarget(channel=NotificationChannel.EMAIL, value="shiftlead@planta.com"),
                ]
            ),
            accessible_device_ids=["P1"],
        )

    assert updated is not None
    assert updated.notification_recipients == [
        {"channel": "email", "value": "new@planta.com"},
        {"channel": "email", "value": "shiftlead@planta.com"},
    ]


@pytest.mark.asyncio
async def test_rule_visibility_uses_plant_scope_not_creator_scope(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                Rule(
                    rule_id="rule-p1-shared",
                    tenant_id="TENANT-A",
                    rule_name="Plant One Shared Rule",
                    scope=RuleScope.SELECTED_DEVICES.value,
                    device_ids=["P1"],
                    property="power",
                    condition=">",
                    threshold=8.0,
                    status=RuleStatus.ACTIVE.value,
                    notification_channels=["email"],
                    notification_recipients=[{"channel": "email", "value": "ops-a@plant.com"}],
                ),
                Rule(
                    rule_id="rule-p2-hidden",
                    tenant_id="TENANT-A",
                    rule_name="Plant Two Hidden Rule",
                    scope=RuleScope.SELECTED_DEVICES.value,
                    device_ids=["P2"],
                    property="power",
                    condition=">",
                    threshold=9.0,
                    status=RuleStatus.ACTIVE.value,
                    notification_channels=["email"],
                    notification_recipients=[{"channel": "email", "value": "ops-b@plant.com"}],
                ),
            ]
        )
        await session.commit()

        operator_repo = RuleRepository(
            session,
            _ctx(role="operator", user_id="operator-a", plant_ids=["PLANT-1"]),
        )
        operator_rows, operator_total = await operator_repo.list_rules(
            accessible_device_ids=["P1"],
            page=1,
            page_size=20,
        )
        visible_to_operator = await operator_repo.get_by_id("rule-p1-shared", accessible_device_ids=["P1"])
        hidden_from_operator = await operator_repo.get_by_id("rule-p2-hidden", accessible_device_ids=["P1"])

        manager_repo = RuleRepository(
            session,
            _ctx(role="plant_manager", user_id="pm-ab", plant_ids=["PLANT-1", "PLANT-2"]),
        )
        manager_rows, manager_total = await manager_repo.list_rules(
            accessible_device_ids=["P1", "P2"],
            page=1,
            page_size=20,
        )

        admin_repo = RuleRepository(
            session,
            _ctx(role="org_admin", user_id="org-admin", plant_ids=[]),
        )
        admin_rows, admin_total = await admin_repo.list_rules(
            accessible_device_ids=["P1", "P2"],
            page=1,
            page_size=20,
        )

    assert operator_total == 1
    assert [row.rule_id for row in operator_rows] == ["rule-p1-shared"]
    assert visible_to_operator is not None
    assert visible_to_operator.notification_recipients == [{"channel": "email", "value": "ops-a@plant.com"}]
    assert hidden_from_operator is None

    assert manager_total == 2
    assert [row.rule_id for row in manager_rows] == ["rule-p1-shared", "rule-p2-hidden"]

    assert admin_total == 2
    assert [row.rule_id for row in admin_rows] == ["rule-p1-shared", "rule-p2-hidden"]


@pytest.mark.asyncio
async def test_alert_visibility_is_plant_scoped_across_roles(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                Alert(
                    alert_id="alert-p1",
                    tenant_id="TENANT-A",
                    rule_id="rule-p1",
                    device_id="P1",
                    severity="high",
                    message="Plant 1 alert",
                    actual_value=10.0,
                    threshold_value=5.0,
                    status="open",
                ),
                Alert(
                    alert_id="alert-p2",
                    tenant_id="TENANT-A",
                    rule_id="rule-p2",
                    device_id="P2",
                    severity="high",
                    message="Plant 2 alert",
                    actual_value=10.0,
                    threshold_value=5.0,
                    status="open",
                ),
                Alert(
                    alert_id="alert-p3",
                    tenant_id="TENANT-A",
                    rule_id="rule-p3",
                    device_id="P3",
                    severity="high",
                    message="Plant 3 alert",
                    actual_value=10.0,
                    threshold_value=5.0,
                    status="open",
                ),
            ]
        )
        await session.commit()

        operator_repo = AlertRepository(session, _ctx(role="operator", user_id="operator-a", plant_ids=["PLANT-1"]))
        operator_alerts, operator_total = await operator_repo.list_alerts(
            accessible_device_ids=["P1"],
            page=1,
            page_size=20,
        )

        manager_repo = AlertRepository(
            session,
            _ctx(role="plant_manager", user_id="pm-ab", plant_ids=["PLANT-1", "PLANT-2"]),
        )
        manager_alerts, manager_total = await manager_repo.list_alerts(
            accessible_device_ids=["P1", "P2"],
            page=1,
            page_size=20,
        )

        admin_repo = AlertRepository(session, _ctx(role="org_admin", user_id="org-admin", plant_ids=[]))
        admin_alerts, admin_total = await admin_repo.list_alerts(
            accessible_device_ids=["P1", "P2", "P3"],
            page=1,
            page_size=20,
        )

    assert operator_total == 1
    assert [row.alert_id for row in operator_alerts] == ["alert-p1"]

    assert manager_total == 2
    assert sorted(row.alert_id for row in manager_alerts) == ["alert-p1", "alert-p2"]

    assert admin_total == 3
    assert sorted(row.alert_id for row in admin_alerts) == ["alert-p1", "alert-p2", "alert-p3"]
