from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_SERVICES_ROOT = SERVICE_ROOT.parents[1]

sys.path.insert(0, str(SERVICE_ROOT))
sys.path.insert(1, str(REPO_SERVICES_ROOT))

from src.utils.localization import local_date_bounds_to_utc


def test_same_day_range_preserves_platform_local_day_in_utc() -> None:
    start_dt, end_dt = local_date_bounds_to_utc(date(2026, 5, 18), date(2026, 5, 18))

    assert start_dt == datetime(2026, 5, 17, 18, 30, tzinfo=timezone.utc)
    assert end_dt == datetime(2026, 5, 18, 18, 29, 59, 999999, tzinfo=timezone.utc)


def test_multi_day_range_spans_complete_local_days_in_utc() -> None:
    start_dt, end_dt = local_date_bounds_to_utc(date(2026, 5, 18), date(2026, 5, 19))

    assert start_dt == datetime(2026, 5, 17, 18, 30, tzinfo=timezone.utc)
    assert end_dt == datetime(2026, 5, 19, 18, 29, 59, 999999, tzinfo=timezone.utc)
