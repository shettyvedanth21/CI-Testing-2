from __future__ import annotations

import csv
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = PROJECT_ROOT / "services" / "rule-engine-service"
SERVICES_DIR = PROJECT_ROOT / "services"
for path in (PROJECT_ROOT, SERVICE_ROOT, SERVICES_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.api.v1.admin_notification_usage import (
    export_notification_usage_csv,
    get_notification_usage_summary,
    list_notification_usage_logs,
)
from app.database import Base
from app.models.rule import NotificationDeliveryLog
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


def _request_for(ctx: TenantContext) -> Request:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/api/v1/admin/notification-usage",
            "raw_path": b"/api/v1/admin/notification-usage",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    request.state.tenant_context = ctx
    return request


def _super_admin_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=None,
        user_id="super-admin-1",
        role="super_admin",
        plant_ids=[],
        is_super_admin=True,
    )


def _org_admin_ctx() -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
        user_id="org-admin-1",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )


def _seed_rows() -> list[NotificationDeliveryLog]:
    return [
        NotificationDeliveryLog(
            id="1",
            tenant_id="TENANT-A",
            rule_id="rule-1",
            alert_id="alert-1",
            device_id="DEV-1",
            event_type="threshold_alert",
            channel="email",
            recipient_masked="o***@example.com",
            recipient_hash="h1",
            provider_name="smtp",
            provider_message_id="MSG-EMAIL-1",
            status="provider_accepted",
            billable_units=1,
            attempted_at=datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc),
            accepted_at=datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc),
            delivered_at=None,
            failed_at=None,
            failure_code=None,
            failure_message=None,
            metadata_json={"safe": True},
            created_at=datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc),
        ),
        NotificationDeliveryLog(
            id="2",
            tenant_id="TENANT-A",
            rule_id="rule-2",
            alert_id="alert-2",
            device_id="DEV-2",
            event_type="threshold_alert",
            channel="sms",
            recipient_masked="******3456",
            recipient_hash="h2",
            provider_name="twilio",
            provider_message_id="MSG-SMS-1",
            status="delivered",
            billable_units=1,
            attempted_at=datetime(2026, 4, 2, 2, 0, tzinfo=timezone.utc),
            accepted_at=datetime(2026, 4, 2, 2, 1, tzinfo=timezone.utc),
            delivered_at=datetime(2026, 4, 2, 2, 2, tzinfo=timezone.utc),
            failed_at=None,
            failure_code=None,
            failure_message=None,
            metadata_json={"safe": True},
            created_at=datetime(2026, 4, 2, 2, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 2, 2, 2, tzinfo=timezone.utc),
        ),
        NotificationDeliveryLog(
            id="3",
            tenant_id="TENANT-A",
            rule_id="rule-2",
            alert_id="alert-3",
            device_id="DEV-2",
            event_type="threshold_alert",
            channel="whatsapp",
            recipient_masked="whatsapp:******7890",
            recipient_hash="h3",
            provider_name="twilio",
            provider_message_id="MSG-WA-1",
            status="failed",
            billable_units=0,
            attempted_at=datetime(2026, 4, 3, 3, 0, tzinfo=timezone.utc),
            accepted_at=None,
            delivered_at=None,
            failed_at=datetime(2026, 4, 3, 3, 1, tzinfo=timezone.utc),
            failure_code="63016",
            failure_message="Template rejected",
            metadata_json={"safe": True},
            created_at=datetime(2026, 4, 3, 3, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 3, 3, 1, tzinfo=timezone.utc),
        ),
        NotificationDeliveryLog(
            id="4",
            tenant_id="TENANT-A",
            rule_id="rule-3",
            alert_id="alert-4",
            device_id="DEV-3",
            event_type="threshold_alert",
            channel="sms",
            recipient_masked="******9999",
            recipient_hash="h4",
            provider_name="twilio",
            provider_message_id=None,
            status="skipped",
            billable_units=0,
            attempted_at=datetime(2026, 4, 4, 4, 0, tzinfo=timezone.utc),
            accepted_at=None,
            delivered_at=None,
            failed_at=None,
            failure_code="channel_disabled",
            failure_message="Disabled",
            metadata_json={"safe": True},
            created_at=datetime(2026, 4, 4, 4, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 4, 4, 0, tzinfo=timezone.utc),
        ),
        NotificationDeliveryLog(
            id="5",
            tenant_id="TENANT-A",
            rule_id="rule-1",
            alert_id="alert-5",
            device_id="DEV-1",
            event_type="rule_created",
            channel="email",
            recipient_masked="a***@example.com",
            recipient_hash="h5",
            provider_name="smtp",
            provider_message_id="MSG-EMAIL-2",
            status="attempted",
            billable_units=0,
            attempted_at=datetime(2026, 4, 5, 5, 0, tzinfo=timezone.utc),
            accepted_at=None,
            delivered_at=None,
            failed_at=None,
            failure_code=None,
            failure_message=None,
            metadata_json={"safe": True},
            created_at=datetime(2026, 4, 5, 5, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 5, 5, 0, tzinfo=timezone.utc),
        ),
        NotificationDeliveryLog(
            id="6",
            tenant_id="TENANT-A",
            rule_id="rule-1",
            alert_id="alert-6",
            device_id="DEV-1",
            event_type="threshold_alert",
            channel="email",
            recipient_masked="o***@example.com",
            recipient_hash="h6",
            provider_name="smtp",
            provider_message_id="MSG-OUTSIDE-MONTH",
            status="provider_accepted",
            billable_units=1,
            attempted_at=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
            accepted_at=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
            delivered_at=None,
            failed_at=None,
            failure_code=None,
            failure_message=None,
            metadata_json={"safe": True},
            created_at=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
        ),
        NotificationDeliveryLog(
            id="7",
            tenant_id="TENANT-B",
            rule_id="rule-9",
            alert_id="alert-9",
            device_id="DEV-9",
            event_type="threshold_alert",
            channel="sms",
            recipient_masked="******1111",
            recipient_hash="h7",
            provider_name="twilio",
            provider_message_id="MSG-TENANT-B",
            status="delivered",
            billable_units=1,
            attempted_at=datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc),
            accepted_at=datetime(2026, 4, 10, 0, 1, tzinfo=timezone.utc),
            delivered_at=datetime(2026, 4, 10, 0, 2, tzinfo=timezone.utc),
            failed_at=None,
            failure_code=None,
            failure_message=None,
            metadata_json={"safe": True},
            created_at=datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 10, 0, 2, tzinfo=timezone.utc),
        ),
    ]


