from datetime import datetime, timedelta, timezone

from src.services.wastage_calculators import (
    calculate_offhours,
    calculate_overconsumption,
    calculate_unoccupied,
)


def _rows(start: datetime, count: int, minutes: int = 1, **fields):
    out = []
    for i in range(count):
        r = {"timestamp": start + timedelta(minutes=i * minutes)}
        r.update(fields)
        out.append(r)
    return out


def test_offhours_same_day_shift_counts_outside_only():
    start = datetime(2026, 3, 10, 7, 0, tzinfo=timezone.utc)
    rows = _rows(start, 4, power=1000)  # 1kW
    # 07:00,07:01,07:02,07:03 with shift 07:02-07:03 means first two outside
    res = calculate_offhours(rows, "07:02", "07:03", idle_threshold=1.0, tariff_rate=10.0)
    assert res.duration_sec is not None and res.duration_sec > 0
    assert res.kwh is not None and res.kwh > 0
    assert res.cost is not None and res.cost > 0


def test_offhours_below_threshold_outside_shift_is_counted():
    start = datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)
    rows = _rows(start, 3, current=0.7, voltage=231.0, power=160.0)
    res = calculate_offhours(rows, "09:00", "17:00", idle_threshold=1.0, tariff_rate=10.0)
    assert res.duration_sec is not None and res.duration_sec > 0
    assert res.kwh is not None and res.kwh > 0
    assert res.cost is not None and res.cost > 0


def test_offhours_no_shift_skipped():
    start = datetime(2026, 3, 10, 7, 0, tzinfo=timezone.utc)
    rows = _rows(start, 3, power=1000)
    res = calculate_offhours(rows, None, None, idle_threshold=1.0, tariff_rate=10.0)
    assert res.skipped_reason is not None
    assert res.kwh is None


def test_offhours_uses_multiple_shifts():
    start = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
    rows = _rows(start, 3, power=1000)  # 09:00, 09:01, 09:02
    shifts = [
        {"day_of_week": start.weekday(), "shift_start": "08:00", "shift_end": "08:30"},
        {"day_of_week": start.weekday(), "shift_start": "09:00", "shift_end": "09:30"},
    ]
    res = calculate_offhours(rows, None, None, idle_threshold=1.0, tariff_rate=10.0, shifts=shifts)
    assert res.kwh == 0.0
    assert res.duration_sec == 0


def test_overconsumption_voltage_path():
    start = datetime(2026, 3, 10, 7, 0, tzinfo=timezone.utc)
    rows = _rows(start, 3, current=10.0, voltage=230.0)
    res = calculate_overconsumption(rows, overconsumption_threshold=5.0, tariff_rate=9.0)
    assert res.duration_sec is not None and res.duration_sec > 0
    assert res.kwh is not None and res.kwh > 0
    assert res.cost is not None and res.cost > 0


def test_overconsumption_threshold_missing_skipped():
    start = datetime(2026, 3, 10, 7, 0, tzinfo=timezone.utc)
    rows = _rows(start, 3, current=10.0, voltage=230.0)
    res = calculate_overconsumption(rows, overconsumption_threshold=None, tariff_rate=9.0)
    assert res.skipped_reason is not None


def test_offhours_watts_value_is_normalized_to_kw():
    start = datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc)
    # 250 watts for 3 one-minute intervals => roughly 0.0083 kWh
    rows = _rows(start, 3, power=250.0)
    res = calculate_offhours(rows, "08:00", "18:00", idle_threshold=1.0, tariff_rate=10.0)
    assert res.kwh is not None
    assert res.kwh < 0.02
    assert res.cost is not None
    assert res.cost < 0.2


def test_unoccupied_night_without_shift():
    start = datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc)
    rows = _rows(start, 4, power=1000)
    res = calculate_unoccupied(
        rows,
        running_current_threshold=1.0,
        tariff_rate=8.0,
        weekday_window=("23:00", "05:00"),
        weekend_window=("23:00", "05:00"),
        config_source="site_default",
    )
    assert res.duration_sec is not None and res.duration_sec > 0
    assert res.kwh is not None and res.kwh > 0


def test_unoccupied_overnight_shift_skipped():
    start = datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc)
    rows = _rows(start, 4, power=1000)
    shifts = [{"day_of_week": start.weekday(), "shift_start": "22:00", "shift_end": "06:00"}]
    res = calculate_unoccupied(
        rows,
        running_current_threshold=1.0,
        tariff_rate=8.0,
        shifts=shifts,
        weekday_window=("23:00", "05:00"),
        weekend_window=("23:00", "05:00"),
        config_source="site_default",
    )
    assert res.skipped_reason is not None


def test_gap_over_15_minutes_is_skipped():
    start = datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc)
    rows = [
        {"timestamp": start, "power": 1000},
        {"timestamp": start + timedelta(minutes=30), "power": 1000},
    ]
    res = calculate_unoccupied(
        rows,
        running_current_threshold=1.0,
        tariff_rate=8.0,
        weekday_window=("23:00", "05:00"),
        weekend_window=("23:00", "05:00"),
        config_source="site_default",
    )
    # first row has default 1 minute interval, second is skipped due gap
    assert res.duration_sec in (60, 0)


def test_unoccupied_missing_config_skipped():
    start = datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc)
    rows = _rows(start, 2, power=1000)
    res = calculate_unoccupied(
        rows,
        running_current_threshold=1.0,
        tariff_rate=8.0,
        weekday_window=None,
        weekend_window=None,
        config_source=None,
    )
    assert res.skipped_reason == "Unoccupied window not configured"


def test_unoccupied_uses_weekend_window():
    # Saturday 14-Mar-2026 01:00 UTC
    start = datetime(2026, 3, 14, 1, 0, tzinfo=timezone.utc)
    rows = _rows(start, 3, power=1000)
    res = calculate_unoccupied(
        rows,
        running_current_threshold=1.0,
        tariff_rate=8.0,
        weekday_window=("23:00", "00:00"),
        weekend_window=("00:00", "05:00"),
        config_source="site_default",
    )
    assert res.duration_sec is not None and res.duration_sec > 0
    assert res.kwh is not None and res.kwh > 0
