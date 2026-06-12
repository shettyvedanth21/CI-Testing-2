import json
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib import error


TOOL_DIR = Path(__file__).resolve().parent.parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from credential_bootstrap import DeviceMQTTCredentialBootstrap  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class CredentialBootstrapTests(unittest.TestCase):
    def _bootstrap(self) -> DeviceMQTTCredentialBootstrap:
        return DeviceMQTTCredentialBootstrap(
            auth_service_url="http://auth-service:8090",
            device_service_url="http://device-service:8000",
            tenant_id="SH00000001",
            device_id="VD00000003",
            bootstrap_email="super@example.com",
            bootstrap_password="Validate123!",
            retries=1,
        )

    def test_fetch_returns_registered_credential(self) -> None:
        seen_requests = []

        def _fake_urlopen(req, timeout=0):  # noqa: ANN001
            seen_requests.append(req)
            if req.full_url.endswith("/api/v1/auth/login"):
                return _FakeResponse({"access_token": "token-1"})
            return _FakeResponse(
                {
                    "data": {
                        "mqtt_password": "plain-secret",
                        "credential": {
                            "mqtt_username": "device:SH00000001:VD00000003",
                            "publish_topic": "SH00000001/devices/VD00000003/telemetry",
                        },
                    }
                }
            )

        with mock.patch("credential_bootstrap.request.urlopen", side_effect=_fake_urlopen):
            credential = self._bootstrap().fetch()

        self.assertEqual(credential.mqtt_username, "device:SH00000001:VD00000003")
        self.assertEqual(credential.mqtt_password, "plain-secret")
        self.assertEqual(credential.publish_topic, "SH00000001/devices/VD00000003/telemetry")
        register_request = seen_requests[1]
        self.assertEqual(register_request.headers["Authorization"], "Bearer token-1")
        self.assertEqual(register_request.headers["X-target-tenant-id"], "SH00000001")

    def test_fetch_rotates_when_credential_already_exists(self) -> None:
        seen_requests = []

        def _fake_urlopen(req, timeout=0):  # noqa: ANN001
            seen_requests.append(req)
            if req.full_url.endswith("/api/v1/auth/login"):
                return _FakeResponse({"access_token": "token-1"})
            if req.full_url.endswith("/mqtt-credential/register"):
                raise error.HTTPError(
                    req.full_url,
                    409,
                    "Conflict",
                    hdrs=None,
                    fp=None,
                )
            return _FakeResponse(
                {
                    "data": {
                        "mqtt_password": "rotated-secret",
                        "credential": {
                            "mqtt_username": "device:SH00000001:VD00000003",
                            "publish_topic": "SH00000001/devices/VD00000003/telemetry",
                        },
                    }
                }
            )

        with mock.patch("credential_bootstrap.request.urlopen", side_effect=_fake_urlopen):
            credential = self._bootstrap().fetch()

        self.assertEqual(credential.mqtt_password, "rotated-secret")
        self.assertTrue(any(req.full_url.endswith("/mqtt-credential/rotate") for req in seen_requests))


if __name__ == "__main__":
    unittest.main()
