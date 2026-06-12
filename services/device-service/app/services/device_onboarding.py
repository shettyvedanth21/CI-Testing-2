"""Service layer for transactional device onboarding with MQTT provisioning."""

from __future__ import annotations

from app.schemas.device import DeviceCreate
from app.services.device import DeviceService
from app.services.device_mqtt import DeviceMQTTService
from services.shared.tenant_context import TenantContext


class DeviceOnboardingService:
    """Creates a device and provisions its one-time MQTT credential in one flow."""

    def __init__(self, session, ctx: TenantContext):
        self._session = session
        self._device_service = DeviceService(session, ctx)
        self._mqtt_service = DeviceMQTTService(session, ctx)

    async def onboard_device(self, device_data: DeviceCreate):
        try:
            device = await self._device_service.create_device(device_data, commit=False)
            credential, mqtt_password = await self._mqtt_service.register_credential(
                device_id=device.device_id,
                commit=False,
            )
            await self._session.commit()
            return device, credential, mqtt_password
        except Exception:
            await self._session.rollback()
            raise
