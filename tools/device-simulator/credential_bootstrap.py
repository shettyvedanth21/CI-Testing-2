"""Helpers to fetch per-device MQTT credentials from Shivex for local/dev simulation."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request

logger = logging.getLogger(__name__)


@dataclass
class BootstrappedCredential:
    mqtt_username: str
    mqtt_password: str
    publish_topic: str


class DeviceMQTTCredentialBootstrap:
    """Acquires a current per-device MQTT secret from Shivex using admin HTTP APIs."""

    def __init__(
        self,
        *,
        auth_service_url: str,
        device_service_url: str,
        tenant_id: str,
        device_id: str,
        bootstrap_email: str,
        bootstrap_password: str,
        retries: int = 10,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        self._auth_service_url = auth_service_url.rstrip("/")
        self._device_service_url = device_service_url.rstrip("/")
        self._tenant_id = tenant_id
        self._device_id = device_id
        self._bootstrap_email = bootstrap_email
        self._bootstrap_password = bootstrap_password
        self._retries = retries
        self._retry_delay_seconds = retry_delay_seconds

    def fetch(self) -> BootstrappedCredential:
        token = self._login_with_retries()
        response = self._register_or_rotate(token)
        data = response["data"]
        credential = data["credential"]
        return BootstrappedCredential(
            mqtt_username=str(credential["mqtt_username"]),
            mqtt_password=str(data["mqtt_password"]),
            publish_topic=str(credential["publish_topic"]),
        )

    def _login_with_retries(self) -> str:
        for attempt in range(1, self._retries + 1):
            try:
                return self._login()
            except Exception as exc:
                if attempt == self._retries:
                    raise
                logger.info(
                    "Retrying MQTT credential bootstrap login",
                    extra={"attempt": attempt, "error": str(exc)},
                )
                time.sleep(self._retry_delay_seconds)
        raise RuntimeError("Unreachable login retry state")

    def _login(self) -> str:
        body = json.dumps(
            {
                "email": self._bootstrap_email,
                "password": self._bootstrap_password,
            }
        ).encode("utf-8")
        req = request.Request(
            url=f"{self._auth_service_url}/api/v1/auth/login",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Auth-service login did not return an access token")
        return str(token)

    def _register_or_rotate(self, access_token: str) -> dict[str, Any]:
        register_url = (
            f"{self._device_service_url}/api/v1/devices/{self._device_id}/mqtt-credential/register"
        )
        rotate_url = (
            f"{self._device_service_url}/api/v1/devices/{self._device_id}/mqtt-credential/rotate"
        )
        try:
            return self._post_json(register_url, access_token)
        except error.HTTPError as exc:
            if exc.code != 409:
                detail = exc.read().decode("utf-8", errors="ignore")
                raise RuntimeError(
                    f"Device credential register failed with HTTP {exc.code}: {detail}"
                ) from exc
            logger.info(
                "MQTT credential already exists for simulator device; rotating for fresh secret",
                extra={"tenant_id": self._tenant_id, "device_id": self._device_id},
            )
            return self._post_json(rotate_url, access_token)

    def _post_json(self, url: str, access_token: str) -> dict[str, Any]:
        req = request.Request(
            url=url,
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
                "X-Target-Tenant-Id": self._tenant_id,
            },
        )
        with request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
