"""QR provisioning bundle parsing for the device simulator."""

from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class ProvisioningBundle:
    version: int
    broker: str
    port: int
    tenant_id: str
    device_id: str
    username: str
    password: str
    topic: str


def parse_provisioning_bundle(raw: str) -> ProvisioningBundle:
    """Parse and validate a QR provisioning JSON payload."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Provisioning bundle is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Provisioning bundle must be a JSON object")

    required_fields = (
        "version",
        "broker",
        "port",
        "tenant_id",
        "device_id",
        "username",
        "password",
        "topic",
    )
    missing_fields = [field for field in required_fields if field not in payload]
    if missing_fields:
        missing = ", ".join(missing_fields)
        raise ValueError(f"Provisioning bundle is missing required fields: {missing}")

    try:
        version = int(payload["version"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Provisioning bundle version must be an integer") from exc

    if version != 1:
        raise ValueError(f"Unsupported provisioning bundle version: {version}")

    broker = str(payload["broker"]).strip()
    tenant_id = str(payload["tenant_id"]).strip()
    device_id = str(payload["device_id"]).strip()
    username = str(payload["username"]).strip()
    password = str(payload["password"]).strip()
    topic = str(payload["topic"]).strip()

    if not broker:
        raise ValueError("Provisioning bundle broker cannot be empty")
    if not tenant_id:
        raise ValueError("Provisioning bundle tenant_id cannot be empty")
    if not device_id:
        raise ValueError("Provisioning bundle device_id cannot be empty")
    if not username:
        raise ValueError("Provisioning bundle username cannot be empty")
    if not password:
        raise ValueError("Provisioning bundle password cannot be empty")
    if not topic:
        raise ValueError("Provisioning bundle topic cannot be empty")

    try:
        port = int(payload["port"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Provisioning bundle port must be an integer") from exc

    if port <= 0 or port > 65535:
        raise ValueError("Provisioning bundle port must be between 1 and 65535")

    expected_topic = f"{tenant_id}/devices/{device_id}/telemetry"
    if topic != expected_topic:
        raise ValueError(
            "Provisioning bundle topic must match the canonical telemetry topic "
            f"'{expected_topic}'"
        )

    return ProvisioningBundle(
        version=version,
        broker=broker,
        port=port,
        tenant_id=tenant_id,
        device_id=device_id,
        username=username,
        password=password,
        topic=topic,
    )
