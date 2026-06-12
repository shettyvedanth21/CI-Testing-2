from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools" / "device-simulator"


def load_tool_module(module_name: str, filename: str):
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    spec = importlib.util.spec_from_file_location(module_name, TOOLS_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_arguments_accepts_tenant_id_argument(monkeypatch):
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--device-id", "COMPRESSOR-001", "--tenant-id", "SH00000001"],
    )

    main = load_tool_module("device_simulator_main_alias", "main.py")
    config = main.parse_arguments()

    assert config.device_id == "COMPRESSOR-001"
    assert config.tenant_id == "SH00000001"


def test_parse_arguments_falls_back_to_tenant_id_env(monkeypatch):
    monkeypatch.setenv("TENANT_ID", "SH00000002")
    monkeypatch.setattr(sys, "argv", ["main.py", "--device-id", "PUMP-002"])

    main = load_tool_module("device_simulator_main_env", "main.py")
    config = main.parse_arguments()

    assert config.device_id == "PUMP-002"
    assert config.tenant_id == "SH00000002"


def test_heartbeat_and_client_id_are_tenant_scoped(monkeypatch):
    simulator_mod = load_tool_module("device_simulator_runtime", "simulator.py")
    config_mod = load_tool_module("device_simulator_config", "config.py")

    config = config_mod.SimulatorConfig(
        device_id="COMPRESSOR-001",
        tenant_id="SH00000001",
        device_service_url="http://device-service:8000",
    )

    captured = {}

    class FakeResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout):
        captured["req"] = req
        captured["timeout"] = timeout
        return FakeResponse()

    class FakeMQTTClient:
        def __init__(self, broker_host, broker_port, client_id):
            captured["client_id"] = client_id
            self.is_connected = True

        def connect(self):
            return True

        def disconnect(self):
            return None

        def reconnect(self):
            return True

    class FakeTelemetryGenerator:
        def __init__(self, device_id, fault_mode, metric_config):
            self.device_id = device_id
            self.fault_mode = fault_mode
            self.metric_config = metric_config

        def generate(self):
            raise AssertionError("telemetry generation should not run in this test")

    monkeypatch.setattr(simulator_mod.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(simulator_mod, "MQTTClient", FakeMQTTClient)
    monkeypatch.setattr(simulator_mod, "TelemetryGenerator", FakeTelemetryGenerator)
    monkeypatch.setattr(simulator_mod.DeviceSimulator, "_run_loop", lambda self: None)

    simulator = simulator_mod.DeviceSimulator(config)
    simulator.start()
    simulator._send_device_heartbeat()

    assert captured["client_id"] == "simulator_sh00000001_compressor-001"
    assert captured["req"].full_url == (
        "http://device-service:8000/api/v1/devices/COMPRESSOR-001/heartbeat"
    )
    headers = {key.lower(): value for key, value in captured["req"].header_items()}
    assert headers["x-internal-service"] == "telemetry-simulator"
    assert headers["x-tenant-id"] == "SH00000001"
