from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.auth import PlatformMaintenanceStatus
from app.services.platform_maintenance_status import compute_platform_maintenance_effective_status


UTC = timezone.utc


def _announcement(*, status: PlatformMaintenanceStatus, starts_at: datetime, ends_at: datetime):
    return SimpleNamespace(
        status=status,
        starts_at=starts_at,
        ends_at=ends_at,
    )


def test_cancelled_status_wins_over_time_window():
    now = datetime.now(UTC)
    announcement = _announcement(
        status=PlatformMaintenanceStatus.CANCELLED,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
    )

    assert compute_platform_maintenance_effective_status(announcement, now=now) == PlatformMaintenanceStatus.CANCELLED


def test_completed_status_wins_over_time_window():
    now = datetime.now(UTC)
    announcement = _announcement(
        status=PlatformMaintenanceStatus.COMPLETED,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
    )

    assert compute_platform_maintenance_effective_status(announcement, now=now) == PlatformMaintenanceStatus.COMPLETED


def test_future_active_notice_downgrades_to_scheduled():
    now = datetime.now(UTC)
    announcement = _announcement(
        status=PlatformMaintenanceStatus.ACTIVE,
        starts_at=now + timedelta(minutes=30),
        ends_at=now + timedelta(hours=2),
    )

    assert compute_platform_maintenance_effective_status(announcement, now=now) == PlatformMaintenanceStatus.SCHEDULED


def test_expired_scheduled_notice_becomes_completed():
    now = datetime.now(UTC)
    announcement = _announcement(
        status=PlatformMaintenanceStatus.SCHEDULED,
        starts_at=now - timedelta(hours=2),
        ends_at=now - timedelta(minutes=5),
    )

    assert compute_platform_maintenance_effective_status(announcement, now=now) == PlatformMaintenanceStatus.COMPLETED


def test_naive_timestamps_are_normalized_to_utc():
    now = datetime.now(UTC)
    announcement = _announcement(
        status=PlatformMaintenanceStatus.SCHEDULED,
        starts_at=(now - timedelta(minutes=5)).replace(tzinfo=None),
        ends_at=(now + timedelta(minutes=20)).replace(tzinfo=None),
    )

    assert compute_platform_maintenance_effective_status(announcement, now=now) == PlatformMaintenanceStatus.ACTIVE
