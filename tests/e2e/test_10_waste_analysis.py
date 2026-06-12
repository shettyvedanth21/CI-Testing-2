from datetime import date

import pytest

from tests.helpers.assertions import assert_numeric_non_negative, assert_valid_pdf
from tests.helpers.wait import wait_for_download_ready, wait_for_job


pytestmark = pytest.mark.slow


def test_run_waste_analysis(api, device_id, simulator, state):
    simulator.send_normal(count=12, interval_sec=0.2)
    today = date.today()
    result = api.waste.run_analysis(
        {
            "scope": "selected",
            "device_ids": [device_id],
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
            "granularity": "daily",
            "job_name": "E2E Waste Analysis",
        }
    )
    state["waste_job_id"] = result["job_id"]
    assert result["job_id"]


def test_waste_job_completes(api, state):
    wait_for_job(lambda: api.waste.get_status(state["waste_job_id"]), timeout_sec=180, description="waste analysis job")


def test_waste_result_has_device_entry(api, device_id, state):
    result = api.waste.get_result(state["waste_job_id"])
    devices = result.get("devices") or result.get("device_summaries") or []
    assert devices
    state["waste_device_row"] = next(item for item in devices if item["device_id"] == device_id)


def test_waste_idle_energy_present(state):
    row = state["waste_device_row"]
    assert any(key in row for key in ("idle_energy_kwh", "idle_kwh", "idle_running_kwh"))


def test_waste_offhours_field_present(state):
    row = state["waste_device_row"]
    assert any(key in row for key in ("off_hours", "offhours_energy_kwh"))


def test_waste_offhours_skipped_reason_valid(state):
    row = state["waste_device_row"]
    off_hours = row.get("off_hours", {})
    if isinstance(off_hours, dict) and off_hours.get("skipped_reason"):
        assert isinstance(off_hours["skipped_reason"], str)
        assert len(off_hours["skipped_reason"]) > 5


def test_waste_overconsumption_field_present(state):
    row = state["waste_device_row"]
    assert any(key in row for key in ("overconsumption", "overconsumption_kwh"))


def test_waste_unoccupied_field_exists(state):
    row = state["waste_device_row"]
    assert any(key in row for key in ("unoccupied_running", "unoccupied_energy_kwh"))


def test_waste_total_cost_numeric(state):
    row = state["waste_device_row"]
    total = row.get("total_waste_cost") or row.get("total_cost") or row.get("total_waste_energy_cost")
    assert_numeric_non_negative(total, label="total_waste_cost")


def test_waste_pdf_valid(api, state):
    status = api.waste.get_status(state["waste_job_id"])
    coverage = status.get("coverage_result") or {}
    if not status.get("download_ready") and (
        status.get("error_code") == "INSUFFICIENT_TELEMETRY_COVERAGE"
        or coverage.get("terminal_status") == "business_blocked"
    ):
        assert status.get("result_ready") is True
        assert coverage.get("artifact_generation_allowed") is False
        return

    wait_for_download_ready(
        lambda: api.waste.get_status(state["waste_job_id"]),
        timeout_sec=300,
        description="waste PDF download",
    )
    pdf = api.waste.download_pdf(state["waste_job_id"])
    assert_valid_pdf(pdf, label="Waste Analysis PDF")
