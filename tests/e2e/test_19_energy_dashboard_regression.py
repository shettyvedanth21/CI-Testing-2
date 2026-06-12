"""
TEST SUITE 19 - Energy Dashboard Regression
Verifies top-level energy widgets and calendar remain non-zero after simulator telemetry.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.db_client import pymysql
from tests.helpers.wait import wait_until


pytestmark = pytest.mark.slow

SERVICES = {
    "device": "http://localhost:8000",
    "data": "http://localhost:8081",
    "rules": "http://localhost:8002",
    "analytics": "http://localhost:8003",
    "reporting": "http://localhost:8085",
    "waste": "http://localhost:8087",
    "copilot": "http://localhost:8007",
}


def _dashboard_summary(api) -> dict:
    resp = api.device.c.get("/api/v1/devices/dashboard/summary")
    resp.raise_for_status()
    return resp.json()


def _today_loss_breakdown(api) -> dict:
    resp = api.device.c.get("/api/v1/devices/dashboard/today-loss-breakdown")
    resp.raise_for_status()
    return resp.json()


def _monthly_calendar(api) -> dict:
    today = date.today()
    resp = api.device.c.get(
        "/api/v1/devices/calendar/monthly-energy",
        params={"year": today.year, "month": today.month},
    )
    resp.raise_for_status()
    return resp.json()


@pytest.fixture(scope="session", autouse=True)
def ensure_energy_dashboard_device(api, device_id):
    if pymysql is None:
        pytest.skip("PyMySQL is not installed in this environment")
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

    api.device.set_idle_config(device_id, full_load_current_a=10.0, idle_current_threshold=1.0)
    api.device.set_waste_config(device_id, overconsumption_current_threshold_a=20.0)


def test_dashboard_summary_calendar_and_loss_breakdown_show_energy(api, simulator, device_id):
    simulator.send_idle(count=8, interval_sec=0.2)

    def _ready():
        summary = _dashboard_summary(api)
        widgets = summary.get("energy_widgets") or {}
        today_energy = float(widgets.get("today_energy_kwh") or 0.0)
        month_energy = float(widgets.get("month_energy_kwh") or 0.0)
        today_loss = float(widgets.get("today_loss_kwh") or 0.0)
        if today_energy <= 0 or month_energy <= 0 or today_loss <= 0:
            return None

        breakdown = _today_loss_breakdown(api)
        breakdown_totals = breakdown.get("totals") or {}
        if float(breakdown_totals.get("today_energy_kwh") or 0.0) <= 0:
            return None
        if float(breakdown_totals.get("total_loss_kwh") or 0.0) <= 0:
            return None

        calendar = _monthly_calendar(api)
        summary_block = calendar.get("summary") or {}
        if float(summary_block.get("total_energy_kwh") or 0.0) <= 0:
            return None
        if not any(float(day.get("energy_kwh") or 0.0) > 0 for day in calendar.get("days") or []):
            return None

        summary_energy = today_energy
        breakdown_energy = float(breakdown_totals.get("today_energy_kwh") or 0.0)
        summary_loss = today_loss
        breakdown_loss = float(breakdown_totals.get("total_loss_kwh") or 0.0)
        if summary_loss > summary_energy + 0.01:
            return None
        if breakdown_loss > breakdown_energy + 0.01:
            return None

        today = date.today().isoformat()
        calendar_today = next(
            (d for d in calendar.get("days") or [] if d.get("date") == today),
            {},
        )
        if calendar_today:
            calendar_energy = float(calendar_today.get("energy_kwh") or 0.0)
            if calendar_energy <= 0:
                return None
            if abs(calendar_energy - summary_energy) > 0.5:
                return None
            calendar_loss = float(calendar_today.get("loss_kwh") or 0.0)
            if calendar_loss > 0 and calendar_loss > calendar_energy + 0.01:
                return None
            if calendar_loss > 0 and abs(calendar_loss - summary_loss) > 0.5:
                return None

        return {
            "summary": summary,
            "breakdown": breakdown,
            "calendar": calendar,
            "calendar_today": calendar_today,
        }

    result = wait_until(
        _ready,
        timeout_sec=90,
        poll_sec=3.0,
        description="energy dashboard summary, loss breakdown, and calendar to become non-zero",
    )

    summary_widgets = result["summary"]["energy_widgets"]
    breakdown_totals = result["breakdown"]["totals"]
    calendar_summary = result["calendar"]["summary"]

    assert float(summary_widgets["today_energy_kwh"]) > 0
    assert float(summary_widgets["today_loss_kwh"]) > 0
    assert float(breakdown_totals["today_energy_kwh"]) > 0
    assert float(breakdown_totals["total_loss_kwh"]) > 0
    assert float(calendar_summary["total_energy_kwh"]) > 0

    calendar_today = result["calendar_today"]

    assert float(summary_widgets["today_loss_kwh"]) <= float(summary_widgets["today_energy_kwh"]) + 0.01
    assert float(breakdown_totals["total_loss_kwh"]) <= float(breakdown_totals["today_energy_kwh"]) + 0.01

    if calendar_today:
        assert float(calendar_today.get("energy_kwh", 0.0)) == pytest.approx(
            float(summary_widgets["today_energy_kwh"]), abs=0.5
        )
        calendar_loss = float(calendar_today.get("loss_kwh", 0.0))
        if calendar_loss > 0:
            assert calendar_loss <= float(calendar_today.get("energy_kwh", 0.0)) + 0.01
            assert calendar_loss == pytest.approx(float(summary_widgets["today_loss_kwh"]), abs=0.5)
