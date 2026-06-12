import socket
import sys
import unittest
from pathlib import Path
from unittest import mock


TOOL_DIR = Path(__file__).resolve().parent.parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from config import SimulatorConfig  # noqa: E402
from main import _default_internal_service_url, parse_arguments  # noqa: E402


class DefaultInternalServiceUrlTests(unittest.TestCase):
    def test_uses_docker_dns_when_service_name_resolves(self) -> None:
        with mock.patch("main.socket.getaddrinfo", return_value=[object()]):
            self.assertEqual(
                _default_internal_service_url("device-service", 8000),
                "http://device-service:8000",
            )

    def test_falls_back_to_localhost_when_service_name_does_not_resolve(self) -> None:
        with mock.patch("main.socket.getaddrinfo", side_effect=socket.gaierror):
            self.assertEqual(
                _default_internal_service_url("device-service", 8000),
                "http://localhost:8000",
            )


class SimulatorConfigTests(unittest.TestCase):
    def test_defaults_to_plain_tcp_mqtt_port(self) -> None:
        config = SimulatorConfig(device_id="VD00000003")
        self.assertEqual(config.broker_port, 1883)

    def test_explicit_mqtt_credentials_disable_bootstrap(self) -> None:
        config = SimulatorConfig(
            device_id="VD00000003",
            mqtt_username="device:SH00000001:VD00000003",
            mqtt_password="secret",
            mqtt_credential_bootstrap_email="admin@example.com",
            mqtt_credential_bootstrap_password="Validate123!",
        )
        self.assertFalse(config.should_bootstrap_mqtt_credential)


class ParseArgumentsTests(unittest.TestCase):
    def test_provisioning_bundle_overrides_manual_mqtt_values(self) -> None:
        bundle = (
            '{"version":1,"broker":"shivex.ai","port":1883,'
            '"tenant_id":"SH00000001","device_id":"VD00000003",'
            '"username":"device:SH00000001:VD00000003","password":"secret",'
            '"topic":"SH00000001/devices/VD00000003/telemetry"}'
        )

        with mock.patch.object(
            sys,
            "argv",
            [
                "main.py",
                "--device-id",
                "IGNORED",
                "--provisioning-bundle",
                bundle,
                "--mqtt-credential-bootstrap-email",
                "admin@example.com",
                "--mqtt-credential-bootstrap-password",
                "Validate123!",
            ],
        ):
            config = parse_arguments()

        self.assertEqual(config.device_id, "VD00000003")
        self.assertEqual(config.tenant_id, "SH00000001")
        self.assertEqual(config.broker_host, "shivex.ai")
        self.assertEqual(config.broker_port, 1883)
        self.assertEqual(config.mqtt_username, "device:SH00000001:VD00000003")
        self.assertEqual(config.mqtt_password, "secret")
        self.assertEqual(config.topic, "SH00000001/devices/VD00000003/telemetry")
        self.assertFalse(config.should_bootstrap_mqtt_credential)


if __name__ == "__main__":
    unittest.main()
