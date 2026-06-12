def test_set_parameter_health_config(api, device_id):
    result = api.device.set_parameter_health(
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
    assert len(result) == 2


def test_health_score_returned(api, device_id):
    latest = api.data.get_latest(device_id)
    score = api.device.calculate_health_score(
        device_id,
        {
            "current": float(latest["current"]),
            "voltage": float(latest["voltage"]),
        },
    )
    assert score.get("health_score") is not None


def test_health_score_in_valid_range(api, device_id):
    latest = api.data.get_latest(device_id)
    score = api.device.calculate_health_score(
        device_id,
        {
            "current": float(latest["current"]),
            "voltage": float(latest["voltage"]),
        },
    )
    value = float(score["health_score"])
    assert 0 <= value <= 100


def test_health_score_not_zero_with_normal_telemetry(api, device_id):
    latest = api.data.get_latest(device_id)
    score = api.device.calculate_health_score(
        device_id,
        {
            "current": float(latest["current"]),
            "voltage": float(latest["voltage"]),
        },
    )
    assert float(score["health_score"]) > 10
