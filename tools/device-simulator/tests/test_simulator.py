import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


TOOL_DIR = Path(__file__).resolve().parent.parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from config import SimulatorConfig  # noqa: E402
from simulator import DeviceSimulator  # noqa: E402


class FallbackHeartbeatStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SimulatorConfig(
            device_id="VD00000003",
            tenant_id="SH00000001",
            device_service_url="http://localhost:8000",
        )
        self.simulator = DeviceSimulator(self.config)

    def test_does_not_send_fallback_heartbeat_when_mqtt_is_healthy(self) -> None:
        self.simulator._mqtt_client = SimpleNamespace(is_connected=True)
        self.assertFalse(self.simulator._should_send_fallback_heartbeat())

    def test_sends_fallback_heartbeat_when_mqtt_is_disconnected(self) -> None:
        self.simulator._mqtt_client = SimpleNamespace(is_connected=False)
        self.assertTrue(self.simulator._should_send_fallback_heartbeat())

    def test_sends_fallback_heartbeat_when_buffered_payloads_exist(self) -> None:
        self.simulator._mqtt_client = SimpleNamespace(is_connected=True)
        self.simulator._pending_payloads.append({"device_id": "VD00000003"})
        self.assertTrue(self.simulator._should_send_fallback_heartbeat())

    def test_send_device_heartbeat_uses_signed_internal_headers(self) -> None:
        captured = {}

        class _Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def _fake_urlopen(req, timeout=0):  # noqa: ANN001
            captured["headers"] = dict(req.header_items())
            captured["url"] = req.full_url
            return _Response()

        with mock.patch.dict(os.environ, {"INTERNAL_SERVICE_SHARED_SECRET": "shared-secret"}, clear=False):
            with mock.patch("simulator.request.urlopen", side_effect=_fake_urlopen):
                self.simulator._send_device_heartbeat()

        self.assertEqual(captured["url"], "http://localhost:8000/api/v1/devices/VD00000003/heartbeat")
        self.assertEqual(captured["headers"]["X-internal-service"], "telemetry-simulator")
        self.assertEqual(captured["headers"]["X-tenant-id"], "SH00000001")
        self.assertIn("X-internal-service-timestamp", captured["headers"])
        self.assertIn("X-internal-service-signature", captured["headers"])


if __name__ == "__main__":
    unittest.main()
