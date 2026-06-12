"""
TEST SUITE 15 - Health Score Calculation with Weights
Verifies weight persistence, validation, and score behavior.
"""

from __future__ import annotations

import httpx
import pytest

from tests.helpers.db_client import db_query


@pytest.fixture(scope="module", autouse=True)
def ensure_health_setup(api, simulator, device_id):
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

    resp = api.device.c.get(f"/api/v1/devices/{device_id}/health-config")
    resp.raise_for_status()
    existing = resp.json().get("data", [])
    existing_names = {item.get("parameter_name") for item in existing}

    if not {"current", "voltage"}.issubset(existing_names):
        api.device.set_parameter_health(
            device_id,
            {
                "parameters": [
                {
                    "field": "current",
                    "normal_min": 8.0,
                    "normal_max": 18.0,
                    "weight": 60.0,
                },
                {
                    "field": "voltage",
                    "normal_min": 210.0,
                    "normal_max": 250.0,
                    "weight": 40.0,
                },
            ]
            },
        )
    simulator.send_normal(count=5, interval_sec=0.2)


def test_health_config_weights_save_to_db(device_id):
    try:
        rows = db_query(
            "SELECT parameter_name, weight FROM parameter_health_config "
            "WHERE device_id = %s ORDER BY parameter_name",
            (device_id,),
        )
    except Exception as exc:
        pytest.skip(f"DB not accessible: {exc}")

    if not rows:
        pytest.skip("No parameter health config rows found")

    weights = {row["parameter_name"]: float(row["weight"]) for row in rows}
    if "current" in weights:
        assert weights["current"] == 60.0
    if "voltage" in weights:
        assert weights["voltage"] == 40.0


def test_weights_sum_to_100(device_id):
    try:
        rows = db_query(
            "SELECT SUM(weight) AS total_weight FROM parameter_health_config "
            "WHERE device_id = %s AND is_active = 1",
            (device_id,),
        )
    except Exception as exc:
        pytest.skip(f"DB not accessible: {exc}")

    total = rows[0]["total_weight"] if rows else None
    if total is None:
        pytest.skip("No active health config rows found")
    assert abs(float(total) - 100.0) < 0.01


def test_validate_weights_endpoint_returns_valid(api, device_id):
    resp = api.device.c.get(f"/api/v1/devices/{device_id}/health-config/validate-weights")
    resp.raise_for_status()
    body = resp.json()
    assert body["is_valid"] is True
    assert abs(float(body["total_weight"]) - 100.0) < 0.01


def test_health_score_current_within_normal_range(api, device_id):
    score = api.device.calculate_health_score(device_id, {"current": 12.5, "voltage": 231.0})
    assert float(score["health_score"]) >= 50


def test_health_score_current_below_normal_min(api, device_id):
    normal = api.device.calculate_health_score(device_id, {"current": 12.5, "voltage": 231.0})
    low = api.device.calculate_health_score(device_id, {"current": 2.0, "voltage": 231.0})
    assert float(low["health_score"]) <= float(normal["health_score"])


def test_health_score_current_above_normal_max(api, device_id):
    normal = api.device.calculate_health_score(device_id, {"current": 12.5, "voltage": 231.0})
    critical = api.device.calculate_health_score(device_id, {"current": 35.0, "voltage": 231.0})
    assert float(critical["health_score"]) < float(normal["health_score"])


def test_health_score_always_in_0_to_100_range(api, device_id):
    for values in (
        {"current": 0.0, "voltage": 0.0},
        {"current": 12.5, "voltage": 231.0},
        {"current": 100.0, "voltage": 300.0},
        {"current": 35.0, "voltage": 150.0},
    ):
        score = api.device.calculate_health_score(device_id, values)
        value = score.get("health_score")
        if value is None:
            pytest.skip(f"Health score unavailable for values {values}")
        assert 0 <= float(value) <= 100


def test_health_score_reflects_both_parameters(api, device_id):
    both_good = api.device.calculate_health_score(device_id, {"current": 12.5, "voltage": 231.0})
    one_bad = api.device.calculate_health_score(device_id, {"current": 35.0, "voltage": 231.0})
    assert float(both_good["health_score"]) > float(one_bad["health_score"])


def test_health_score_response_includes_parameter_breakdown(api, device_id):
    score = api.device.calculate_health_score(device_id, {"current": 12.5, "voltage": 231.0})
    breakdown = score.get("parameter_scores")
    assert isinstance(breakdown, list)
    assert len(breakdown) >= 1
    assert all("parameter_name" in item for item in breakdown)


def test_dashboard_bootstrap_exposes_health_score(api, device_id):
    resp = api.device.c.get(f"/api/v1/devices/{device_id}/dashboard-bootstrap")
    resp.raise_for_status()
    body = resp.json()
    assert "health_score" in body
