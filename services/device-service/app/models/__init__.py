"""Device model - re-export for convenience."""

from app.models.device import (
    Device,
    DeviceLatestTelemetrySnapshot,
    DeviceRecentTelemetrySample,
    DeviceStatus,
    DeviceStateInterval,
    DeviceStateIntervalType,
    DeviceHardwareInstallation,
    DeviceDashboardWidget,
    DeviceDashboardWidgetSetting,
    MaintenanceLog,
    HardwareUnitSequence,
    DeviceLiveState,
    HardwareUnit,
    HardwareUnitStatus,
    WasteSiteConfig,
)

__all__ = [
    "Device",
    "DeviceLatestTelemetrySnapshot",
    "DeviceRecentTelemetrySample",
    "DeviceStatus",
    "DeviceStateInterval",
    "DeviceStateIntervalType",
    "DeviceHardwareInstallation",
    "DeviceDashboardWidget",
    "DeviceDashboardWidgetSetting",
    "MaintenanceLog",
    "HardwareUnitSequence",
    "DeviceLiveState",
    "HardwareUnit",
    "HardwareUnitStatus",
    "WasteSiteConfig",
]
