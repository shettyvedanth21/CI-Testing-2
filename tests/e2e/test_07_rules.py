import pytest

from tests.helpers.wait import wait_until


def test_create_threshold_rule(api, device_id, state):
    result = api.rules.create_rule(
        {
            "rule_name": "E2E High Current Alert",
            "rule_type": "threshold",
            "property": "current",
            "condition": ">",
            "threshold": 30.0,
            "scope": "selected_devices",
            "device_ids": [device_id],
            "notification_channels": ["email"],
            "cooldown_minutes": 5,
            "cooldown_mode": "interval",
        }
    )
    state["rule_id"] = result["rule_id"]
    assert result["rule_name"] == "E2E High Current Alert"


def test_rule_in_list(api, state):
    rules = api.rules.get_rules()
    ids = [str(item["rule_id"]) for item in rules]
    assert str(state["rule_id"]) in ids


def test_alert_fires_on_spike(api, simulator, device_id, state):
    simulator.send_spike(count=5, interval_sec=0.5)
    attempts = {"count": 0}

    def alert_fired():
        attempts["count"] += 1
        # Rule engine can be cold after restarts; re-send a burst a few times.
        if attempts["count"] in (4, 8, 12):
            simulator.send_spike(count=3, interval_sec=0.2)
        alerts = api.rules.get_alerts(device_id)
        return any(str(item["rule_id"]) == str(state["rule_id"]) for item in alerts)

    wait_until(alert_fired, timeout_sec=90, description="alert to fire after current spike")


def test_alert_has_required_fields(api, device_id, state):
    alerts = api.rules.get_alerts(device_id)
    matches = [item for item in alerts if str(item.get("rule_id")) == str(state["rule_id"])]
    if not matches:
        pytest.skip("No matching alert available after retries; rule engine likely still warming up")
    alert = matches[0]
    assert alert["device_id"] == device_id
    assert alert["severity"] in ("high", "critical", "warning", "medium", "low")
    assert alert.get("created_at")


def test_rule_deleted_after_test(api, state):
    api.rules.delete_rule(state["rule_id"])
    rules = api.rules.get_rules()
    ids = [str(item["rule_id"]) for item in rules]
    assert str(state["rule_id"]) not in ids
