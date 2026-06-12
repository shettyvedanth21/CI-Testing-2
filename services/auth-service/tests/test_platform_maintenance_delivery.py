import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, SERVICES_ROOT, AUTH_SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(AUTH_SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.database import Base
from app.models.auth import (
    Organization,
    PlatformMaintenanceAnnouncement,
    PlatformMaintenanceAnnouncementTarget,
    PlatformMaintenanceEmailDelivery,
    PlatformMaintenanceEmailDeliveryStatus,
    PlatformMaintenanceSeverity,
    PlatformMaintenanceStatus,
    User,
    UserRole,
)
from app.repositories.platform_maintenance_repository import PlatformMaintenanceRepository
from app.services.mailer_service import mailer_svc
from app.services.platform_maintenance_delivery import PlatformMaintenanceDeliveryService
from app.config import settings


UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


def _make_org(*, tenant_id: str, is_active: bool = True) -> Organization:
    now = _now()
    return Organization(
        id=tenant_id,
        name=f"Org {tenant_id}",
        slug=f"org-{tenant_id.lower()}",
        is_active=is_active,
        entitlements_version=0,
        premium_feature_grants_json=[],
        role_feature_matrix_json={},
        created_at=now,
        updated_at=now,
    )


def _make_user(
    *,
    user_id: str,
    tenant_id: str | None,
    role: UserRole,
    email: str,
    is_active: bool = True,
) -> User:
    now = _now()
    return User(
        id=user_id,
        tenant_id=tenant_id,
        email=email,
        hashed_password="hashed",
        full_name=f"User {user_id}",
        role=role,
        permissions_version=0,
        is_active=is_active,
        activated_at=now,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )


def _make_announcement(
    *,
    announcement_id: str,
    status: PlatformMaintenanceStatus = PlatformMaintenanceStatus.SCHEDULED,
    broadcast_all_tenants: bool = False,
    target_tenant_ids: list[str] | None = None,
    starts_at: datetime | None = None,
    duration_minutes: int = 60,
) -> PlatformMaintenanceAnnouncement:
    now = _now()
    starts = starts_at or (now + timedelta(minutes=30))
    announcement = PlatformMaintenanceAnnouncement(
        id=announcement_id,
        title="Platform maintenance",
        severity=PlatformMaintenanceSeverity.WARNING,
        message="Scheduled platform maintenance.",
        starts_at=starts,
        estimated_duration_minutes=duration_minutes,
        ends_at=starts + timedelta(minutes=duration_minutes),
        status=status,
        broadcast_all_tenants=broadcast_all_tenants,
        created_by_user_id="super-1",
        updated_by_user_id="super-1",
        created_at=now,
        updated_at=now,
    )
    announcement.targets = [
        PlatformMaintenanceAnnouncementTarget(
            id=f"target-{announcement_id}-{tenant_id}",
            announcement_id=announcement_id,
            tenant_id=tenant_id,
            created_at=now,
        )
        for tenant_id in (target_tenant_ids or [])
    ]
    return announcement


def _make_delivery(
    *,
    delivery_id: str,
    announcement_id: str,
    user_id: str,
    tenant_id: str,
    status: PlatformMaintenanceEmailDeliveryStatus,
    recipient_email: str = "user@example.com",
    retry_count: int = 0,
    next_attempt_at: datetime | None = None,
    processing_started_at: datetime | None = None,
) -> PlatformMaintenanceEmailDelivery:
    now = _now()
    return PlatformMaintenanceEmailDelivery(
        id=delivery_id,
        announcement_id=announcement_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_role=UserRole.OPERATOR.value,
        recipient_email=recipient_email,
        recipient_full_name="Recipient",
        status=status,
        retry_count=retry_count,
        next_attempt_at=next_attempt_at or now,
        processing_started_at=processing_started_at,
        created_at=now,
        updated_at=now,
    )


@pytest_asyncio.fixture
async def db_session(tmp_path):
    db_path = tmp_path / "platform-maintenance-delivery.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_announcement_creates_deliveries_for_eligible_target_users_only(db_session):
    service = PlatformMaintenanceDeliveryService()
    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001", is_active=True),
            _make_org(tenant_id="SH00000002", is_active=True),
            _make_org(tenant_id="SH00000003", is_active=False),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            _make_user(user_id="org-admin", tenant_id="SH00000001", role=UserRole.ORG_ADMIN, email="org-admin@example.com"),
            _make_user(user_id="operator", tenant_id="SH00000001", role=UserRole.OPERATOR, email="operator@example.com"),
            _make_user(user_id="viewer", tenant_id="SH00000002", role=UserRole.VIEWER, email="viewer@example.com"),
            _make_user(user_id="inactive", tenant_id="SH00000001", role=UserRole.OPERATOR, email="inactive@example.com", is_active=False),
            _make_user(user_id="bad-email", tenant_id="SH00000001", role=UserRole.PLANT_MANAGER, email="   "),
            _make_user(user_id="inactive-org", tenant_id="SH00000003", role=UserRole.OPERATOR, email="inactive-org@example.com"),
            _make_announcement(
                announcement_id="pm-targeted",
                target_tenant_ids=["SH00000001"],
                broadcast_all_tenants=False,
            ),
        ]
    )
    await db_session.commit()

    announcement = await db_session.scalar(
        select(PlatformMaintenanceAnnouncement)
        .options(selectinload(PlatformMaintenanceAnnouncement.targets))
        .where(PlatformMaintenanceAnnouncement.id == "pm-targeted")
    )
    assert announcement is not None

    await service.sync_announcement(db_session, announcement)
    await db_session.commit()

    deliveries = (
        await db_session.execute(
            select(PlatformMaintenanceEmailDelivery).order_by(PlatformMaintenanceEmailDelivery.user_id.asc())
        )
    ).scalars().all()

    assert [delivery.user_id for delivery in deliveries] == ["operator", "org-admin"]
    assert all(delivery.status == PlatformMaintenanceEmailDeliveryStatus.PENDING for delivery in deliveries)


