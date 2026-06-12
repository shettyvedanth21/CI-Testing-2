"""Pydantic schemas for Device Service API."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
import math
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
import re

ALLOWED_HARDWARE_UNIT_TYPES = {
    "energy_meter",
    "ct_sensor",
    "esp32",
    "oil_sensor",
    "temperature_sensor",
    "vibration_sensor",
    "motor_sensor",
}

ALLOWED_INSTALLATION_ROLES = {
    "main_meter",
    "ct1",
    "ct2",
    "ct3",
    "ct4",
    "controller",
    "oil_sensor",
    "temperature_sensor",
    "vibration_sensor",
    "motor_sensor",
}

HARDWARE_ROLE_COMPATIBILITY = {
    "energy_meter": {"main_meter"},
    "ct_sensor": {"ct1", "ct2", "ct3", "ct4"},
    "esp32": {"controller"},
    "oil_sensor": {"oil_sensor"},
    "temperature_sensor": {"temperature_sensor"},
    "vibration_sensor": {"vibration_sensor"},
    "motor_sensor": {"motor_sensor"},
}

MANUAL_DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,49}$")
LEGACY_PHASE_TYPE_ALIASES = {
    "single_phase": "single",
    "single-phase": "single",
    "three_phase": "three",
    "three-phase": "three",
}


def normalize_phase_type(
    value: str | None,
    *,
    allow_legacy_aliases: bool,
) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized in ("single", "three"):
        return normalized
    if allow_legacy_aliases and normalized in LEGACY_PHASE_TYPE_ALIASES:
        return LEGACY_PHASE_TYPE_ALIASES[normalized]
    raise ValueError("phase_type must be 'single', 'three', or null")


class DeviceBase(BaseModel):
    """Base schema with common device fields.
    
    Note: status field is DEPRECATED. Use runtime_status instead.
    Runtime status is computed dynamically based on telemetry activity.
    """
    
    model_config = ConfigDict(str_strip_whitespace=True)
    
    device_name: str = Field(..., min_length=1, max_length=255, description="Human-readable device name")
    device_type: str = Field(..., min_length=1, max_length=100, description="Device type (e.g., bulb, compressor)")
    manufacturer: Optional[str] = Field(None, max_length=255, description="Device manufacturer")
    model: Optional[str] = Field(None, max_length=255, description="Device model")
    location: Optional[str] = Field(None, max_length=500, description="Physical location of device")
    phase_type: Optional[str] = Field(None, description="Electrical phase type: 'single' or 'three'")
    data_source_type: str = Field("metered", description="Telemetry source type: 'metered' or 'sensor'")
    energy_flow_mode: str = Field("consumption_only", description="Power flow mode: 'consumption_only' or 'bidirectional'")
    polarity_mode: str = Field("normal", description="Telemetry polarity mode: 'normal' or 'inverted'")
    
    @field_validator("phase_type", mode="before")
    @classmethod
    def validate_phase_type_field(cls, value: Optional[str]) -> Optional[str]:
        return normalize_phase_type(value, allow_legacy_aliases=False)

    @model_validator(mode='after')
    def validate_phase_type(self) -> 'DeviceBase':
        """Validate phase_type field."""
        if self.data_source_type not in ("metered", "sensor"):
            raise ValueError("data_source_type must be 'metered' or 'sensor'")
        if self.energy_flow_mode not in ("consumption_only", "bidirectional"):
            raise ValueError("energy_flow_mode must be 'consumption_only' or 'bidirectional'")
        if self.polarity_mode not in ("normal", "inverted"):
            raise ValueError("polarity_mode must be 'normal' or 'inverted'")
        return self


class DeviceCreate(DeviceBase):
    """Schema for creating a new device.
    
    Note: status field is DEPRECATED and ignored. Runtime status is computed
    automatically based on telemetry activity (RUNNING/STOPPED).
    """
    
    device_id: Optional[str] = Field(
        None,
        min_length=1,
        max_length=50,
        description="Optional caller-supplied device ID. When omitted, the platform generates one.",
    )
    tenant_id: Optional[str] = Field(None, max_length=50, description="Tenant ID for multi-tenancy")
    plant_id: str = Field(..., min_length=1, description="Plant this device belongs to")
    device_id_class: str = Field(..., description="Generated device ID classification: 'active', 'test', or 'virtual'")
    metadata_json: Optional[str] = Field(None, description="Additional metadata as JSON string")
    
    # DEPRECATED: Status is now computed dynamically from telemetry
    # This field is kept for backward compatibility but ignored
    status: Optional[str] = Field(None, description="DEPRECATED: Ignored. Use runtime_status instead.")

    @model_validator(mode='after')
    def validate_device_id_class(self) -> 'DeviceCreate':
        if self.device_id_class not in ("active", "test", "virtual"):
            raise ValueError("device_id_class must be 'active', 'test', or 'virtual'")
        if self.device_id is not None:
            normalized = self.device_id.strip()
            if not MANUAL_DEVICE_ID_PATTERN.fullmatch(normalized):
                raise ValueError(
                    "device_id may contain letters, numbers, underscore, dash, colon, and dot only"
                )
            self.device_id = normalized
        return self


class DeviceUpdate(BaseModel):
    """Schema for updating an existing device.
    
    Note: status field is DEPRECATED and ignored. Runtime status is computed
    automatically based on telemetry activity.
    """
    
    model_config = ConfigDict(str_strip_whitespace=True)

    device_name: Optional[str] = Field(None, min_length=1, max_length=255)
    device_type: Optional[str] = Field(None, min_length=1, max_length=100)
    manufacturer: Optional[str] = Field(None, max_length=255)
    model: Optional[str] = Field(None, max_length=255)
    location: Optional[str] = Field(None, max_length=500)
    plant_id: str | None = Field(default=None, min_length=1, max_length=36, description="Plant this device belongs to")
    data_source_type: Optional[str] = Field(None, description="Telemetry source type: 'metered' or 'sensor'")
    energy_flow_mode: Optional[str] = Field(None, description="Power flow mode: 'consumption_only' or 'bidirectional'")
    polarity_mode: Optional[str] = Field(None, description="Telemetry polarity mode: 'normal' or 'inverted'")
    # DEPRECATED: Status is now computed dynamically
    status: Optional[str] = Field(None, description="DEPRECATED: Ignored.")
    metadata_json: Optional[str] = Field(None, description="Additional metadata as JSON string")

    @model_validator(mode='after')
    def validate_update_fields(self) -> 'DeviceUpdate':
        if "plant_id" in self.model_fields_set and self.plant_id is None:
            raise ValueError("plant_id cannot be null")
        if self.data_source_type is not None and self.data_source_type not in ("metered", "sensor"):
            raise ValueError("data_source_type must be 'metered' or 'sensor'")
        if self.energy_flow_mode is not None and self.energy_flow_mode not in ("consumption_only", "bidirectional"):
            raise ValueError("energy_flow_mode must be 'consumption_only' or 'bidirectional'")
        if self.polarity_mode is not None and self.polarity_mode not in ("normal", "inverted"):
            raise ValueError("polarity_mode must be 'normal' or 'inverted'")
        return self


class DeviceResponse(DeviceBase):
    """Schema for device response.
    
    Includes both legacy status (deprecated) and runtime_status (computed).
    """
    
    model_config = ConfigDict(from_attributes=True)
    
    device_id: str
    tenant_id: Optional[str] = None
    plant_id: Optional[str] = None
    device_id_class: Optional[str] = None
    metadata_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    first_telemetry_timestamp: Optional[datetime] = None
    full_load_current_a: Optional[float] = None
    idle_threshold_pct_of_fla: float = 0.25

    # Legacy status - DEPRECATED but included for backward compatibility
    legacy_status: str = "active"
    
    # Runtime status - computed dynamically based on telemetry
    runtime_status: str = "stopped"
    last_seen_timestamp: Optional[datetime] = None

    @field_validator("phase_type", mode="before")
    @classmethod
    def normalize_response_phase_type(cls, value: Optional[str]) -> Optional[str]:
        return normalize_phase_type(value, allow_legacy_aliases=True)
    
    @field_validator("first_telemetry_timestamp", mode="after")
    @classmethod
    def normalize_first_telemetry_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class DeviceListResponse(BaseModel):
    """Schema for paginated device list response."""
    
    success: bool = True
    data: list[DeviceResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class DeviceSingleResponse(BaseModel):
    """Schema for single device response."""
    
    success: bool = True
    data: DeviceResponse


class DeviceMqttProvisioningBundle(BaseModel):
    """One-time MQTT provisioning payload returned during onboarding only."""

    broker_host: str
    broker_port: int
    tenant_id: str
    device_id: str
    username: str
    password: str
    publish_topic: str
    status_topic: str
    subscribe_topics: list[str] = Field(default_factory=list)


class DeviceOnboardResponseData(BaseModel):
    """Device onboarding response with the one-time MQTT bundle."""

    device: DeviceResponse
    mqtt: DeviceMqttProvisioningBundle


class DeviceOnboardResponse(BaseModel):
    """Create device and return the one-time MQTT provisioning contract."""

    success: bool = True
    data: DeviceOnboardResponseData


class DeviceMQTTCredentialRequest(BaseModel):
    """Optional metadata accepted during MQTT credential registration/rotation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chip_id: Optional[str] = Field(None, max_length=255, description="Optional future hardware identifier placeholder")


