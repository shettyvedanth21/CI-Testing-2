from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings


def get_platform_tz() -> ZoneInfo:
    return ZoneInfo(settings.PLATFORM_TIMEZONE)


def platform_tz_label() -> str:
    return "IST" if settings.PLATFORM_TIMEZONE == "Asia/Kolkata" else settings.PLATFORM_TIMEZONE


def format_platform_datetime(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(get_platform_tz())
    return f"{local_dt.strftime('%Y-%m-%d %H:%M:%S')} {platform_tz_label()}"