@pytest.mark.asyncio
async def test_sync_broadcast_announcement_reaches_all_active_orgs_but_excludes_suspended_orgs(db_session):
    service = PlatformMaintenanceDeliveryService()
    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001", is_active=True),
            _make_org(tenant_id="SH00000002", is_active=True),
            _make_org(tenant_id="SH00000003", is_active=False),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            _make_user(user_id="org-1-user", tenant_id="SH00000001", role=UserRole.ORG_ADMIN, email="org-1@example.com"),
            _make_user(user_id="org-2-user", tenant_id="SH00000002", role=UserRole.OPERATOR, email="org-2@example.com"),
            _make_user(user_id="org-3-user", tenant_id="SH00000003", role=UserRole.VIEWER, email="org-3@example.com"),
            _make_announcement(
                announcement_id="pm-broadcast",
                broadcast_all_tenants=True,
                target_tenant_ids=[],
            ),
        ]
    )
    await db_session.commit()

    announcement = await db_session.scalar(
        select(PlatformMaintenanceAnnouncement)
        .options(selectinload(PlatformMaintenanceAnnouncement.targets))
        .where(PlatformMaintenanceAnnouncement.id == "pm-broadcast")
    )
    assert announcement is not None

    await service.sync_announcement(db_session, announcement)
    await db_session.commit()

    deliveries = (
        await db_session.execute(
            select(PlatformMaintenanceEmailDelivery).order_by(PlatformMaintenanceEmailDelivery.user_id.asc())
        )
    ).scalars().all()

    assert [delivery.user_id for delivery in deliveries] == ["org-1-user", "org-2-user"]
    assert {delivery.tenant_id for delivery in deliveries} == {"SH00000001", "SH00000002"}


@pytest.mark.asyncio
async def test_sync_reachable_announcements_cancels_open_rows_for_cancelled_announcement(db_session):
    service = PlatformMaintenanceDeliveryService()
    announcement = _make_announcement(
        announcement_id="pm-cancelled",
        status=PlatformMaintenanceStatus.CANCELLED,
        target_tenant_ids=["SH00000001"],
    )
    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001"),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            announcement,
            _make_delivery(
                delivery_id="delivery-1",
                announcement_id="pm-cancelled",
                user_id="user-1",
                tenant_id="SH00000001",
                status=PlatformMaintenanceEmailDeliveryStatus.PENDING,
            ),
        ]
    )
    await db_session.commit()

    synced = await service.sync_reachable_announcements(db_session)
    await db_session.commit()

    updated_delivery = await db_session.get(PlatformMaintenanceEmailDelivery, "delivery-1")
    assert synced == 1
    assert updated_delivery is not None
    assert updated_delivery.status == PlatformMaintenanceEmailDeliveryStatus.CANCELLED
    assert updated_delivery.failure_code == "ANNOUNCEMENT_NOT_REACHABLE"


