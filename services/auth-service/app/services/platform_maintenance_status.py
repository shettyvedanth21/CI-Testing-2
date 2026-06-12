from __future__ import annotations

from datetime import datetime, timezone

from app.models.auth import PlatformMaintenanceAnnouncement, PlatformMaintenanceStatus


UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def compute_platform_maintenance_effective_status(
    announcement: PlatformMaintenanceAnnouncement,
    *,
    now: datetime | None = None,
) -> PlatformMaintenanceStatus:
    resolved_now = _as_utc_datetime(now or utcnow())
    starts_at = _as_utc_datetime(announcement.starts_at)
    ends_at = _as_utc_datetime(announcement.ends_at)
    if announcement.status == PlatformMaintenanceStatus.CANCELLED:
        return PlatformMaintenanceStatus.CANCELLED
    if announcement.status == PlatformMaintenanceStatus.COMPLETED:
        return PlatformMaintenanceStatus.COMPLETED
    if ends_at <= resolved_now:
        return PlatformMaintenanceStatus.COMPLETED
    if starts_at <= resolved_now < ends_at:
        return PlatformMaintenanceStatus.ACTIVE
    if announcement.status == PlatformMaintenanceStatus.ACTIVE and starts_at > resolved_now:
        return PlatformMaintenanceStatus.SCHEDULED
    return announcement.status