class DeviceMQTTAclResponse(BaseModel):
    """Schema for a single MQTT ACL entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    device_id: str
    mqtt_username: str
    topic: str
    access: str
    permission: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DeviceMQTTCredentialMetadataResponse(BaseModel):
    """Non-secret metadata for a device MQTT credential."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    device_id: str
    mqtt_username: str
    password_algorithm: str
    publish_topic: str
    status_topic: str
    subscribe_topic: Optional[str] = None
    subscribe_topics: list[str] = Field(default_factory=list)
    chip_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    rotated_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    acl_entries: list[DeviceMQTTAclResponse] = Field(default_factory=list)


class DeviceMQTTCredentialSecretResponse(BaseModel):
    """Credential response that includes the one-time plaintext token."""

    credential: DeviceMQTTCredentialMetadataResponse
    mqtt_password: str = Field(..., description="One-time plaintext MQTT token; returned only on create/rotate")


class DeviceMQTTCredentialStatusResponse(BaseModel):
    """Read-only MQTT credential status response."""

    success: bool = True
    data: DeviceMQTTCredentialMetadataResponse


class DeviceMQTTCredentialSecretEnvelope(BaseModel):
    """Create/rotate MQTT credential response envelope."""

    success: bool = True
    data: DeviceMQTTCredentialSecretResponse


