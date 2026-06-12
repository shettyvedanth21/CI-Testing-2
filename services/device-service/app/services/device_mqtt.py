"""Service layer for per-device MQTT credential management."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from app.models.device import (
    DeviceMQTTACL,
    DeviceMQTTAccess,
    DeviceMQTTCredential,
    DeviceMQTTPasswordAlgorithm,
    DeviceMQTTPermission,
)
from app.repositories.device import DeviceRepository
from app.repositories.device_mqtt import DeviceMQTTACLRepository, DeviceMQTTCredentialRepository
from app.services.device_errors import (
    DeviceMQTTCredentialAlreadyExistsError,
    DeviceMQTTCredentialNotFoundError,
)
from services.shared.tenant_context import TenantContext
import logging

logger = logging.getLogger(__name__)


def build_device_publish_topic(tenant_id: str, device_id: str) -> str:
    return f"{tenant_id}/devices/{device_id}/telemetry"


def build_device_status_topic(tenant_id: str, device_id: str) -> str:
    return f"{tenant_id}/devices/{device_id}/status"


def build_device_primary_subscribe_topic(tenant_id: str, device_id: str) -> str:
    return f"{tenant_id}/devices/{device_id}/cmd"


def build_device_subscribe_topics(tenant_id: str, device_id: str) -> tuple[str, ...]:
    return (
        build_device_primary_subscribe_topic(tenant_id, device_id),
        f"{tenant_id}/devices/{device_id}/config",
        f"{tenant_id}/devices/{device_id}/ota",
    )


class DeviceMQTTService:
    """Manages device-bound MQTT credentials and explicit authorization rows."""

    def __init__(self, session, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._devices = DeviceRepository(session, ctx)
        self._credentials = DeviceMQTTCredentialRepository(session, ctx)
        self._acls = DeviceMQTTACLRepository(session, ctx)

    async def get_credential(self, device_id: str) -> DeviceMQTTCredential:
        await self._require_device(device_id)
        credential = await self._credentials.get_for_device(device_id)
        if credential is None:
            raise DeviceMQTTCredentialNotFoundError(f"MQTT credential for device '{device_id}' was not found.")
        await self._session.refresh(credential, attribute_names=["acl_entries"])
        return credential

    async def register_credential(
        self,
        *,
        device_id: str,
        chip_id: str | None = None,
        commit: bool = True,
    ) -> tuple[DeviceMQTTCredential, str]:
        await self._require_device(device_id)
        existing = await self._credentials.get_for_device(device_id)
        if existing is not None:
            raise DeviceMQTTCredentialAlreadyExistsError(
                f"MQTT credential for device '{device_id}' already exists."
            )

        mqtt_password = self._generate_secret()
        credential = DeviceMQTTCredential(
            tenant_id=self._ctx.require_tenant(),
            device_id=device_id,
            mqtt_username=self._build_username(device_id),
            password_hash=self._hash_secret(mqtt_password),
            password_algorithm=DeviceMQTTPasswordAlgorithm.SHA256.value,
            publish_topic=self._build_publish_topic(device_id),
            subscribe_topic=self._build_subscribe_topic(device_id),
            chip_id=chip_id,
            is_active=True,
        )
        created = await self._credentials.create(credential)
        await self._replace_acl_rows(created)
        if commit:
            await self._session.commit()
        credential = await self._credentials.get_for_device(device_id)
        assert credential is not None
        await self._session.refresh(credential, attribute_names=["acl_entries"])
        logger.info(
            "Device MQTT credential created",
            extra={"tenant_id": credential.tenant_id, "device_id": device_id, "mqtt_username": credential.mqtt_username},
        )
        return credential, mqtt_password

    async def revoke_credential(self, *, device_id: str) -> DeviceMQTTCredential:
        credential = await self.get_credential(device_id)
        now = datetime.now(timezone.utc)
        credential.is_active = False
        credential.revoked_at = now
        for acl in credential.acl_entries:
            acl.is_active = False
        await self._session.commit()
        credential = await self._credentials.get_for_device(device_id)
        assert credential is not None
        await self._session.refresh(credential, attribute_names=["acl_entries"])
        logger.info(
            "Device MQTT credential revoked",
            extra={"tenant_id": credential.tenant_id, "device_id": device_id, "mqtt_username": credential.mqtt_username},
        )
        return credential

    async def rotate_credential(
        self,
        *,
        device_id: str,
        chip_id: str | None = None,
        commit: bool = True,
    ) -> tuple[DeviceMQTTCredential, str]:
        credential = await self.get_credential(device_id)
        mqtt_password = self._generate_secret()
        now = datetime.now(timezone.utc)
        credential.password_hash = self._hash_secret(mqtt_password)
        credential.password_algorithm = DeviceMQTTPasswordAlgorithm.SHA256.value
        credential.publish_topic = self._build_publish_topic(device_id)
        credential.subscribe_topic = self._build_subscribe_topic(device_id)
        credential.is_active = True
        credential.rotated_at = now
        credential.revoked_at = None
        if chip_id is not None:
            credential.chip_id = chip_id
        await self._replace_acl_rows(credential)
        if commit:
            await self._session.commit()
        credential = await self._credentials.get_for_device(device_id)
        assert credential is not None
        await self._session.refresh(credential, attribute_names=["acl_entries"])
        logger.info(
            "Device MQTT credential rotated",
            extra={"tenant_id": credential.tenant_id, "device_id": device_id, "mqtt_username": credential.mqtt_username},
        )
        return credential, mqtt_password

    async def _replace_acl_rows(self, credential: DeviceMQTTCredential) -> None:
        rows = [
            DeviceMQTTACL(
                credential_id=credential.id,
                tenant_id=credential.tenant_id,
                device_id=credential.device_id,
                mqtt_username=credential.mqtt_username,
                topic=credential.publish_topic,
                access=DeviceMQTTAccess.PUBLISH.value,
                permission=DeviceMQTTPermission.ALLOW.value,
                is_active=credential.is_active,
            )
        ]
        rows.append(
            DeviceMQTTACL(
                credential_id=credential.id,
                tenant_id=credential.tenant_id,
                device_id=credential.device_id,
                mqtt_username=credential.mqtt_username,
                topic=self._build_status_topic(credential.device_id),
                access=DeviceMQTTAccess.PUBLISH.value,
                permission=DeviceMQTTPermission.ALLOW.value,
                is_active=credential.is_active,
            )
        )
        for topic in self._build_subscribe_topics(credential.device_id):
            rows.append(
                DeviceMQTTACL(
                    credential_id=credential.id,
                    tenant_id=credential.tenant_id,
                    device_id=credential.device_id,
                    mqtt_username=credential.mqtt_username,
                    topic=topic,
                    access=DeviceMQTTAccess.SUBSCRIBE.value,
                    permission=DeviceMQTTPermission.ALLOW.value,
                    is_active=credential.is_active,
                )
            )

        rows.extend(
            [
                DeviceMQTTACL(
                    credential_id=credential.id,
                    tenant_id=credential.tenant_id,
                    device_id=credential.device_id,
                    mqtt_username=credential.mqtt_username,
                    topic="#",
                    access=DeviceMQTTAccess.PUBLISH.value,
                    permission=DeviceMQTTPermission.DENY.value,
                    is_active=credential.is_active,
                ),
                DeviceMQTTACL(
                    credential_id=credential.id,
                    tenant_id=credential.tenant_id,
                    device_id=credential.device_id,
                    mqtt_username=credential.mqtt_username,
                    topic="#",
                    access=DeviceMQTTAccess.SUBSCRIBE.value,
                    permission=DeviceMQTTPermission.DENY.value,
                    is_active=credential.is_active,
                ),
            ]
        )
        await self._acls.replace_for_credential(credential.id, rows)

    async def _require_device(self, device_id: str):
        device = await self._devices.get_by_id(device_id)
        if device is None:
            raise DeviceMQTTCredentialNotFoundError(f"Device '{device_id}' was not found.")
        return device

    def _build_username(self, device_id: str) -> str:
        return f"device:{self._ctx.require_tenant()}:{device_id}"

    def _build_publish_topic(self, device_id: str) -> str:
        return build_device_publish_topic(self._ctx.require_tenant(), device_id)

    def _build_status_topic(self, device_id: str) -> str:
        return build_device_status_topic(self._ctx.require_tenant(), device_id)

    def _build_subscribe_topic(self, device_id: str) -> str:
        return build_device_primary_subscribe_topic(self._ctx.require_tenant(), device_id)

    def _build_subscribe_topics(self, device_id: str) -> tuple[str, ...]:
        return build_device_subscribe_topics(self._ctx.require_tenant(), device_id)

    @staticmethod
    def _generate_secret() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _hash_secret(secret_value: str) -> str:
        return hashlib.sha256(secret_value.encode("utf-8")).hexdigest()
