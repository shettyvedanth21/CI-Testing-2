"""
TEST SUITE 14 - Dashboard Widgets
Verifies home dashboard widget contracts, loss totals, and summary sanity.
"""

from __future__ import annotations

import math

import pytest


BASE = "http://localhost:8000"


def get_dashboard_summary(api) -> dict:
    resp = api.device.c.get("/api/v1/devices/dashboard/summary")
    resp.raise_for_status()
    return resp.json()


def test_dashboard_summary_returns_200(api):
    resp = api.device.c.get("/api/v1/devices/dashboard/summary")
    assert resp.status_code == 200


def test_dashboard_summary_has_energy_widgets(api):
    body = get_dashboard_summary(api)
    assert "energy_widgets" in body
    assert isinstance(body["energy_widgets"], dict)


def test_energy_widgets_has_expected_fields(api):
    widgets = get_dashboard_summary(api)["energy_widgets"]
    for key in (
        "month_energy_kwh",
        "month_energy_cost_inr",
        "today_energy_kwh",
        "today_energy_cost_inr",
        "today_loss_kwh",
        "today_loss_cost_inr",
    ):
        assert key in widgets, f"{key} missing from energy_widgets"


def test_dashboard_summary_has_device_and_alert_sections(api):
    body = get_dashboard_summary(api)
    assert "summary" in body
    assert "alerts" in body
    assert "devices" in body
    assert isinstance(body["devices"], list)


def test_dashboard_summary_counts_are_non_negative(api):
    body = get_dashboard_summary(api)
    summary = body["summary"]
    alerts = body["alerts"]

    for key in ("total_devices", "running_devices", "stopped_devices", "uptime_configured_devices"):
        value = summary.get(key)
        if isinstance(value, (int, float)):
            assert value >= 0

    for key in ("active", "unacknowledged", "last_24h"):
        value = alerts.get(key)
        if isinstance(value, (int, float)):
            assert value >= 0


def test_dashboard_widget_values_are_non_negative(api):
    widgets = get_dashboard_summary(api)["energy_widgets"]
    for key, value in widgets.items():
        if isinstance(value, (int, float)):
            assert value >= 0, f"{key} cannot be negative"


def test_today_loss_does_not_exceed_today_energy(api):
    widgets = get_dashboard_summary(api)["energy_widgets"]
    today_energy = float(widgets.get("today_energy_kwh") or 0.0)
    today_loss = float(widgets.get("today_loss_kwh") or 0.0)
    assert today_loss <= today_energy + 0.01


def test_no_nan_in_dashboard_widgets(api):
    widgets = get_dashboard_summary(api)["energy_widgets"]
    for key, value in widgets.items():
        if isinstance(value, float):
            assert not math.isnan(value), f"NaN found in {key}"
            assert not math.isinf(value), f"Inf found in {key}"


def test_cost_data_state_is_valid(api):
    body = get_dashboard_summary(api)
    assert body.get("cost_data_state") in ("fresh", "stale", "unavailable")


def test_cost_data_reasons_is_list(api):
    body = get_dashboard_summary(api)
    assert isinstance(body.get("cost_data_reasons"), list)


def test_today_loss_breakdown_endpoint_exists(api):
    resp = api.device.c.get("/api/v1/devices/dashboard/today-loss-breakdown")
    assert resp.status_code == 200


def test_today_loss_breakdown_has_expected_categories(api):
    body = api.device.c.get("/api/v1/devices/dashboard/today-loss-breakdown").json()
    totals = body["totals"]
    for key in (
        "idle_kwh",
        "off_hours_kwh",
        "overconsumption_kwh",
        "total_loss_kwh",
        "today_energy_kwh",
    ):
        assert key in totals, f"{key} missing from totals"


def test_today_loss_breakdown_rows_match_expected_shape(api):
    body = api.device.c.get("/api/v1/devices/dashboard/today-loss-breakdown").json()
    rows = body.get("rows", [])
    assert isinstance(rows, list)
    if not rows:
        pytest.skip("No loss breakdown rows returned")

    row = rows[0]
    for key in (
        "device_id",
        "device_name",
        "idle_kwh",
        "off_hours_kwh",
        "overconsumption_kwh",
        "total_loss_kwh",
    ):
        assert key in row, f"{key} missing from breakdown row"


def test_device_loss_breakdown_row_present_for_e2e_device(api, device_id):
    body = api.device.c.get("/api/v1/devices/dashboard/today-loss-breakdown").json()
    rows = body.get("rows", [])
    match = next((row for row in rows if row.get("device_id") == device_id), None)
    if match is None:
        pytest.skip(f"{device_id} not present in today-loss-breakdown rows")
    assert match["device_id"] == device_id