class DeviceStateIntervalResponse(BaseModel):
    """Schema for a single device state interval row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    tenant_id: str
    state_type: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_sec: Optional[int] = None
    is_open: bool
    opened_by_sample_ts: Optional[datetime] = None
    closed_by_sample_ts: Optional[datetime] = None
    opened_reason: Optional[str] = None
    closed_reason: Optional[str] = None
    source: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DeviceStateIntervalListResponse(BaseModel):
    """Schema for paginated device interval list response."""

    success: bool = True
    data: list[DeviceStateIntervalResponse]
    total: int
    limit: int
    offset: int


class MaintenanceLogBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    maintenance_date: date
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    cost: Decimal = Field(..., ge=0)
    performed_by: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = Field(None, max_length=50)
    next_due_date: Optional[date] = None

    @model_validator(mode="after")
    def validate_dates(self) -> "MaintenanceLogBase":
        if self.next_due_date is not None and self.next_due_date < self.maintenance_date:
            raise ValueError("next_due_date cannot be earlier than maintenance_date")
        return self


class MaintenanceLogCreate(MaintenanceLogBase):
    pass


class MaintenanceLogUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    maintenance_date: Optional[date] = None
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, min_length=1)
    cost: Optional[Decimal] = Field(None, ge=0)
    performed_by: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = Field(None, max_length=50)
    next_due_date: Optional[date] = None

    @model_validator(mode="after")
    def validate_dates(self) -> "MaintenanceLogUpdate":
        if (
            self.maintenance_date is not None
            and self.next_due_date is not None
            and self.next_due_date < self.maintenance_date
        ):
            raise ValueError("next_due_date cannot be earlier than maintenance_date")
        return self


class MaintenanceLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    device_id: str
    maintenance_date: date
    title: str
    description: str
    cost: Decimal
    performed_by: Optional[str] = None
    status: Optional[str] = None
    next_due_date: Optional[date] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MaintenanceLogListResponse(BaseModel):
    success: bool = True
    data: list[MaintenanceLogResponse]
    total: int


class MaintenanceLogSingleResponse(BaseModel):
    success: bool = True
    data: MaintenanceLogResponse


class MaintenanceLogDeleteResponse(BaseModel):
    success: bool = True
    message: str
    maintenance_log_id: int


class MaintenanceLogSummary(BaseModel):
    total_records: int
    total_cost: Decimal
    latest_maintenance_date: Optional[date] = None
    last_recorded_at: Optional[datetime] = None
    next_due_date: Optional[date] = None


class MaintenanceLogSummaryResponse(BaseModel):
    success: bool = True
    data: MaintenanceLogSummary


class DeviceDeleteResponse(BaseModel):
    """Schema for device deletion response."""
    
    success: bool = True
    message: str
    device_id: str


class ErrorResponse(BaseModel):
    """Schema for error responses."""
    
    success: bool = False
    error: dict
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class HardwareUnitBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    plant_id: str = Field(..., min_length=1, max_length=36)
    unit_type: str = Field(..., min_length=1, max_length=100)
    unit_name: str = Field(..., min_length=1, max_length=255)
    manufacturer: Optional[str] = Field(None, max_length=255)
    model: Optional[str] = Field(None, max_length=255)
    serial_number: Optional[str] = Field(None, max_length=255)
    status: str = Field(default="available")

    @model_validator(mode="after")
    def validate_fields(self) -> "HardwareUnitBase":
        if self.unit_type not in ALLOWED_HARDWARE_UNIT_TYPES:
            raise ValueError(
                "unit_type must be one of: energy_meter, ct_sensor, esp32, oil_sensor, "
                "temperature_sensor, vibration_sensor, motor_sensor"
            )
        if self.status not in {"available", "retired"}:
            raise ValueError("status must be 'available' or 'retired'")
        return self


class HardwareUnitCreate(HardwareUnitBase):
    tenant_id: Optional[str] = Field(None, max_length=50)


class HardwareUnitUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    plant_id: Optional[str] = Field(None, min_length=1, max_length=36)
    unit_type: Optional[str] = Field(None, min_length=1, max_length=100)
    unit_name: Optional[str] = Field(None, min_length=1, max_length=255)
    manufacturer: Optional[str] = Field(None, max_length=255)
    model: Optional[str] = Field(None, max_length=255)
    serial_number: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = None

    @model_validator(mode="after")
    def validate_fields(self) -> "HardwareUnitUpdate":
        if self.unit_type is not None and self.unit_type not in ALLOWED_HARDWARE_UNIT_TYPES:
            raise ValueError(
                "unit_type must be one of: energy_meter, ct_sensor, esp32, oil_sensor, "
                "temperature_sensor, vibration_sensor, motor_sensor"
            )
        if self.status is not None and self.status not in {"available", "retired"}:
            raise ValueError("status must be 'available' or 'retired'")
        return self


class HardwareUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hardware_unit_id: str
    tenant_id: str
    plant_id: str
    unit_type: str
    unit_name: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime


class HardwareUnitListResponse(BaseModel):
    success: bool = True
    data: list[HardwareUnitResponse]
    total: int


class HardwareUnitSingleResponse(BaseModel):
    success: bool = True
    data: HardwareUnitResponse


class DeviceHardwareInstallationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    hardware_unit_id: str = Field(..., min_length=1, max_length=100)
    installation_role: str = Field(..., min_length=1, max_length=100)
    commissioned_at: Optional[datetime] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_installation_role(self) -> "DeviceHardwareInstallationCreate":
        if self.installation_role not in ALLOWED_INSTALLATION_ROLES:
            raise ValueError(
                "installation_role must be one of: main_meter, ct1, ct2, ct3, ct4, "
                "controller, oil_sensor, temperature_sensor, vibration_sensor, motor_sensor"
            )
        return self


class DeviceHardwareInstallationDecommission(BaseModel):
    decommissioned_at: Optional[datetime] = None
    notes: Optional[str] = None


class DeviceHardwareInstallationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    plant_id: str
    device_id: str
    hardware_unit_id: str
    installation_role: str
    commissioned_at: datetime
    decommissioned_at: Optional[datetime] = None
    is_active: bool
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "commissioned_at",
        "decommissioned_at",
        "created_at",
        "updated_at",
        mode="before",
    )
    @classmethod
    def normalize_datetimes(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class DeviceHardwareInstallationSingleResponse(BaseModel):
    success: bool = True
    data: DeviceHardwareInstallationResponse


class DeviceHardwareInstallationHistoryResponse(BaseModel):
    success: bool = True
    data: list[DeviceHardwareInstallationResponse]
    total: int


class DeviceHardwareMappingResponse(BaseModel):
    device_id: str
    plant_id: str
    plant_name: str
    installation_role: str
    installation_role_label: str
    hardware_unit_id: str
    hardware_type: str
    hardware_type_label: str
    hardware_name: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: str
    is_active: bool


class DeviceHardwareMappingListResponse(BaseModel):
    success: bool = True
    data: list[DeviceHardwareMappingResponse]
    total: int


# =====================================================
# Shift Configuration Schemas
# =====================================================

class ShiftBase(BaseModel):
    """Base schema for shift configuration."""
    
    shift_name: str = Field(..., min_length=1, max_length=100, description="Shift name (e.g., Morning Shift)")
    shift_start: time = Field(..., description="Shift start time (HH:MM)")
    shift_end: time = Field(..., description="Shift end time (HH:MM)")
    maintenance_break_minutes: int = Field(default=0, ge=0, le=480, description="Maintenance break duration in minutes")
    day_of_week: Optional[int] = Field(None, ge=0, le=6, description="Day of week (0=Monday, 6=Sunday). Null means all days.")
    is_active: bool = Field(default=True, description="Whether shift is active")


class ShiftCreate(ShiftBase):
    """Schema for creating a new shift."""
    
    device_id: Optional[str] = Field(None, description="Device ID (set automatically from URL)")
    tenant_id: Optional[str] = Field(None, description="Tenant ID (set automatically from header)")


class ShiftUpdate(BaseModel):
    """Schema for updating an existing shift."""
    
    shift_name: Optional[str] = Field(None, min_length=1, max_length=100)
    shift_start: Optional[time] = None
    shift_end: Optional[time] = None
    maintenance_break_minutes: Optional[int] = Field(None, ge=0, le=480)
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    is_active: Optional[bool] = None


class ShiftResponse(ShiftBase):
    """Schema for shift response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    device_id: str
    tenant_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    @property
    def planned_duration_minutes(self) -> int:
        """Calculate total planned shift duration in minutes."""
        start_minutes = self.shift_start.hour * 60 + self.shift_start.minute
        end_minutes = self.shift_end.hour * 60 + self.shift_end.minute
        
        if end_minutes <= start_minutes:
            end_minutes += 24 * 60
            
        return end_minutes - start_minutes
    
    @property
    def effective_runtime_minutes(self) -> int:
        """Calculate effective runtime after maintenance break."""
        return self.planned_duration_minutes - self.maintenance_break_minutes


