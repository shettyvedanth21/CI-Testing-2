from datetime import datetime

import httpx
import pytest


def test_create_day_shift(api, device_id, state):
    weekday = datetime.now().weekday()
    result = api.device.set_shift(
        device_id,
        {
            "shift_name": "Day Shift",
            "shift_start": "08:00",
            "shift_end": "18:00",
            "day_of_week": weekday,
            "maintenance_break_minutes": 0,
            "is_active": True,
        },
    )
    state["shift_id"] = result["id"]
    assert result["shift_name"] == "Day Shift"


def test_shift_in_list(api, device_id):
    shifts = api.device.get_shifts(device_id)
    assert any(item["shift_name"] == "Day Shift" for item in shifts)


def test_shift_times_persisted(api, device_id):
    shifts = api.device.get_shifts(device_id)
    match = next(item for item in shifts if item["shift_name"] == "Day Shift")
    assert match["shift_start"] == "08:00:00"
    assert match["shift_end"] == "18:00:00"


def test_overlapping_shift_rejected(api, device_id):
    weekday = datetime.now().weekday()
    with pytest.raises(httpx.HTTPStatusError) as exc:
        api.device.set_shift(
            device_id,
            {
                "shift_name": "Overlapping Shift",
                "shift_start": "10:00",
                "shift_end": "16:00",
                "day_of_week": weekday,
                "maintenance_break_minutes": 0,
                "is_active": True,
            },
        )
    assert exc.value.response.status_code in (400, 409, 422)
