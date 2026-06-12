import sys
import unittest
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parent.parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from provisioning_bundle import parse_provisioning_bundle  # noqa: E402


class ProvisioningBundleTests(unittest.TestCase):
    def test_parses_valid_bundle(self) -> None:
        bundle = parse_provisioning_bundle(
            """
            {
              "version": 1,
              "broker": "shivex.ai",
              "port": 1883,
              "tenant_id": "SH00000001",
              "device_id": "VD00000003",
              "username": "device:SH00000001:VD00000003",
              "password": "secret",
              "topic": "SH00000001/devices/VD00000003/telemetry"
            }
            """
        )

        self.assertEqual(bundle.broker, "shivex.ai")
        self.assertEqual(bundle.port, 1883)
        self.assertEqual(bundle.topic, "SH00000001/devices/VD00000003/telemetry")

    def test_rejects_non_canonical_topic(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical telemetry topic"):
            parse_provisioning_bundle(
                """
                {
                  "version": 1,
                  "broker": "shivex.ai",
                  "port": 1883,
                  "tenant_id": "SH00000001",
                  "device_id": "VD00000003",
                  "username": "device:SH00000001:VD00000003",
                  "password": "secret",
                  "topic": "SH00000001/devices/VD00000003/status"
                }
                """
            )

    def test_rejects_missing_required_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            parse_provisioning_bundle('{"version":1,"broker":"shivex.ai"}')


if __name__ == "__main__":
    unittest.main()