class ShiftListResponse(BaseModel):
    """Schema for shift list response."""
    
    success: bool = True
    data: list[ShiftResponse]
    total: int


class ShiftSingleResponse(BaseModel):
    """Schema for single shift response."""
    
    success: bool = True
    data: ShiftResponse


class ShiftDeleteResponse(BaseModel):
    """Schema for shift deletion response."""
    
    success: bool = True
    message: str
    shift_id: int


# =====================================================
# Uptime Calculation Schemas
# =====================================================

class UptimeResponse(BaseModel):
    """Schema for uptime response."""
    
    device_id: str
    uptime_percentage: Optional[float] = Field(None, description="Uptime percentage (0-100)")
    total_planned_minutes: int = Field(0, description="Total planned runtime in minutes")
    total_effective_minutes: int = Field(0, description="Total effective runtime (minus maintenance)")
    actual_running_minutes: int = Field(0, description="Actual running minutes from telemetry")
    shifts_configured: int = Field(0, description="Number of shifts configured")
    window_start: Optional[datetime] = Field(None, description="Active shift window start timestamp (IST)")
    window_end: Optional[datetime] = Field(None, description="Active shift window end timestamp (IST)")
    window_timezone: str = Field("Asia/Kolkata", description="Timezone used for shift window")
    data_coverage_pct: float = Field(0.0, description="Telemetry coverage percentage for shift window")
    data_quality: str = Field("low", description="Coverage quality: high | medium | low")
    calculation_mode: str = Field("runtime_telemetry_shift_window", description="Uptime calculation mode")
    message: str = Field(..., description="Status message")
    
    model_config = ConfigDict(from_attributes=True)


# =====================================================
# Health Configuration Schemas
# =====================================================

class ParameterHealthConfigBase(BaseModel):
    """Base schema for parameter health configuration."""
    
    parameter_name: str = Field(..., min_length=1, max_length=100, description="Parameter name (e.g., pressure, temperature)")
    normal_min: Optional[float] = Field(None, description="Normal range minimum")
    normal_max: Optional[float] = Field(None, description="Normal range maximum")
    weight: float = Field(default=0.0, ge=0, le=100, description="Weight percentage (0-100)")
    ignore_zero_value: bool = Field(default=False, description="Ignore zero values for this parameter")
    is_active: bool = Field(default=True, description="Whether this parameter is active for health scoring")


