from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from services.shared.energy_accounting import aggregate_window


def _row(ts_utc: datetime, *, current: float = 1.0, voltage: float = 230.0, power_w: float = 1000.0) -> dict[str, object]:
    return {
        "timestamp": ts_utc.isoformat().replace("+00:00", "Z"),
        "current": current,
        "voltage": voltage,
        "power": power_w,
    }


def test_aggregate_window_uses_platform_timezone_and_preserves_loss_components() -> None:
    platform_tz = ZoneInfo("Asia/Kolkata")
    local_shift = [
        {
            "shift_start": "00:00",
            "shift_end": "01:00",
            "is_active": True,
        }
    ]

    start_idle = datetime(2026, 3, 30, 18, 35, tzinfo=ZoneInfo("UTC"))  # 2026-03-31 00:05 IST
    start_offhours = datetime(2026, 3, 30, 19, 35, tzinfo=ZoneInfo("UTC"))  # 2026-03-31 01:05 IST

    rows = [
        _row(start_idle),
        _row(start_idle + timedelta(minutes=10)),
        _row(start_offhours, current=3.0, voltage=231.0, power_w=1000.0),
        _row(start_offhours + timedelta(minutes=10)),
    ]

    accounting = aggregate_window(
        rows,
        platform_tz=platform_tz,
        shifts=local_shift,
        idle_threshold=2.0,
        over_threshold=20.0,
    )

    expected_interval_kwh = round((1.0 * 10.0) / 60.0, 6)
    assert round(accounting.total.idle_kwh, 6) == expected_interval_kwh
    assert round(accounting.total.offhours_kwh, 6) == expected_interval_kwh
    assert round(accounting.total.overconsumption_kwh, 6) == 0.0
    assert abs(accounting.total.total_loss_kwh - (expected_interval_kwh * 2)) < 1e-6
    assert accounting.total.energy_kwh >= accounting.total.total_loss_kwh
    assert accounting.total.idle_duration_sec == 600
    assert accounting.total.offhours_duration_sec == 600
    assert accounting.total.overconsumption_duration_sec == 0
    assert accounting.by_day[date(2026, 3, 31)].idle_kwh == accounting.total.idle_kwh
    assert accounting.by_day[date(2026, 3, 31)].offhours_kwh == accounting.total.offhours_kwh
    assert accounting.samples == 4


def test_aggregate_window_counts_outside_shift_idle_as_offhours() -> None:
    platform_tz = ZoneInfo("Asia/Kolkata")
    shift = [
        {
            "shift_start": "09:00",
            "shift_end": "17:00",
            "is_active": True,
        }
    ]

    start_idle = datetime(2026, 3, 30, 12, 35, tzinfo=ZoneInfo("UTC"))  # 2026-03-30 18:05 IST
    rows = [
        _row(start_idle, current=0.7, voltage=231.0, power_w=160.0),
        _row(start_idle + timedelta(minutes=10), current=0.7, voltage=231.0, power_w=160.0),
    ]

    accounting = aggregate_window(
        rows,
        platform_tz=platform_tz,
        shifts=shift,
        idle_threshold=1.0,
        over_threshold=20.0,
    )

    assert accounting.total.idle_kwh == 0.0
    assert accounting.total.offhours_kwh > 0
    assert accounting.total.total_loss_kwh == accounting.total.offhours_kwh
    assert accounting.total.idle_duration_sec == 0
    assert accounting.total.offhours_duration_sec == 600


def test_aggregate_window_counts_inside_shift_above_threshold_as_full_overconsumption_loss() -> None:
    platform_tz = ZoneInfo("Asia/Kolkata")
    shift = [
        {
            "shift_start": "09:00",
            "shift_end": "17:00",
            "is_active": True,
        }
    ]

    start_run = datetime(2026, 3, 30, 4, 30, tzinfo=ZoneInfo("UTC"))  # 10:00 IST
    rows = [
        _row(start_run, current=25.0, voltage=230.0, power_w=5750.0),
        _row(start_run + timedelta(minutes=10), current=25.0, voltage=230.0, power_w=5750.0),
    ]

    accounting = aggregate_window(
        rows,
        platform_tz=platform_tz,
        shifts=shift,
        idle_threshold=1.0,
        over_threshold=20.0,
    )

    expected_interval_kwh = 5750.0 / 1000.0 * (10.0 / 60.0)

    assert accounting.total.idle_kwh == 0.0
    assert accounting.total.offhours_kwh == 0.0
    assert round(accounting.total.overconsumption_kwh, 6) == round(expected_interval_kwh, 6)
    assert round(accounting.total.total_loss_kwh, 6) == round(expected_interval_kwh, 6)
    assert round(accounting.total.energy_kwh, 6) == round(expected_interval_kwh, 6)
    assert accounting.total.overconsumption_duration_sec == 600