@pytest.mark.asyncio
async def test_claim_due_deliveries_recovers_stale_processing_and_skips_max_retry_rows(db_session, monkeypatch):
    service = PlatformMaintenanceDeliveryService()
    now = _now()
    monkeypatch.setattr(settings, "PLATFORM_MAINTENANCE_EMAIL_MAX_RETRIES", 3)
    monkeypatch.setattr(settings, "PLATFORM_MAINTENANCE_EMAIL_BACKOFF_BASE_SECONDS", 60)
    monkeypatch.setattr(settings, "PLATFORM_MAINTENANCE_EMAIL_BACKOFF_MAX_SECONDS", 300)
    monkeypatch.setattr(settings, "PLATFORM_MAINTENANCE_EMAIL_PROCESSING_TIMEOUT_SECONDS", 120)

    announcement = _make_announcement(
        announcement_id="pm-claim",
        target_tenant_ids=["SH00000001"],
    )
    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001"),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            announcement,
            _make_delivery(
                delivery_id="stale-processing",
                announcement_id="pm-claim",
                user_id="user-stale",
                tenant_id="SH00000001",
                status=PlatformMaintenanceEmailDeliveryStatus.PROCESSING,
                processing_started_at=now - timedelta(minutes=10),
                next_attempt_at=now - timedelta(minutes=10),
            ),
            _make_delivery(
                delivery_id="max-retry",
                announcement_id="pm-claim",
                user_id="user-max",
                tenant_id="SH00000001",
                status=PlatformMaintenanceEmailDeliveryStatus.FAILED,
                retry_count=3,
                next_attempt_at=now - timedelta(minutes=1),
            ),
            _make_delivery(
                delivery_id="pending-due",
                announcement_id="pm-claim",
                user_id="user-pending",
                tenant_id="SH00000001",
                status=PlatformMaintenanceEmailDeliveryStatus.PENDING,
                next_attempt_at=now - timedelta(minutes=1),
            ),
        ]
    )
    await db_session.commit()

    claimed = await service.claim_due_deliveries(db_session, limit=10)
    await db_session.commit()

    claimed_ids = [delivery.id for delivery in claimed]
    stale_delivery = await db_session.get(PlatformMaintenanceEmailDelivery, "stale-processing")
    max_retry_delivery = await db_session.get(PlatformMaintenanceEmailDelivery, "max-retry")
    pending_delivery = await db_session.get(PlatformMaintenanceEmailDelivery, "pending-due")

    assert claimed_ids == ["pending-due"]
    assert stale_delivery is not None
    assert stale_delivery.status == PlatformMaintenanceEmailDeliveryStatus.FAILED
    assert stale_delivery.retry_count == 1
    assert stale_delivery.failure_code == "PROCESSING_TIMEOUT"
    assert max_retry_delivery is not None
    assert max_retry_delivery.status == PlatformMaintenanceEmailDeliveryStatus.FAILED
    assert pending_delivery is not None
    assert pending_delivery.status == PlatformMaintenanceEmailDeliveryStatus.PROCESSING