class ParameterHealthConfigCreate(ParameterHealthConfigBase):
    """Schema for creating parameter health configuration."""
    
    device_id: Optional[str] = Field(None, description="Device ID (set automatically from URL)")
    tenant_id: Optional[str] = Field(None, description="Tenant ID (set automatically from header)")

    @field_validator("normal_min", "normal_max", "weight")
    @classmethod
    def validate_finite_numeric_fields(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if not math.isfinite(value):
            raise ValueError("numeric health configuration values must be finite")
        return value

    @model_validator(mode="after")
    def validate_parameter_range(self) -> "ParameterHealthConfigCreate":
        if (
            self.normal_min is not None
            and self.normal_max is not None
            and self.normal_min > self.normal_max
        ):
            raise ValueError("normal_min cannot be greater than normal_max")
        return self


class ParameterHealthConfigUpdate(BaseModel):
    """Schema for updating parameter health configuration."""
    
    parameter_name: Optional[str] = Field(None, min_length=1, max_length=100)
    normal_min: Optional[float] = None
    normal_max: Optional[float] = None
    weight: Optional[float] = Field(None, ge=0, le=100)
    ignore_zero_value: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("normal_min", "normal_max", "weight")
    @classmethod
    def validate_finite_numeric_fields(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if not math.isfinite(value):
            raise ValueError("numeric health configuration values must be finite")
        return value

    @model_validator(mode="after")
    def validate_parameter_range(self) -> "ParameterHealthConfigUpdate":
        if (
            self.normal_min is not None
            and self.normal_max is not None
            and self.normal_min > self.normal_max
        ):
            raise ValueError("normal_min cannot be greater than normal_max")
        return self


class ParameterHealthConfigResponse(ParameterHealthConfigBase):
    """Schema for parameter health configuration response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    device_id: str
    tenant_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ParameterHealthConfigListResponse(BaseModel):
    """Schema for health configuration list response."""
    
    success: bool = True
    data: list[ParameterHealthConfigResponse]
    total: int


class ParameterHealthConfigSingleResponse(BaseModel):
    """Schema for single health configuration response."""
    
    success: bool = True
    data: ParameterHealthConfigResponse


class WeightValidationResponse(BaseModel):
    """Schema for weight validation response."""
    
    is_valid: bool
    total_weight: float
    message: str
    parameters: list[dict]


# =====================================================
# Health Score Calculation Schemas
# =====================================================

class TelemetryValues(BaseModel):
    """Schema for telemetry values for health calculation."""
    
    values: dict[str, float] = Field(..., description="Dictionary of parameter names to values")
    machine_state: Optional[str] = Field(
        "RUNNING",
        description="Machine operational state used for health-score eligibility. Health scoring runs for RUNNING, IDLE, and UNLOAD; OFF and POWER CUT return standby.",
    )


class ParameterScore(BaseModel):
    """Schema for individual parameter score."""
    
    parameter_name: str
    telemetry_key: Optional[str] = None
    value: Optional[float] = None
    raw_score: Optional[float] = None
    weighted_score: float
    weight: float
    status: str
    status_color: str
    resolution: Optional[str] = None
    included_in_score: bool = True


class HealthScoreResponse(BaseModel):
    """Schema for health score response."""
    
    device_id: str
    health_score: Optional[float] = None
    status: str
    status_color: str
    message: str
    machine_state: str
    parameter_scores: list[ParameterScore]
    total_weight_configured: float
    parameters_included: int
    parameters_skipped: int


# =====================================================
# Device Property Schemas (Dynamic Schema Discovery)
# =====================================================

class DevicePropertyResponse(BaseModel):
    """Schema for device property response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    device_id: str
    property_name: str
    data_type: str
    is_numeric: bool
    discovered_at: datetime
    last_seen_at: datetime


class DevicePropertyListResponse(BaseModel):
    """Schema for device property list response."""
    
    success: bool = True
    data: list[DevicePropertyResponse]
    total: int


class DevicePropertiesRequest(BaseModel):
    """Request schema for getting properties for specific devices."""
    
    device_ids: list[str] = Field(..., description="List of device IDs to get properties for")


class CommonPropertiesResponse(BaseModel):
    """Response schema for common properties across devices."""
    
    success: bool = True
    properties: list[str] = Field(..., description="List of common property names")
    device_count: int = Field(..., description="Number of devices considered")
    message: str


class AllDevicesPropertiesResponse(BaseModel):
    """Response for all devices properties (for dropdown population)."""
    
    success: bool = True
    devices: dict[str, list[str]] = Field(..., description="Device ID to properties mapping")
    all_properties: list[str] = Field(..., description="All unique properties across devices")


class DashboardWidgetConfigUpdateRequest(BaseModel):
    """Idempotent replace request for dashboard widget fields."""

    selected_fields: list[str] = Field(default_factory=list, description="Ordered list of selected widget field names")


class DashboardWidgetConfigResponse(BaseModel):
    """Widget visibility configuration response for a device dashboard."""

    success: bool = True
    device_id: str
    available_fields: list[str] = Field(default_factory=list)
    selected_fields: list[str] = Field(default_factory=list)
    effective_fields: list[str] = Field(default_factory=list)
    default_applied: bool = True


# =====================================================
# Performance Trends Schemas
# =====================================================

class PerformanceTrendPoint(BaseModel):
    """Schema for one performance trend point."""

    timestamp: str
    health_score: Optional[float] = None
    uptime_percentage: Optional[float] = None
    planned_minutes: int = 0
    effective_minutes: int = 0
    break_minutes: int = 0


class PerformanceTrendFallbackPoint(BaseModel):
    """Last known valid point outside the selected window."""

    timestamp: str
    value: float


class PerformanceTrendResponse(BaseModel):
    """Schema for performance trend response."""

    success: bool = True
    device_id: str
    metric: str
    range: str
    interval_minutes: int
    timezone: str
    points: list[PerformanceTrendPoint]
    total_points: int
    sampled_points: int
    message: str
    metric_message: str
    range_start: str
    range_end: str
    is_stale: bool = False
    last_actual_timestamp: Optional[str] = None
    fallback_point: Optional[PerformanceTrendFallbackPoint] = None


# =====================================================
# Home Dashboard Schemas
# =====================================================

class DashboardDeviceItem(BaseModel):
    """Per-device card data for home dashboard."""

    device_id: str
    device_name: str
    device_type: str
    runtime_status: str
    operational_status: str = "unknown"
    location: Optional[str] = None
    first_telemetry_timestamp: Optional[datetime] = None
    last_seen_timestamp: Optional[datetime] = None
    health_score: Optional[float] = None
    uptime_percentage: Optional[float] = None
    daily_uptime_percentage: Optional[float] = None

    @field_validator("first_telemetry_timestamp", mode="after")
    @classmethod
    def normalize_first_telemetry_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class DashboardOperationalStatusCounts(BaseModel):
    unknown: int = 0
    stopped: int = 0
    idle: int = 0
    running: int = 0
    overconsumption: int = 0


class DashboardAlertsSummary(BaseModel):
    """System-level alert aggregates."""

    active_alerts: int = 0
    alerts_triggered: int = 0
    alerts_cleared: int = 0
    rules_created: int = 0


class DashboardSystemSummary(BaseModel):
    """Top-level KPI aggregates."""

    total_devices: int = 0
    running_devices: int = 0
    stopped_devices: int = 0
    idle_devices: int = 0
    in_load_devices: int = 0
    overconsumption_devices: int = 0
    unknown_devices: int = 0
    status_counts: DashboardOperationalStatusCounts = Field(default_factory=DashboardOperationalStatusCounts)
    devices_with_health_data: int = 0
    devices_with_health_configured: int = 0
    devices_missing_health_config: int = 0
    devices_with_uptime_configured: int = 0
    devices_missing_uptime_config: int = 0
    system_health: Optional[float] = None
    average_efficiency: Optional[float] = None


class DashboardSummaryResponse(BaseModel):
    """Home dashboard API response."""

    success: bool = True
    generated_at: datetime
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)
    degraded_services: list[str] = Field(default_factory=list)
    summary: DashboardSystemSummary
    alerts: DashboardAlertsSummary
    devices: list[DashboardDeviceItem]
    energy_widgets: Optional[dict] = None
    cost_data_state: str = "unavailable"
    cost_data_reasons: list[str] = Field(default_factory=list)
    cost_generated_at: Optional[datetime] = None


class FleetSnapshotItem(BaseModel):
    device_id: str
    device_name: str
    device_type: str
    plant_id: Optional[str] = None
    runtime_status: str
    load_state: str = "unknown"
    current_band: Optional[str] = None
    operational_status: str = "unknown"
    location: Optional[str] = None
    first_telemetry_timestamp: Optional[datetime] = None
    last_seen_timestamp: Optional[datetime] = None
    health_score: Optional[float] = None
    has_uptime_config: bool = False
    data_freshness_ts: Optional[datetime] = None
    version: int = 0

    @field_validator("first_telemetry_timestamp", mode="after")
    @classmethod
    def normalize_first_telemetry_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class FleetSnapshotResponse(BaseModel):
    success: bool = True
    generated_at: datetime
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)
    degraded_services: list[str] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    total_pages: int
    devices: list[FleetSnapshotItem]


