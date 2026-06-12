import os
import sys
import unittest
from pathlib import Path
from unittest import mock


TOOL_DIR = Path(__file__).resolve().parent.parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from internal_service_auth import build_internal_service_headers, sign_internal_service_request  # noqa: E402


class InternalServiceAuthTests(unittest.TestCase):
    def test_sign_internal_service_request_matches_expected_contract(self) -> None:
        timestamp, signature = sign_internal_service_request(
            "telemetry-simulator",
            "SH00000001",
            timestamp=1_714_476_800,
            secret="shared-secret",
        )

        self.assertEqual(timestamp, 1_714_476_800)
        self.assertEqual(
            signature,
            "894b530979f18f1119da2e982d7563854ca9d14f46df0aa24a1fe076ae8186ab",
        )

    def test_build_internal_service_headers_reads_secret_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"INTERNAL_SERVICE_SHARED_SECRET": "shared-secret"}, clear=False):
            headers = build_internal_service_headers("telemetry-simulator", "SH00000001")

        self.assertEqual(headers["X-Internal-Service"], "telemetry-simulator")
        self.assertEqual(headers["X-Tenant-Id"], "SH00000001")
        self.assertIn("X-Internal-Service-Timestamp", headers)
        self.assertIn("X-Internal-Service-Signature", headers)


if __name__ == "__main__":
    unittest.main()
