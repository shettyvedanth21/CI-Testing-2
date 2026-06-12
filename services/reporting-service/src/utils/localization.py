from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings


def get_platform_tz() -> ZoneInfo:
    return ZoneInfo(settings.PLATFORM_TIMEZONE)


def local_date_bounds_to_utc(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """Convert inclusive platform-local calendar dates into UTC datetimes."""
    platform_tz = get_platform_tz()
    start_local = datetime.combine(start_date, time.min, tzinfo=platform_tz)
    end_local = datetime.combine(end_date, time.max, tzinfo=platform_tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def format_platform_timestamp(value: Any, fallback: str = "N/A", include_tz: bool = True) -> str:
    if not value:
        return fallback
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        local_dt = dt.astimezone(get_platform_tz())
        formatted = local_dt.strftime("%d %b %Y, %I:%M %p")
        return f"{formatted} {get_platform_tz().key}" if include_tz else formatted
    except Exception:
        return str(value)


def currency_symbol(currency: str | None) -> str:
    normalized = (currency or "").upper()
    return {
        "INR": "Rs.",
        "USD": "$",
        "EUR": "EUR",
        "GBP": "GBP",
    }.get(normalized, normalized or "")