class DeviceDashboardBootstrapSummaryResponse(BaseModel):
    class CO2FactorMeta(BaseModel):
        value: float = 0.0
        unit: str = "kg_co2_per_kwh"
        method: Optional[str] = None
        country: Optional[str] = None
        region: Optional[str] = None
        source: Optional[str] = None
        source_version: Optional[str] = None
        factor_year: Optional[str] = None

    class CO2PeriodToday(BaseModel):
        energy_kwh: float = 0.0
        co2_kg: float = 0.0
        loss_kwh: float = 0.0
        avoidable_co2_kg: Optional[float] = None
        available: bool = True
        avoidable_co2_available: bool = False
        avoidable_co2_reason: Optional[str] = None

    class CO2PeriodWeek(BaseModel):
        available: bool = False
        reason: Optional[str] = None

    class CO2PeriodMonth(BaseModel):
        energy_kwh: float = 0.0
        co2_kg: float = 0.0
        available: bool = True
        avoidable_co2_available: bool = False
        avoidable_co2_reason: Optional[str] = None

    class CO2Overview(BaseModel):
        available: bool = False
        reason: Optional[str] = None
        today: Optional["DeviceDashboardBootstrapSummaryResponse.CO2PeriodToday"] = None
        week: Optional["DeviceDashboardBootstrapSummaryResponse.CO2PeriodWeek"] = None
        month: Optional["DeviceDashboardBootstrapSummaryResponse.CO2PeriodMonth"] = None
        factor: Optional["DeviceDashboardBootstrapSummaryResponse.CO2FactorMeta"] = None
        factor_source: str = "unconfigured"
        calculation_version: str = "co2_scope2_v1"

    class LossOverview(BaseModel):
        day_bucket: Optional[date] = None
        updated_at: Optional[datetime] = None
        last_telemetry_ts: Optional[datetime] = None
        currency: str = "INR"
        costs_available: bool = False
        idle_kwh: float = 0.0
        idle_cost_inr: Optional[float] = None
        off_hours_kwh: float = 0.0
        off_hours_cost_inr: Optional[float] = None
        overconsumption_kwh: float = 0.0
        overconsumption_cost_inr: Optional[float] = None
        total_loss_kwh: float = 0.0
        total_loss_cost_inr: Optional[float] = None
        today_energy_kwh: float = 0.0
        co2_overview: Optional["DeviceDashboardBootstrapSummaryResponse.CO2Overview"] = None

    class OverviewReadiness(BaseModel):
        summary_ready: bool = True
        telemetry_ready: bool = False
        health_ready: bool = False
        uptime_ready: bool = False
        loss_ready: bool = False

    success: bool = True
    generated_at: datetime
    version: int = 0
    device_id: str
    device_name: str
    device_type: str = ""
    plant_id: Optional[str] = None
    location: Optional[str] = None
    runtime_status: str = "stopped"
    load_state: str = "unknown"
    current_band: str = "unknown"
    operational_status: str = "unknown"
    last_seen_timestamp: Optional[datetime] = None
    first_telemetry_timestamp: Optional[datetime] = None
    health_score: Optional[float] = None
    uptime_percentage: Optional[float] = None
    current_shift_uptime_percentage: Optional[float] = None
    daily_uptime_percentage: Optional[float] = None
    full_load_current_a: Optional[float] = None
    idle_threshold_pct_of_fla: Optional[float] = None
    derived_idle_threshold_a: Optional[float] = None
    derived_overconsumption_threshold_a: Optional[float] = None
    last_current_a: Optional[float] = None
    last_voltage_v: Optional[float] = None
    data_source_type: Optional[str] = None
    data_freshness_ts: Optional[datetime] = None
    live_updated_at: Optional[datetime] = None
    loss_overview: Optional[LossOverview] = None
    overview_readiness: OverviewReadiness = Field(default_factory=OverviewReadiness)


