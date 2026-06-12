"""Structured errors for device creation workflows."""

from __future__ import annotations


class DeviceCreateError(Exception):
    """Base class for create-device failures."""


class DeviceAlreadyExistsError(DeviceCreateError):
    """Raised when a real duplicate device conflict is detected."""


class DeviceIdAllocationError(DeviceCreateError):
    """Raised when the backend cannot allocate a unique generated device_id."""


class DevicePlantRequiredError(DeviceCreateError):
    """Raised when a device create or update would leave the device without a plant."""


class InvalidDeviceMetadataError(ValueError):
    """Raised when persisted device metadata violates the public contract."""

    def __init__(self, device_id: str, field_name: str, message: str) -> None:
        super().__init__(message)
        self.device_id = device_id
        self.field_name = field_name
        self.message = message


class DeviceMQTTError(Exception):
    """Base class for device MQTT credential workflow failures."""


class DeviceMQTTCredentialAlreadyExistsError(DeviceMQTTError):
    """Raised when a device already has a registered MQTT credential."""


class DeviceMQTTCredentialNotFoundError(DeviceMQTTError):
    """Raised when a device does not have a registered MQTT credential."""


class HardwareInventoryError(Exception):
    """Base class for hardware inventory workflow failures."""


class HardwareUnitAlreadyExistsError(HardwareInventoryError):
    """Raised when a hardware unit identifier already exists inside the tenant."""


class HardwareUnitIdAllocationError(HardwareInventoryError):
    """Raised when the backend cannot allocate a unique generated hardware_unit_id."""


class HardwareUnitNotFoundError(HardwareInventoryError):
    """Raised when a hardware unit cannot be found in the tenant scope."""


class HardwareInstallationNotFoundError(HardwareInventoryError):
    """Raised when a hardware installation cannot be found in the tenant scope."""


class HardwareInstallationConflictError(HardwareInventoryError):
    """Raised when a lifecycle invariant blocks the requested installation."""


class HardwareTenantMismatchError(HardwareInventoryError):
    """Raised when cross-tenant hardware/device ownership is detected."""


class HardwarePlantMismatchError(HardwareInventoryError):
    """Raised when plant consistency rules are violated."""


class HardwareStatusError(HardwareInventoryError):
    """Raised when a hardware status transition would violate lifecycle rules."""


class HardwareInstallationCompatibilityError(HardwareInventoryError):
    """Raised when a hardware unit type is incompatible with the requested installation role."""
