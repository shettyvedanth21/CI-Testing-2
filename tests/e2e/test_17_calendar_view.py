"""
TEST SUITE 17 - Calendar View (Monthly Energy Breakdown)
Verifies monthly energy calendar structure and reconciliation.
"""

from __future__ import annotations

import math
import time
from datetime import date

import pytest


def get_calendar(api, year: int, month: int) -> dict:
    resp = api.device.c.get(
        "/api/v1/devices/calendar/monthly-energy",
        params={"year": year, "month": month},
    )
    resp.raise_for_status()
    return resp.json()


def test_calendar_endpoint_returns_200(api):
    now = time.localtime()
    resp = api.device.c.get("/api/v1/devices/calendar/monthly-energy", params={"year": now.tm_year, "month": now.tm_mon})
    assert resp.status_code == 200


def test_calendar_has_summary_block(api):
    now = time.localtime()
    body = get_calendar(api, now.tm_year, now.tm_mon)
    assert "summary" in body


def test_calendar_summary_has_total_consumption(api):
    now = time.localtime()
    body = get_calendar(api, now.tm_year, now.tm_mon)
    summary = body["summary"]
    assert "total_energy_kwh" in summary
    assert "total_energy_cost_inr" in summary


def test_calendar_has_daily_entries(api):
    now = time.localtime()
    body = get_calendar(api, now.tm_year, now.tm_mon)
    daily = body.get("days") or []
    assert isinstance(daily, list)
    assert len(daily) >= 1 or date.today().day == 1


def test_calendar_daily_entries_have_date_and_kwh(api):
    now = time.localtime()
    body = get_calendar(api, now.tm_year, now.tm_mon)
    daily = body.get("days") or []
    if not daily:
        pytest.skip("No daily entries to verify")

    entry = daily[0]
    assert "date" in entry
    assert "energy_kwh" in entry


def test_calendar_daily_entries_have_cost(api):
    now = time.localtime()
    body = get_calendar(api, now.tm_year, now.tm_mon)
    daily = body.get("days") or []
    if not daily:
        pytest.skip("No daily entries to verify")

    assert "energy_cost_inr" in daily[0]


def test_calendar_daily_values_are_non_negative(api):
    now = time.localtime()
    body = get_calendar(api, now.tm_year, now.tm_mon)
    for entry in body.get("days") or []:
        for key in ("energy_kwh", "energy_cost_inr"):
            value = entry.get(key)
            if isinstance(value, (int, float)):
                assert value >= 0
                assert not math.isnan(value)
                assert not math.isinf(value)


def test_calendar_cost_data_state_is_valid(api):
    now = time.localtime()
    body = get_calendar(api, now.tm_year, now.tm_mon)
    assert body.get("cost_data_state") in ("fresh", "stale", "unavailable")


def test_calendar_monthly_total_matches_sum_of_days(api):
    now = time.localtime()
    body = get_calendar(api, now.tm_year, now.tm_mon)
    daily = body.get("days") or []
    if not daily:
        pytest.skip("No daily entries to compare")

    daily_sum = sum(float(entry.get("energy_kwh") or 0.0) for entry in daily)
    monthly_total = float(body.get("summary", {}).get("total_energy_kwh") or 0.0)

    if monthly_total == 0 and daily_sum == 0:
        pytest.skip("Both monthly total and daily sum are 0")

    assert abs(monthly_total - daily_sum) <= max(0.05, monthly_total * 0.05)


def test_calendar_previous_month_accessible(api):
    now = date.today()
    if now.month == 1:
        year, month = now.year - 1, 12
    else:
        year, month = now.year, now.month - 1

    resp = api.device.c.get("/api/v1/devices/calendar/monthly-energy", params={"year": year, "month": month})
    assert resp.status_code == 200