@pytest.mark.asyncio
async def test_send_delivery_marks_sent_when_recipient_is_still_eligible(db_session, monkeypatch):
    service = PlatformMaintenanceDeliveryService()
    send_mock = AsyncMock()
    monkeypatch.setattr(mailer_svc, "send_platform_maintenance_email", send_mock)

    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001"),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            _make_user(user_id="user-1", tenant_id="SH00000001", role=UserRole.OPERATOR, email="user-1@example.com"),
            _make_announcement(
                announcement_id="pm-send",
                target_tenant_ids=["SH00000001"],
            ),
            _make_delivery(
                delivery_id="delivery-send",
                announcement_id="pm-send",
                user_id="user-1",
                tenant_id="SH00000001",
                status=PlatformMaintenanceEmailDeliveryStatus.PROCESSING,
                recipient_email="user-1@example.com",
            ),
        ]
    )
    await db_session.commit()

    delivery = await db_session.scalar(
        select(PlatformMaintenanceEmailDelivery)
        .options(
            selectinload(PlatformMaintenanceEmailDelivery.announcement).selectinload(
                PlatformMaintenanceAnnouncement.targets
            )
        )
        .where(PlatformMaintenanceEmailDelivery.id == "delivery-send")
    )
    assert delivery is not None

    await service.send_delivery(db_session, delivery)
    await db_session.commit()

    updated_delivery = await db_session.get(PlatformMaintenanceEmailDelivery, "delivery-send")
    assert updated_delivery is not None
    assert updated_delivery.status == PlatformMaintenanceEmailDeliveryStatus.SENT
    assert updated_delivery.sent_at is not None
    send_mock.assert_awaited_once()
    assert send_mock.await_args.kwargs["status"] == "scheduled"


@pytest.mark.asyncio
async def test_send_delivery_uses_effective_active_status_for_email_copy(db_session, monkeypatch):
    service = PlatformMaintenanceDeliveryService()
    send_mock = AsyncMock()
    monkeypatch.setattr(mailer_svc, "send_platform_maintenance_email", send_mock)
    active_start = _now() - timedelta(minutes=10)

    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001"),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            _make_user(user_id="user-3", tenant_id="SH00000001", role=UserRole.OPERATOR, email="user-3@example.com"),
            _make_announcement(
                announcement_id="pm-effective-active",
                target_tenant_ids=["SH00000001"],
                starts_at=active_start,
                status=PlatformMaintenanceStatus.SCHEDULED,
                duration_minutes=60,
            ),
            _make_delivery(
                delivery_id="delivery-effective-active",
                announcement_id="pm-effective-active",
                user_id="user-3",
                tenant_id="SH00000001",
                status=PlatformMaintenanceEmailDeliveryStatus.PROCESSING,
                recipient_email="user-3@example.com",
            ),
        ]
    )
    await db_session.commit()

    delivery = await db_session.scalar(
        select(PlatformMaintenanceEmailDelivery)
        .options(
            selectinload(PlatformMaintenanceEmailDelivery.announcement).selectinload(
                PlatformMaintenanceAnnouncement.targets
            )
        )
        .where(PlatformMaintenanceEmailDelivery.id == "delivery-effective-active")
    )
    assert delivery is not None

    await service.send_delivery(db_session, delivery)
    await db_session.commit()

    assert send_mock.await_args.kwargs["status"] == "active"


@pytest.mark.asyncio
async def test_send_delivery_handles_naive_database_timestamps(db_session, monkeypatch):
    service = PlatformMaintenanceDeliveryService()
    send_mock = AsyncMock()
    monkeypatch.setattr(mailer_svc, "send_platform_maintenance_email", send_mock)
    active_start = _now() - timedelta(minutes=10)

    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001"),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            _make_user(user_id="user-4", tenant_id="SH00000001", role=UserRole.OPERATOR, email="user-4@example.com"),
            _make_announcement(
                announcement_id="pm-naive-send",
                target_tenant_ids=["SH00000001"],
                starts_at=active_start,
                status=PlatformMaintenanceStatus.SCHEDULED,
                duration_minutes=60,
            ),
            _make_delivery(
                delivery_id="delivery-naive-send",
                announcement_id="pm-naive-send",
                user_id="user-4",
                tenant_id="SH00000001",
                status=PlatformMaintenanceEmailDeliveryStatus.PROCESSING,
                recipient_email="user-4@example.com",
            ),
        ]
    )
    await db_session.commit()

    delivery = await db_session.scalar(
        select(PlatformMaintenanceEmailDelivery)
        .options(
            selectinload(PlatformMaintenanceEmailDelivery.announcement).selectinload(
                PlatformMaintenanceAnnouncement.targets
            )
        )
        .where(PlatformMaintenanceEmailDelivery.id == "delivery-naive-send")
    )
    assert delivery is not None
    delivery.announcement.starts_at = delivery.announcement.starts_at.replace(tzinfo=None)
    delivery.announcement.ends_at = delivery.announcement.ends_at.replace(tzinfo=None)

    await service.send_delivery(db_session, delivery)
    await db_session.commit()

    updated_delivery = await db_session.get(PlatformMaintenanceEmailDelivery, "delivery-naive-send")
    assert updated_delivery is not None
    assert updated_delivery.status == PlatformMaintenanceEmailDeliveryStatus.SENT
    assert send_mock.await_args.kwargs["status"] == "active"