async def _response_body_text(response: Any) -> str:
    if hasattr(response, "body") and response.body:
        return response.body.decode("utf-8")
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
    return b"".join(chunks).decode("utf-8")


@pytest.mark.asyncio
async def test_super_admin_can_access_summary_endpoint(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        response = await get_notification_usage_summary(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            db=session,
        )

    assert response.tenant_id == "TENANT-A"
    assert response.month == "2026-04"
    assert response.totals.attempted_count == 5


@pytest.mark.asyncio
async def test_non_super_admin_cannot_access_summary_endpoint(session_factory):
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await get_notification_usage_summary(
                tenant_id="TENANT-A",
                request=_request_for(_org_admin_ctx()),
                month="2026-04",
                db=session,
            )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "SUPER_ADMIN_REQUIRED"


@pytest.mark.asyncio
async def test_super_admin_can_access_detailed_logs_endpoint(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        response = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            page=1,
            page_size=50,
            db=session,
        )

    assert response.total == 5
    assert len(response.data) == 5
    assert response.data[0].attempted_at <= response.data[-1].attempted_at


@pytest.mark.asyncio
async def test_non_super_admin_cannot_access_detailed_logs_endpoint(session_factory):
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await list_notification_usage_logs(
                tenant_id="TENANT-A",
                request=_request_for(_org_admin_ctx()),
                month="2026-04",
                db=session,
            )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "SUPER_ADMIN_REQUIRED"


@pytest.mark.asyncio
async def test_monthly_summary_aggregation_is_correct_across_channels(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        response = await get_notification_usage_summary(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            db=session,
        )

    assert response.by_channel["email"].attempted_count == 2
    assert response.by_channel["sms"].attempted_count == 2
    assert response.by_channel["whatsapp"].attempted_count == 1


@pytest.mark.asyncio
async def test_failed_and_skipped_rows_counted_correctly_and_billable_sum_is_exact(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        response = await get_notification_usage_summary(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            db=session,
        )

    assert response.totals.failed_count == 1
    assert response.totals.skipped_count == 1
    assert response.totals.billable_count == 2


@pytest.mark.asyncio
async def test_filters_channel_status_rule_and_device_work(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        channel_filtered = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            channel="sms",
            db=session,
        )
        status_filtered = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            status_value="failed",
            db=session,
        )
        rule_filtered = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            rule_id="rule-1",
            db=session,
        )
        device_filtered = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            device_id="DEV-2",
            db=session,
        )

    assert {row.channel for row in channel_filtered.data} == {"sms"}
    assert [row.status for row in status_filtered.data] == ["failed"]
    assert {row.rule_id for row in rule_filtered.data} == {"rule-1"}
    assert {row.device_id for row in device_filtered.data} == {"DEV-2"}


