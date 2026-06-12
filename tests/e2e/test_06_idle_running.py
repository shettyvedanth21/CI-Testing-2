def test_configure_idle_threshold(api, device_id):
    result = api.device.set_idle_config(device_id, idle_current_threshold=1.0)
    assert result["idle_current_threshold"] == 1.0


def test_configure_overconsumption_threshold(api, device_id):
    result = api.device.set_waste_config(device_id, overconsumption_current_threshold_a=20.0)
    assert result["overconsumption_current_threshold_a"] == 20.0


def test_idle_telemetry_sent(simulator):
    simulator.send_idle(count=5, interval_sec=0.2)


def test_current_state_endpoint_responds(api, device_id):
    state = api.device.get_current_state(device_id)
    assert state["device_id"] == device_id
    assert "state" in state
    assert "threshold" in state