class DeviceDashboardBootstrapResponse(BaseModel):
    success: bool = True
    generated_at: datetime
    version: int = 0
    device: Optional[DeviceResponse] = None
    telemetry: list[dict] = Field(default_factory=list)
    telemetry_business: Optional[dict] = None
    uptime: dict = Field(default_factory=dict)
    shifts: list[dict] = Field(default_factory=list)
    health_configs: list[dict] = Field(default_factory=list)
    health_score: Optional[dict] = None
    widget_config: Optional[dict] = None
    current_state: Optional[dict] = None
    idle_stats: Optional[dict] = None
    idle_config: Optional[dict] = None
    waste_config: Optional[dict] = None
    loss_stats: Optional[dict] = None
    co2_overview: Optional[dict] = None


class DeviceDetailSnapshotResponse(BaseModel):
    class Availability(BaseModel):
        snapshot_ready: bool = False
        health_score_ready: bool = False
        widget_config_ready: bool = False
        health_configs_ready: bool = False
        recent_telemetry_ready: bool = False
        stale: bool = False

    class Snapshot(BaseModel):
        sample_ts: Optional[datetime] = None
        projection_version: int = 0
        snapshot_version: int = 0
        runtime_status: str = "stopped"
        load_state: str = "unknown"
        current_band: str = "unknown"
        last_power_kw: Optional[float] = None
        last_current_a: Optional[float] = None
        last_voltage_v: Optional[float] = None
        numeric_fields: dict[str, float] = Field(default_factory=dict)
        source_fields: dict[str, str | None] = Field(default_factory=dict)
        normalization_version: Optional[str] = None
        updated_at: Optional[datetime] = None

    success: bool = True
    generated_at: datetime
    device_id: str
    data_freshness_ts: Optional[datetime] = None
    freshness_age_seconds: Optional[int] = None
    availability: Availability = Field(default_factory=Availability)
    snapshot: Optional[Snapshot] = None
    health_score: Optional[dict] = None
    health_configs: list[dict] = Field(default_factory=list)
    widget_config: Optional[dict] = None
    recent_telemetry: list[dict] = Field(default_factory=list)


class DashboardEnergyWidgets(BaseModel):
    month_energy_kwh: float = 0.0
    month_energy_cost_inr: float = 0.0
    today_energy_kwh: float = 0.0
    today_energy_cost_inr: float = 0.0
    today_loss_kwh: float = 0.0
    today_loss_cost_inr: float = 0.0
    generated_at: datetime
    currency: str = "INR"
    data_quality: str = "ok"
    invariant_checks: dict = Field(default_factory=dict)
    reconciliation_warning: Optional[str] = None
    no_nan_inf: bool = True


class TodayLossBreakdownTotals(BaseModel):
    idle_kwh: float = 0.0
    idle_cost_inr: float = 0.0
    off_hours_kwh: float = 0.0
    off_hours_cost_inr: float = 0.0
    overconsumption_kwh: float = 0.0
    overconsumption_cost_inr: float = 0.0
    total_loss_kwh: float = 0.0
    total_loss_cost_inr: float = 0.0
    today_energy_kwh: float = 0.0
    today_energy_cost_inr: float = 0.0