@pytest.mark.asyncio
async def test_csv_export_matches_filtered_logs_and_is_deterministic(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        logs_response = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            channel="sms",
            db=session,
        )
        csv_response = await export_notification_usage_csv(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            channel="sms",
            db=session,
        )

    csv_text = await _response_body_text(csv_response)
    csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(csv_rows) == len(logs_response.data)
    assert [row["Attempted At"] for row in csv_rows] == [
        entry.attempted_at.astimezone(timezone.utc).isoformat() for entry in logs_response.data
    ]
    assert csv_response.headers["Content-Disposition"].startswith('attachment; filename="notification_usage_TENANT-A_2026-04_sms_')
    assert csv_response.headers["Content-Disposition"].endswith('.csv"')
    assert csv_response.headers["X-Notification-Usage-Month"] == "2026-04"
    assert "X-Export-Generated-At" in csv_response.headers


@pytest.mark.asyncio
async def test_masked_recipient_exposed_and_raw_not_exposed(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        logs_response = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            db=session,
        )
        csv_response = await export_notification_usage_csv(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            db=session,
        )

    first_row = logs_response.data[0]
    assert first_row.recipient_masked in {"o***@example.com", "******3456", "whatsapp:******7890", "******9999", "a***@example.com"}
    assert not hasattr(first_row, "recipient")
    assert "ops@example.com" not in await _response_body_text(csv_response)


@pytest.mark.asyncio
async def test_invalid_month_is_rejected_cleanly(session_factory):
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await get_notification_usage_summary(
                tenant_id="TENANT-A",
                request=_request_for(_super_admin_ctx()),
                month="2026-4",
                db=session,
            )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "INVALID_MONTH"


@pytest.mark.asyncio
async def test_date_range_and_search_filters_limit_logs_and_summary(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        logs_response = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            date_from=datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc),
            date_to=datetime(2026, 4, 3, 23, 59, tzinfo=timezone.utc),
            search="MSG-SMS-1",
            db=session,
        )
        summary_response = await get_notification_usage_summary(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            date_from=datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc),
            date_to=datetime(2026, 4, 3, 23, 59, tzinfo=timezone.utc),
            search="MSG-SMS-1",
            db=session,
        )

    assert logs_response.total == 1
    assert [row.provider_message_id for row in logs_response.data] == ["MSG-SMS-1"]
    assert summary_response.totals.attempted_count == 1
    assert summary_response.totals.billable_count == 1


@pytest.mark.asyncio
async def test_include_metadata_flag_is_opt_in_for_logs(session_factory):
    async with session_factory() as session:
        session.add_all(_seed_rows())
        await session.commit()

        without_metadata = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            include_metadata=False,
            db=session,
        )
        with_metadata = await list_notification_usage_logs(
            tenant_id="TENANT-A",
            request=_request_for(_super_admin_ctx()),
            month="2026-04",
            include_metadata=True,
            db=session,
        )

    assert without_metadata.data[0].metadata_json is None
    assert with_metadata.data[0].metadata_json == {"safe": True}
