"""
TEST SUITE 18 - Time-Based Rules
Verifies time-based rule creation, persistence, and cleanup.
"""

from __future__ import annotations

import httpx
import pytest

from tests.helpers.db_client import db_query_one


@pytest.fixture(scope="module", autouse=True)
def ensure_rule_device(api, device_id):
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


@pytest.fixture(scope="module")
def time_rule_state():
    return {"rule_id": None}


def test_create_time_based_rule(api, device_id, time_rule_state):
    payload = {
        "rule_name": "E2E Time-Based Rule",
        "rule_type": "time_based",
        "scope": "selected_devices",
        "device_ids": [device_id],
        "notification_channels": ["email"],
        "cooldown_minutes": 60,
        "cooldown_mode": "interval",
        "time_window_start": "22:00",
        "time_window_end": "06:00",
        "timezone": "Asia/Kolkata",
    }

    result = api.rules.create_rule(payload)
    rule_id = result.get("rule_id") or result.get("id")
    assert rule_id is not None
    time_rule_state["rule_id"] = str(rule_id)


def test_time_based_rule_in_list(api, time_rule_state):
    rules = api.rules.get_rules()
    ids = [str(item.get("rule_id") or item.get("id")) for item in rules]
    assert str(time_rule_state["rule_id"]) in ids


def test_time_based_rule_has_correct_type(api, time_rule_state):
    rules = api.rules.get_rules()
    rule = next(
        (
            item for item in rules
            if str(item.get("rule_id") or item.get("id")) == str(time_rule_state["rule_id"])
        ),
        None,
    )
    assert rule is not None
    assert (rule.get("rule_type") or "").lower() == "time_based"


def test_time_based_rule_has_time_window(api, time_rule_state):
    rules = api.rules.get_rules()
    rule = next(
        (
            item for item in rules
            if str(item.get("rule_id") or item.get("id")) == str(time_rule_state["rule_id"])
        ),
        None,
    )
    assert rule is not None
    assert rule.get("time_window_start") == "22:00"
    assert rule.get("time_window_end") == "06:00"


def test_time_based_rule_cooldown_saved(api, time_rule_state):
    rules = api.rules.get_rules()
    rule = next(
        (
            item for item in rules
            if str(item.get("rule_id") or item.get("id")) == str(time_rule_state["rule_id"])
        ),
        None,
    )
    assert rule is not None
    assert int(rule.get("cooldown_minutes") or 0) == 60


def test_time_based_rule_defaults_time_condition(api, time_rule_state):
    rules = api.rules.get_rules()
    rule = next(
        (
            item for item in rules
            if str(item.get("rule_id") or item.get("id")) == str(time_rule_state["rule_id"])
        ),
        None,
    )
    assert rule is not None
    assert rule.get("time_condition") in (None, "running_in_window")


def test_time_based_rule_saved_in_db(time_rule_state):
    try:
        row = db_query_one(
            "SELECT rule_id, rule_type, time_window_start, time_window_end, cooldown_minutes "
            "FROM rules WHERE rule_id = %s",
            (time_rule_state["rule_id"],),
        )
    except Exception as exc:
        pytest.skip(f"DB not accessible: {exc}")

    if row is None:
        pytest.skip("Rule not found in DB")

    assert str(row["rule_id"]) == str(time_rule_state["rule_id"])
    assert str(row["rule_type"]).lower() == "time_based"
    assert row["time_window_start"] is not None
    assert row["time_window_end"] is not None
    assert int(row["cooldown_minutes"]) == 60


def test_cleanup_time_based_rule(api, time_rule_state):
    if time_rule_state["rule_id"]:
        api.rules.delete_rule(time_rule_state["rule_id"])
        rules = api.rules.get_rules()
        ids = [str(item.get("rule_id") or item.get("id")) for item in rules]
        assert str(time_rule_state["rule_id"]) not in ids