class TodayLossBreakdownRow(BaseModel):
    device_id: str
    device_name: str
    idle_kwh: float = 0.0
    idle_cost_inr: float = 0.0
    off_hours_kwh: float = 0.0
    off_hours_cost_inr: float = 0.0
    overconsumption_kwh: float = 0.0
    overconsumption_cost_inr: float = 0.0
    total_loss_kwh: float = 0.0
    total_loss_cost_inr: float = 0.0
    status: str = "computed"
    reason: Optional[str] = None


class TodayLossBreakdownResponse(BaseModel):
    success: bool = True
    generated_at: datetime
    stale: bool = False
    currency: str = "INR"
    totals: TodayLossBreakdownTotals
    rows: list[TodayLossBreakdownRow]
    data_quality: str = "ok"
    invariant_checks: dict = Field(default_factory=dict)
    no_nan_inf: bool = True
    warnings: list[str] = Field(default_factory=list)
    cost_data_state: str = "unavailable"
    cost_data_reasons: list[str] = Field(default_factory=list)
    cost_generated_at: Optional[datetime] = None


class CalendarDayEnergy(BaseModel):
    date: str
    energy_kwh: float = 0.0
    energy_cost_inr: float = 0.0
    loss_kwh: float = 0.0
    loss_cost_inr: float = 0.0


class CalendarMonthSummary(BaseModel):
    total_energy_kwh: float = 0.0
    total_energy_cost_inr: float = 0.0


class MonthlyEnergyCalendarResponse(BaseModel):
    success: bool = True
    year: int
    month: int
    currency: str = "INR"
    generated_at: datetime
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)
    summary: CalendarMonthSummary
    days: list[CalendarDayEnergy]
    data_quality: str = "ok"
    no_nan_inf: bool = True
    cost_data_state: str = "unavailable"
    cost_data_reasons: list[str] = Field(default_factory=list)
    cost_generated_at: Optional[datetime] = None


class FleetStreamEvent(BaseModel):
    id: Optional[str] = None
    event: str = "fleet_update"
    generated_at: datetime
    freshness_ts: Optional[datetime] = None
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)
    devices: list[FleetSnapshotItem]
    partial: bool = False
    version: int = 0


class DegradationContributionResponse(BaseModel):
    signal: str
    weight: float
    drift: float
    available: bool = True
    observed_value: Optional[float] = None
    baseline_value: Optional[float] = None
    raw_drift: Optional[float] = None


class DegradationScoreTrendPoint(BaseModel):
    computed_at: datetime
    score: float
    status: str
    contributions: Optional[list[DegradationContributionResponse]] = None


class DegradationScoreResponse(BaseModel):
    device_id: str
    available: bool
    state: str
    score: Optional[float] = None
    status: Optional[str] = None
    confidence: Optional[float] = None
    signal_completeness: Optional[float] = None
    baseline_quality: Optional[str] = None
    top_reasons: list[str] = Field(default_factory=list)
    contributions: list[DegradationContributionResponse] = Field(default_factory=list)
    score_trend: list[DegradationScoreTrendPoint] = Field(default_factory=list)
    computed_at: Optional[datetime] = None
    updated_minutes_ago: Optional[float] = None


class AnomalyCountBreakdown(BaseModel):
    total: int = 0
    mild: int = 0
    strong: int = 0
    severe: int = 0
    supply_related: int = 0


class AnomalySignalCount(BaseModel):
    field_name: str
    count: int = 0
    mild: int = 0
    strong: int = 0
    severe: int = 0


class AnomalyBaselineSignalStatus(BaseModel):
    field_name: str
    status: str
    quality_score: Optional[float] = None


class AnomalyEventItem(BaseModel):
    occurred_at: datetime
    signal_field: str
    severity: str
    anomaly_type: str
    supply_related: bool = False
    signal_value: Optional[float] = None
    baseline_mean: Optional[float] = None
    z_score: Optional[float] = None
    duration_seconds: Optional[int] = None
    ended_at: Optional[datetime] = None
    confidence: Optional[float] = None
    startup_adjacent: bool = False
    mode_change: bool = False
    recurring: bool = False


AnomalyEventSummary = AnomalyEventItem


class AnomalyEventListResponse(BaseModel):
    items: list[AnomalyEventItem] = Field(default_factory=list)
    limit: int = 20
    offset: int = 0
    total: int = 0


class AnomalyActivityResponse(BaseModel):
    device_id: str
    available: bool
    state: str
    today_counts: Optional[AnomalyCountBreakdown] = None
    this_week_counts: Optional[AnomalyCountBreakdown] = None
    this_month_counts: Optional[AnomalyCountBreakdown] = None
    week_over_week_change: Optional[int] = None
    top_signal: Optional[str] = None
    avg_confidence: Optional[float] = None
    last_anomaly: Optional[AnomalyEventItem] = None
    baseline_status: Optional[str] = None
    baseline_field_count: Optional[int] = None
    baseline_quality: Optional[str] = None
    computed_at: Optional[datetime] = None
    updated_minutes_ago: Optional[float] = None
    signal_breakdown: list[AnomalySignalCount] = Field(default_factory=list)
    baseline_signals: list[AnomalyBaselineSignalStatus] = Field(default_factory=list)