@pytest.mark.asyncio
async def test_send_delivery_cancels_when_recipient_falls_outside_target_audience(db_session, monkeypatch):
    service = PlatformMaintenanceDeliveryService()
    send_mock = AsyncMock()
    monkeypatch.setattr(mailer_svc, "send_platform_maintenance_email", send_mock)

    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001"),
            _make_org(tenant_id="SH00000002"),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            _make_user(user_id="user-2", tenant_id="SH00000002", role=UserRole.OPERATOR, email="user-2@example.com"),
            _make_announcement(
                announcement_id="pm-audience",
                target_tenant_ids=["SH00000001"],
            ),
            _make_delivery(
                delivery_id="delivery-audience",
                announcement_id="pm-audience",
                user_id="user-2",
                tenant_id="SH00000002",
                status=PlatformMaintenanceEmailDeliveryStatus.PROCESSING,
                recipient_email="user-2@example.com",
            ),
        ]
    )
    await db_session.commit()

    delivery = await db_session.scalar(
        select(PlatformMaintenanceEmailDelivery)
        .options(
            selectinload(PlatformMaintenanceEmailDelivery.announcement).selectinload(
                PlatformMaintenanceAnnouncement.targets
            )
        )
        .where(PlatformMaintenanceEmailDelivery.id == "delivery-audience")
    )
    assert delivery is not None

    await service.send_delivery(db_session, delivery)
    await db_session.commit()

    updated_delivery = await db_session.get(PlatformMaintenanceEmailDelivery, "delivery-audience")
    assert updated_delivery is not None
    assert updated_delivery.status == PlatformMaintenanceEmailDeliveryStatus.CANCELLED
    assert updated_delivery.failure_code == "RECIPIENT_OUTSIDE_TARGET_AUDIENCE"
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_banner_query_excludes_inactive_tenants(db_session):
    repo = PlatformMaintenanceRepository()
    starts_at = _now() - timedelta(minutes=5)
    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001", is_active=False),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            _make_announcement(
                announcement_id="pm-banner-inactive-tenant",
                target_tenant_ids=["SH00000001"],
                starts_at=starts_at,
                status=PlatformMaintenanceStatus.SCHEDULED,
                duration_minutes=60,
            ),
        ]
    )
    await db_session.commit()

    results = await repo.list_current_for_tenant(db_session, "SH00000001", now=_now())

    assert results == []


@pytest.mark.asyncio
async def test_current_banner_query_honors_selected_vs_broadcast_targeting(db_session):
    repo = PlatformMaintenanceRepository()
    starts_at = _now() - timedelta(minutes=10)
    db_session.add_all(
        [
            _make_org(tenant_id="SH00000001", is_active=True),
            _make_org(tenant_id="SH00000002", is_active=True),
            _make_user(user_id="super-1", tenant_id=None, role=UserRole.SUPER_ADMIN, email="super@example.com"),
            _make_announcement(
                announcement_id="pm-selected",
                target_tenant_ids=["SH00000001"],
                starts_at=starts_at,
                status=PlatformMaintenanceStatus.SCHEDULED,
                duration_minutes=60,
            ),
            _make_announcement(
                announcement_id="pm-broadcast-visible",
                broadcast_all_tenants=True,
                target_tenant_ids=[],
                starts_at=starts_at + timedelta(minutes=5),
                status=PlatformMaintenanceStatus.SCHEDULED,
                duration_minutes=60,
            ),
        ]
    )
    await db_session.commit()

    tenant_one_results = await repo.list_current_for_tenant(db_session, "SH00000001", now=_now())
    tenant_two_results = await repo.list_current_for_tenant(db_session, "SH00000002", now=_now())

    assert [announcement.id for announcement in tenant_one_results] == ["pm-selected", "pm-broadcast-visible"]
    assert [announcement.id for announcement in tenant_two_results] == ["pm-broadcast-visible"]
