from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_SERVICES_ROOT = SERVICE_ROOT.parents[1]

sys.path.insert(0, str(SERVICE_ROOT))
sys.path.insert(1, str(REPO_SERVICES_ROOT))

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from src.handlers.energy_reports import normalize_dates_to_utc, validate_date_duration_seconds


def test_same_day_range_counts_as_full_day_for_report_validation() -> None:
    start_dt, end_dt = normalize_dates_to_utc(date(2026, 4, 9), date(2026, 4, 9))

    assert validate_date_duration_seconds(start_dt, end_dt) is True


def test_sub_day_window_still_fails_report_validation() -> None:
    start_dt, _ = normalize_dates_to_utc(date(2026, 4, 9), date(2026, 4, 9))
    end_dt = start_dt.replace(hour=23, minute=0, second=0)

    assert validate_date_duration_seconds(start_dt, end_dt) is False


def test_same_day_range_preserves_platform_local_day_in_utc() -> None:
    start_dt, end_dt = normalize_dates_to_utc(date(2026, 5, 18), date(2026, 5, 18))

    assert start_dt == datetime(2026, 5, 17, 18, 30, tzinfo=timezone.utc)
    assert end_dt == datetime(2026, 5, 18, 18, 29, 59, 999999, tzinfo=timezone.utc)
