from tests.helpers.wait import wait_until


def test_simulator_publishes_telemetry(simulator, api, device_id):
    simulator.send_normal(count=5, interval_sec=0.5)

    def received():
        items = api.data.get_telemetry(device_id, hours_back=1)
        return len(items) >= 5

    wait_until(received, timeout_sec=30, description="first 5 telemetry readings to be stored")


def test_latest_reading_has_core_fields(api, device_id):
    latest = api.data.get_latest(device_id)
    assert latest is not None
    assert any(key in latest for key in ("current", "voltage", "power", "energy_kwh"))


def test_device_goes_online(api, device_id):
    def is_active():
        data = api.device.get_device(device_id)
        status = data.get("runtime_status") or data.get("status")
        return status in ("running", "idle", "online", "active")

    wait_until(is_active, timeout_sec=20, description="device to go online after telemetry")


def test_send_bulk_data_for_downstream_tests(simulator, api, device_id):
    simulator.send_bulk(count=80, mode="normal", interval_sec=0.1)
    simulator.send_bulk(count=40, mode="idle", interval_sec=0.1)
    simulator.send_bulk(count=40, mode="normal", interval_sec=0.1)
    simulator.send_bulk(count=40, mode="overconsumption", interval_sec=0.1)

    def enough():
        items = api.data.get_telemetry(device_id, hours_back=2)
        return len(items) >= 150

    wait_until(enough, timeout_sec=90, description="150+ telemetry readings for downstream tests")
