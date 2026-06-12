from datetime import date, timedelta

import pytest
import requests

from tests.helpers.assertions import assert_valid_pdf
from tests.helpers.wait import wait_for_job


pytestmark = pytest.mark.slow


def test_generate_energy_report(api, device_id, state):
    today = date.today()
    start_date = today - timedelta(days=1)
    result = api.reporting.run_energy_report(
        {
            "device_id": device_id,
            "start_date": start_date.isoformat(),
            "end_date": today.isoformat(),
            "report_name": "E2E Consumption Report",
        }
    )
    state["report_id"] = result["report_id"]
    assert result["report_id"]


def test_report_job_completes(api, state):
    wait_for_job(lambda: api.reporting.get_report_status(state["report_id"]), timeout_sec=180, description="energy report")


def test_report_pdf_valid(api, state):
    pdf = api.reporting.download_report(state["report_id"])
    assert_valid_pdf(pdf, label="Energy Report PDF")


def test_report_final_status_is_completed(api, state):
    status = api.reporting.get_report_status(state["report_id"])
    assert (status.get("status") or "").lower() == "completed"


def test_reporting_history_without_tenant_query_is_supported(api):
    response = api.reporting.c.get("/api/reports/history")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload.get("reports", []), list)


def test_reporting_schedules_without_tenant_query_is_supported(api):
    response = api.reporting.c.get("/api/reports/schedules")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload.get("schedules", []), list)
