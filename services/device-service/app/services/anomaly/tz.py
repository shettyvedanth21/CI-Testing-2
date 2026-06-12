"""Platform timezone helpers for anomaly aggregation.

All anomaly date-window calculations must use these helpers instead of
date.today() or datetime.now(timezone.utc).date() so that IST business
days are respected.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings


def get_platform_tz() -> ZoneInfo:
    return ZoneInfo(settings.PLATFORM_TIMEZONE)


def local_today() -> date:
    return datetime.now(timezone.utc).astimezone(get_platform_tz()).date()
