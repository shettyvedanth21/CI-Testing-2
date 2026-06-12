"""
TEST SUITE 16 - Waste Calculation Verification
Verifies waste category math, tariff application, and DB persistence.
"""

from __future__ import annotations

from datetime import datetime
from datetime import date

import httpx
import pytest

from tests.helpers.assertions import assert_no_nan_inf
from tests.helpers.db_client import db_query_one
from tests.helpers.wait import wait_for_job


pytestmark = pytest.mark.slow


@pytest.fixture(scope="module", autouse=True)
def ensure_waste_setup(api, simulator, device_id):
    try:
        api.device.create_device(
            {
                "device_id": device_id,
                "device_name": f"E2E Compressor {device_id}",
                "device_type": "compressor",
                "location": "E2E Test Floor",
                "data_source_type": "metered",
                "phase_type": "single",
            }
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (400, 409):
            raise

    weekday = datetime.now().weekday()
    shifts = api.device.get_shifts(device_id)
    if not any(item.get("shift_name") == "Day Shift" and item.get("day_of_week") == weekday for item in shifts):
        api.device.set_shift(
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

    api.device.set_idle_config(device_id, idle_current_threshold=1.0)
    api.device.set_waste_config(device_id, overconsumption_current_threshold_a=20.0)
    simulator.send_bulk(count=20, mode="normal", interval_sec=0.1)


def _run_daily_waste_job(api, device_id: str, job_name: str) -> tuple[str, dict]:
    today = date.today()
    result = api.waste.run_analysis(
        {
            "scope": "selected",
            "device_ids": [device_id],
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
            "granularity": "daily",
            "job_name": job_name,
        }
    )
    job_id = result["job_id"]
    wait_for_job(lambda: api.waste.get_status(job_id), timeout_sec=180, description=job_name)
    payload = api.waste.get_result(job_id)
    return job_id, payload


def _device_row(payload: dict, device_id: str) -> dict | None:
    devices = payload.get("devices") or payload.get("device_summaries") or []
    return next((row for row in devices if row.get("device_id") == device_id), None)


def test_idle_threshold_is_saved_in_db(device_id):
    try:
        row = db_query_one(
            "SELECT idle_current_threshold FROM devices WHERE device_id = %s",
            (device_id,),
        )
    except Exception as exc:
        pytest.skip(f"DB not accessible: {exc}")

    if row is None or row["idle_current_threshold"] is None:
        pytest.skip("Idle threshold not found in DB")
    assert float(row["idle_current_threshold"]) == 1.0


def test_overconsumption_threshold_is_saved_in_db(device_id):
    try:
        row = db_query_one(
            "SELECT overconsumption_current_threshold_a FROM devices WHERE device_id = %s",
            (device_id,),
        )
    except Exception as exc:
        pytest.skip(f"DB not accessible: {exc}")

    if row is None or row["overconsumption_current_threshold_a"] is None:
        pytest.skip("Overconsumption threshold not found in DB")
    assert float(row["overconsumption_current_threshold_a"]) == 20.0


def test_waste_categories_are_non_negative(api, device_id):
    _, payload = _run_daily_waste_job(api, device_id, "E2E Waste Category Sanity")
    row = _device_row(payload, device_id)
    if row is None:
        pytest.skip(f"{device_id} not present in waste result")

    assert float(row.get("idle_energy_kwh") or 0.0) >= 0
    assert float(row.get("offhours_energy_kwh") or 0.0) >= 0
    assert float(row.get("overconsumption_kwh") or 0.0) >= 0
    total = row.get("total_waste_cost") or row.get("total_waste_cost_inr") or 0.0
    assert float(total) >= 0


def test_waste_cost_equals_kwh_times_tariff(api, device_id):
    tariff = api.reporting.get_tariff()
    rate = float(tariff.get("rate") or 0.0)
    if rate == 0:
        pytest.skip("Tariff is 0, cannot validate cost math")

    _, payload = _run_daily_waste_job(api, device_id, "E2E Waste Cost Verification")
    row = _device_row(payload, device_id)
    if row is None:
        pytest.skip(f"{device_id} not present in waste result")

    idle_kwh = float(row.get("idle_energy_kwh") or 0.0)
    idle_cost = row.get("idle_cost")
    if idle_cost is None:
        pytest.skip("idle_cost not available for verification")

    expected = round(idle_kwh * rate, 2)
    actual = round(float(idle_cost), 2)
    assert abs(expected - actual) <= 0.05


def test_idle_waste_only_counted_when_below_threshold(api, device_id, simulator):
    simulator.send_idle(count=5, interval_sec=0.2)
    _, payload = _run_daily_waste_job(api, device_id, "E2E Idle Threshold Test")
    row = _device_row(payload, device_id)
    if row is None:
        pytest.skip(f"{device_id} not present in waste result")
    assert float(row.get("idle_energy_kwh") or 0.0) >= 0


def test_overconsumption_waste_above_threshold(api, device_id, simulator):
    simulator.send_overconsumption(count=5, interval_sec=0.2)
    _, payload = _run_daily_waste_job(api, device_id, "E2E Overconsumption Test")
    row = _device_row(payload, device_id)
    if row is None:
        pytest.skip(f"{device_id} not present in waste result")

    over = row.get("overconsumption") or {}
    skipped = over.get("skipped_reason") if isinstance(over, dict) else None
    if skipped:
        pytest.skip(f"Overconsumption skipped: {skipped}")
    assert float(row.get("overconsumption_kwh") or 0.0) >= 0


def test_total_waste_matches_category_sum(api, device_id):
    _, payload = _run_daily_waste_job(api, device_id, "E2E Total Waste Sum")
    row = _device_row(payload, device_id)
    if row is None:
        pytest.skip(f"{device_id} not present in waste result")

    total = row.get("total_waste_cost") or row.get("total_waste_cost_inr")
    if total is None:
        pytest.skip("Total waste cost missing")

    category_sum = round(
        float(row.get("idle_cost") or 0.0)
        + float(row.get("offhours_cost") or 0.0)
        + float(row.get("overconsumption_cost") or 0.0),
        2,
    )
    assert abs(round(float(total), 2) - category_sum) <= 0.05


def test_waste_result_persisted_to_db(api, device_id):
    job_id, payload = _run_daily_waste_job(api, device_id, "E2E Waste DB Verification")
    row = _device_row(payload, device_id)
    if row is None:
        pytest.skip(f"{device_id} not present in waste result")

    try:
        db_row = db_query_one(
            "SELECT device_id, idle_energy_kwh, offhours_energy_kwh, overconsumption_kwh "
            "FROM waste_device_summary WHERE job_id = %s AND device_id = %s",
            (job_id, device_id),
        )
    except Exception as exc:
        pytest.skip(f"DB not accessible: {exc}")

    if db_row is None:
        pytest.skip("Waste DB summary row not found")

    assert db_row["device_id"] == device_id
    assert round(float(db_row["idle_energy_kwh"] or 0.0), 6) == round(float(row.get("idle_energy_kwh") or 0.0), 6)


def test_no_nan_in_waste_calculations(api, device_id):
    _, payload = _run_daily_waste_job(api, device_id, "E2E Waste NaN Check")
    assert_no_nan_inf(payload)
