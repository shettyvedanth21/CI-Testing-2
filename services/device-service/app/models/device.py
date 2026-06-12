"""SQLAlchemy models for Device Service."""

from datetime import datetime, time, timezone, timedelta, date
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import String, DateTime, Text, Integer, ForeignKey, ForeignKeyConstraint, Time, Float, Boolean, UniqueConstraint, Numeric, BigInteger, Index, Date, Enum as SAEnum, event, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base

# Configurable timeout threshold for telemetry (in seconds)
TELEMETRY_TIMEOUT_SECONDS = 60  # Device considered STOPPED if no telemetry for 60 seconds
TENANT_ID_LENGTH = 10


class DeviceStatus(str, Enum):
    """Device status enumeration (LEGACY - for backward compatibility only)."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"
    ERROR = "error"


class RuntimeStatus(str, Enum):
    """Runtime status derived from telemetry activity."""
    RUNNING = "running"
    STOPPED = "stopped"


class PhaseType(str, Enum):
    """Electrical phase type for devices.
    
    Used for energy reporting to distinguish between:
    - single: Single-phase equipment
    - three: Three-phase equipment
    """
    SINGLE = "single"
    THREE = "three"


class DataSourceType(str, Enum):
    """Data source type used by reporting calculations."""
    METERED = "metered"
    SENSOR = "sensor"


class EnergyFlowMode(str, Enum):
    CONSUMPTION_ONLY = "consumption_only"
    BIDIRECTIONAL = "bidirectional"


class PolarityMode(str, Enum):
    NORMAL = "normal"
    INVERTED = "inverted"


class DeviceIdClass(str, Enum):
    """Classification used to allocate generated device identifiers."""

    ACTIVE = "active"
    TEST = "test"
    VIRTUAL = "virtual"


class DeviceStateIntervalType(str, Enum):
    """Durable interval states emitted by the live projection/runtime pipeline."""

    IDLE = "idle"
    OVERCONSUMPTION = "overconsumption"
    RUNTIME_ON = "runtime_on"


class DeviceMQTTPasswordAlgorithm(str, Enum):
    """Supported hashing algorithms for MQTT device secrets."""

    SHA256 = "sha256"


class DeviceMQTTAccess(str, Enum):
    """MQTT ACL access mode."""

    PUBLISH = "publish"
    SUBSCRIBE = "subscribe"


class DeviceMQTTPermission(str, Enum):
    """MQTT ACL permission mode."""

    ALLOW = "allow"
    DENY = "deny"


def _normalize_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AwareTimestamp(TypeDecorator):
    """Timezone-safe timestamp type that normalizes all values to UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return _normalize_aware_utc(value)

    def process_result_value(self, value, dialect):
        return _normalize_aware_utc(value)


class DeviceIdSequence(Base):
    """Persistent per-prefix allocator state for generated device IDs."""

    __tablename__ = "device_id_sequences"

    prefix: Mapped[str] = mapped_column(String(2), primary_key=True)
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class HardwareUnitSequence(Base):
    """Persistent allocator state for generated hardware unit IDs."""

    __tablename__ = "hardware_unit_sequences"

    prefix: Mapped[str] = mapped_column(String(3), primary_key=True)
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class Device(Base):
    """Device model representing IoT devices in the system.
    
    This model is designed to be multi-tenant ready. The tenant_id field
    is included for future multi-tenancy support but is nullable for Phase-1.
    
    Runtime status is computed dynamically based on last_seen_timestamp:
    - RUNNING: telemetry received within TELEMETRY_TIMEOUT_SECONDS
    - STOPPED: no telemetry received within TELEMETRY_TIMEOUT_SECONDS or never received
    """
    
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("device_id", name="uq_devices_device_id"),
    )
    
    # Primary key - using business key for device_id
    device_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    
    # Multi-tenant support
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), primary_key=True, nullable=False, index=True)
    plant_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        comment="Soft ref to plants.id in auth-service",
    )
    
    # Device metadata
    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    device_id_class: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # Phase type - electrical configuration for energy reporting
    # This is static metadata, not telemetry-derived
    phase_type: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        index=True
    )

    # Report source type metadata
    data_source_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DataSourceType.METERED.value,
        index=True,
    )
    energy_flow_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EnergyFlowMode.CONSUMPTION_ONLY.value,
        index=True,
    )
    polarity_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PolarityMode.NORMAL.value,
        index=True,
    )

    # Idle detection threshold in amperes (per-device configuration)
    idle_current_threshold: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )
    overconsumption_current_threshold_a: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )
    full_load_current_a: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )
    idle_threshold_pct_of_fla: Mapped[float] = mapped_column(
        Numeric(6, 4),
        nullable=False,
        default=0.25,
    )
    unoccupied_weekday_start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    unoccupied_weekday_end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    unoccupied_weekend_start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    unoccupied_weekend_end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    
    # Legacy status field - DEPRECATED
    # This field is kept for backward compatibility only.
    # Do NOT use for runtime display - use get_runtime_status() instead.
    legacy_status: Mapped[str] = mapped_column(
        String(50),
        default="active",
        nullable=False,
        index=True
    )
    
    # Last seen timestamp - tracks when telemetry was last received
    # This is the source of truth for runtime status
    last_seen_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True
    )

    # Immutable activation timestamp derived from the first telemetry sample
    # ever observed after onboarding. This is written once and never mutated.
    first_telemetry_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    
    # Extended metadata as JSON (for future extensibility)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    
    # Soft delete support (for future)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    shifts: Mapped[list["DeviceShift"]] = relationship(
        "DeviceShift", 
        back_populates="device", 
        cascade="all, delete-orphan",
        lazy="selectin"
    )

    health_configs: Mapped[list["ParameterHealthConfig"]] = relationship(
        "ParameterHealthConfig",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

    hardware_installations: Mapped[list["DeviceHardwareInstallation"]] = relationship(
        "DeviceHardwareInstallation",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="selectin",
        overlaps="hardware_unit,installations",
    )
    mqtt_credentials: Mapped[list["DeviceMQTTCredential"]] = relationship(
        "DeviceMQTTCredential",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    maintenance_logs: Mapped[list["MaintenanceLog"]] = relationship(
        "MaintenanceLog",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    
    def __repr__(self) -> str:
        return f"<Device(device_id={self.device_id}, name={self.device_name}, type={self.device_type})>"
    
    @property
    def status(self) -> str:
        """Legacy status property for backward compatibility.
        
        DEPRECATED: Use get_runtime_status() instead.
        """
        return self.legacy_status
    
    @property
    def runtime_status(self) -> str:
        """Computed runtime status property for API responses.
        
        This is a computed property that returns the runtime status
        based on last_seen_timestamp. It is used by the ORM when
        serializing to JSON.
        """
        return self.get_runtime_status()
    
    def get_runtime_status(self) -> str:
        """Compute runtime status based on telemetry activity.
        
        Returns:
            'running' - if telemetry received within TELEMETRY_TIMEOUT_SECONDS
            'stopped' - if no telemetry received within TELEMETRY_TIMEOUT_SECONDS or never received
        """
        if self.last_seen_timestamp is None:
            return RuntimeStatus.STOPPED.value
        
        now = datetime.now(timezone.utc)
        
        # Handle naive datetime from database
        last_seen = self.last_seen_timestamp
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        
        time_diff = (now - last_seen).total_seconds()
        
        if time_diff <= TELEMETRY_TIMEOUT_SECONDS:
            return RuntimeStatus.RUNNING.value
        else:
            return RuntimeStatus.STOPPED.value
    
    def is_running(self) -> bool:
        """Check if device is currently running (receiving telemetry)."""
        return self.get_runtime_status() == RuntimeStatus.RUNNING.value
    
    def is_stopped(self) -> bool:
        """Check if device is currently stopped (not receiving telemetry)."""
        return self.get_runtime_status() == RuntimeStatus.STOPPED.value
    
    def update_last_seen(self) -> None:
        """Update last_seen_timestamp to current time.
        
        Called when telemetry is received for this device.
        """
        self.last_seen_timestamp = datetime.now(timezone.utc)


class DeviceMQTTCredential(Base):
    """Per-device MQTT credential state for broker-side authentication."""

    __tablename__ = "device_mqtt_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "device_id", name="uq_device_mqtt_credentials_device"),
        UniqueConstraint("mqtt_username", name="uq_device_mqtt_credentials_username"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    mqtt_username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    password_algorithm: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DeviceMQTTPasswordAlgorithm.SHA256.value,
    )
    publish_topic: Mapped[str] = mapped_column(String(255), nullable=False)
    subscribe_topic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    chip_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    rotated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped["Device"] = relationship(
        "Device",
        back_populates="mqtt_credentials",
    )
    acl_entries: Mapped[list["DeviceMQTTACL"]] = relationship(
        "DeviceMQTTACL",
        back_populates="credential",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class MaintenanceLog(Base):
    """Durable maintenance history for a tenant-scoped device."""

    __tablename__ = "maintenance_logs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    maintenance_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    performed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    next_due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", back_populates="maintenance_logs", lazy="selectin")

    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_maintenance_logs_tenant_device_date",
            "tenant_id",
            "device_id",
            "maintenance_date",
        ),
        Index(
            "ix_maintenance_logs_tenant_device_next_due",
            "tenant_id",
            "device_id",
            "next_due_date",
        ),
        Index(
            "ix_maintenance_logs_tenant_status",
            "tenant_id",
            "status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MaintenanceLog(id={self.id}, tenant_id={self.tenant_id}, "
            f"device_id={self.device_id}, maintenance_date={self.maintenance_date})>"
        )


class DeviceMQTTACL(Base):
    """Explicit MQTT authorization intent for a device credential."""

    __tablename__ = "device_mqtt_acl"
    __table_args__ = (
        UniqueConstraint(
            "credential_id",
            "topic",
            "access",
            "permission",
            name="uq_device_mqtt_acl_rule",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    credential_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("device_mqtt_credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    mqtt_username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    access: Mapped[str] = mapped_column(String(32), nullable=False)
    permission: Mapped[str] = mapped_column(String(32), nullable=False, default=DeviceMQTTPermission.ALLOW.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    credential: Mapped["DeviceMQTTCredential"] = relationship(
        "DeviceMQTTCredential",
        back_populates="acl_entries",
    )


class DeviceShift(Base):
    """Shift configuration for device uptime calculation.
    
    Supports multiple shifts per day per device.
    Each shift has planned start/end times and optional maintenance break.
    """
    
    __tablename__ = "device_shifts"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Foreign key to device
    device_id: Mapped[str] = mapped_column(
        String(50), 
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Tenant for multi-tenancy
    tenant_id: Mapped[Optional[str]] = mapped_column(String(TENANT_ID_LENGTH), nullable=True, index=True)
    
    # Shift identification
    shift_name: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Planned times (stored as time for date-agnostic scheduling)
    shift_start: Mapped[time] = mapped_column(Time, nullable=False)
    shift_end: Mapped[time] = mapped_column(Time, nullable=False)
    
    # Maintenance break duration in minutes
    maintenance_break_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Day of week (0=Monday, 6=Sunday). Null means all days.
    day_of_week: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Active flag
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    
    # Relationship
    device: Mapped["Device"] = relationship("Device", back_populates="shifts")
    
    def __repr__(self) -> str:
        return f"<DeviceShift(id={self.id}, device_id={self.device_id}, shift_name={self.shift_name})>"
    
    @property
    def planned_duration_minutes(self) -> int:
        """Calculate total planned shift duration in minutes."""
        start_minutes = self.shift_start.hour * 60 + self.shift_start.minute
        end_minutes = self.shift_end.hour * 60 + self.shift_end.minute
        
        if end_minutes <= start_minutes:
            # Shift crosses midnight
            end_minutes += 24 * 60
            
        return end_minutes - start_minutes
    
    @property
    def effective_runtime_minutes(self) -> int:
        """Calculate effective runtime after maintenance break."""
        return self.planned_duration_minutes - self.maintenance_break_minutes


class ParameterHealthConfig(Base):
    """Parameter health configuration for device health scoring.
    
    Each parameter can have configurable ranges and weights for health calculation.
    """
    
    __tablename__ = "parameter_health_config"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    device_id: Mapped[str] = mapped_column(
        String(50), 
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    tenant_id: Mapped[Optional[str]] = mapped_column(String(TENANT_ID_LENGTH), nullable=True, index=True)
    
    parameter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    canonical_parameter_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    
    normal_min: Mapped[Optional[float]] = mapped_column(nullable=True)
    normal_max: Mapped[Optional[float]] = mapped_column(nullable=True)
    
    weight: Mapped[float] = mapped_column(default=0.0, nullable=False)
    
    ignore_zero_value: Mapped[bool] = mapped_column(default=False, nullable=False)
    
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    
    device: Mapped["Device"] = relationship("Device", back_populates="health_configs")
    
    def __repr__(self) -> str:
        return f"<ParameterHealthConfig(id={self.id}, device_id={self.device_id}, parameter={self.parameter_name})>"


_HEALTH_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "current": ("current_a", "phase_current"),
    "power": ("active_power", "active_power_kw", "business_power_w", "power_kw", "kw"),
    "power_factor": ("pf", "cos_phi", "powerfactor", "pf_business", "raw_power_factor"),
    "voltage": ("voltage_v",),
}
_HEALTH_ALIASES_TO_CANONICAL = {
    alias.casefold(): canonical
    for canonical, aliases in _HEALTH_PARAMETER_ALIASES.items()
    for alias in aliases
}


def canonicalize_health_parameter_name(parameter_name: Optional[str]) -> str:
    normalized = str(parameter_name or "").strip().casefold()
    return _HEALTH_ALIASES_TO_CANONICAL.get(normalized, normalized)


@event.listens_for(ParameterHealthConfig, "before_insert")
@event.listens_for(ParameterHealthConfig, "before_update")
def _sync_parameter_health_canonical_name(_mapper, _connection, target: ParameterHealthConfig) -> None:
    target.canonical_parameter_name = canonicalize_health_parameter_name(target.parameter_name)


class DevicePerformanceTrend(Base):
    """Materialized trend snapshots for device performance charts."""

    __tablename__ = "device_performance_trends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    device_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)

    bucket_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    bucket_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    health_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    uptime_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    planned_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    effective_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    break_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    points_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("device_id", "tenant_id", "bucket_start_utc", name="uq_perf_trend_device_bucket"),
    )

    def __repr__(self) -> str:
        return f"<DevicePerformanceTrend(device_id={self.device_id}, bucket={self.bucket_start_utc})>"


class DeviceProperty(Base):
    """Dynamic device properties discovered from telemetry.
    
    This table stores the properties (fields) discovered from each device's
    telemetry data. Used for dynamic rule property selection.
    """
    
    __tablename__ = "device_properties"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    device_id: Mapped[str] = mapped_column(
        String(50), 
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    
    property_name: Mapped[str] = mapped_column(String(100), nullable=False)
    
    data_type: Mapped[str] = mapped_column(String(20), default="float", nullable=False)
    
    is_numeric: Mapped[bool] = mapped_column(default=True, nullable=False)
    
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    
    __table_args__ = (
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )
    
    def __repr__(self) -> str:
        return f"<DeviceProperty(device_id={self.device_id}, property={self.property_name})>"


class DeviceDashboardWidget(Base):
    """Per-device dashboard widget visibility configuration."""

    __tablename__ = "device_dashboard_widgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("device_id", "tenant_id", "field_name", name="uq_device_dashboard_widget"),
        Index("ix_device_dashboard_widgets_device_order", "device_id", "tenant_id", "display_order"),
    )

    def __repr__(self) -> str:
        return f"<DeviceDashboardWidget(device_id={self.device_id}, field_name={self.field_name})>"


class DeviceDashboardWidgetSetting(Base):
    """Per-device widget config state to distinguish default/fallback vs explicit empty."""

    __tablename__ = "device_dashboard_widget_settings"

    device_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), primary_key=True, nullable=False)
    is_configured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<DeviceDashboardWidgetSetting(device_id={self.device_id}, "
            f"is_configured={self.is_configured})>"
        )


class IdleRunningLog(Base):
    """Daily aggregate idle-running consumption log per device."""

    __tablename__ = "idle_running_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idle_energy_kwh: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    idle_cost: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    tariff_rate_used: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    pf_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("device_id", "tenant_id", "period_start", name="uq_idle_log_device_day"),
        Index("idx_idle_log_device_period", "device_id", "tenant_id", "period_start"),
    )

    def __repr__(self) -> str:
        return f"<IdleRunningLog(device_id={self.device_id}, period_start={self.period_start})>"


class DeviceStateInterval(Base):
    """Durable per-device interval log for idle, overconsumption, and runtime_on."""

    __tablename__ = "device_state_intervals"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    state_type: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(AwareTimestamp(), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(AwareTimestamp(), nullable=True)
    duration_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    opened_by_sample_ts: Mapped[Optional[datetime]] = mapped_column(AwareTimestamp(), nullable=True)
    closed_by_sample_ts: Mapped[Optional[datetime]] = mapped_column(AwareTimestamp(), nullable=True)
    opened_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    closed_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "state_type IN ('idle', 'overconsumption', 'runtime_on')",
            name="ck_device_state_intervals_state_type",
        ),
        Index(
            "ix_device_state_intervals_device_state_started",
            "tenant_id",
            "device_id",
            "state_type",
            "started_at",
        ),
        Index(
            "ix_device_state_intervals_device_open",
            "tenant_id",
            "device_id",
            "is_open",
        ),
        Index(
            "ix_device_state_intervals_tenant_started",
            "tenant_id",
            "started_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DeviceStateInterval(device_id={self.device_id}, state_type={self.state_type}, "
            f"started_at={self.started_at}, is_open={self.is_open})>"
        )


class DeviceLiveState(Base):
    """Per-device real-time projection for low-latency dashboard reads."""

    __tablename__ = "device_live_state"

    device_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), primary_key=True, nullable=False)
    last_telemetry_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sample_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    runtime_status: Mapped[str] = mapped_column(String(32), nullable=False, default=RuntimeStatus.STOPPED.value, index=True)
    load_state: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    health_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    uptime_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    today_uptime_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_shift_uptime_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    today_energy_kwh: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    today_idle_kwh: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    today_offhours_kwh: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    today_overconsumption_kwh: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    today_loss_kwh: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    today_loss_cost_inr: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)

    month_energy_kwh: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    month_energy_cost_inr: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)

    today_running_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    today_effective_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    day_bucket: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    month_bucket: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)

    last_energy_kwh: Mapped[Optional[float]] = mapped_column(Numeric(14, 6), nullable=True)
    last_power_kw: Mapped[Optional[float]] = mapped_column(Numeric(14, 6), nullable=True)
    last_current_a: Mapped[Optional[float]] = mapped_column(Numeric(14, 6), nullable=True)
    last_voltage_v: Mapped[Optional[float]] = mapped_column(Numeric(14, 6), nullable=True)
    idle_streak_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    idle_streak_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<DeviceLiveState(device_id={self.device_id}, version={self.version})>"


class DeviceLatestTelemetrySnapshot(Base):
    """Latest numeric telemetry snapshot persisted from the live projection lane."""

    __tablename__ = "device_latest_telemetry_snapshot"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        Index("ix_device_latest_snapshot_sample_ts", "sample_ts"),
        Index("ix_device_latest_snapshot_projection_version", "projection_version"),
        Index("ix_device_latest_snapshot_updated_at", "updated_at"),
    )

    device_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), primary_key=True, nullable=False)
    sample_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    projection_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    snapshot_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    runtime_status: Mapped[str] = mapped_column(String(32), nullable=False, default=RuntimeStatus.STOPPED.value)
    load_state: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    current_band: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_power_kw: Mapped[Optional[float]] = mapped_column(Numeric(14, 6), nullable=True)
    last_current_a: Mapped[Optional[float]] = mapped_column(Numeric(14, 6), nullable=True)
    last_voltage_v: Mapped[Optional[float]] = mapped_column(Numeric(14, 6), nullable=True)
    numeric_fields_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_fields_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalization_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<DeviceLatestTelemetrySnapshot(device_id={self.device_id}, "
            f"sample_ts={self.sample_ts}, snapshot_version={self.snapshot_version})>"
        )


class DeviceRecentTelemetrySample(Base):
    """Bounded recent telemetry samples for fast machine-detail seed reads."""

    __tablename__ = "device_recent_telemetry_samples"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_device_recent_telemetry_device_sample",
            "tenant_id",
            "device_id",
            "sample_ts",
        ),
        Index(
            "ix_device_recent_telemetry_device_projection",
            "tenant_id",
            "device_id",
            "projection_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    sample_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    projection_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    runtime_status: Mapped[str] = mapped_column(String(32), nullable=False, default=RuntimeStatus.STOPPED.value)
    load_state: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    current_band: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    telemetry_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<DeviceRecentTelemetrySample(device_id={self.device_id}, "
            f"sample_ts={self.sample_ts}, projection_version={self.projection_version})>"
        )


class WasteSiteConfig(Base):
    """Factory/site-level defaults for waste analysis windows."""

    __tablename__ = "waste_site_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(TENANT_ID_LENGTH), nullable=True, index=True)
    default_unoccupied_weekday_start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    default_unoccupied_weekday_end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    default_unoccupied_weekend_start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    default_unoccupied_weekend_end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_waste_site_config_tenant"),
    )


class DashboardSnapshot(Base):
    """Persisted dashboard snapshot payload for low-latency reads."""

    __tablename__ = "dashboard_snapshots"

    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), primary_key=True)
    snapshot_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    storage_backend: Mapped[str] = mapped_column(
        SAEnum("mysql", "minio", name="dashboard_snapshot_storage_backend"),
        nullable=False,
        default="mysql",
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_dashboard_snapshots_tenant_id", "tenant_id"),
        Index("ix_dashboard_snapshots_generated_at", "generated_at"),
        Index("ix_dashboard_snapshots_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<DashboardSnapshot(tenant_id={self.tenant_id}, "
            f"key={self.snapshot_key}, generated_at={self.generated_at})>"
        )


class HardwareUnitStatus(str, Enum):
    """Lifecycle status for a physical hardware unit."""

    AVAILABLE = "available"
    RETIRED = "retired"


class HardwareUnit(Base):
    """Physical hardware inventory tracked per tenant and plant."""

    __tablename__ = "hardware_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hardware_unit_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    plant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    unit_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    unit_name: Mapped[str] = mapped_column(String(255), nullable=False)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    serial_number: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=HardwareUnitStatus.AVAILABLE.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    installations: Mapped[list["DeviceHardwareInstallation"]] = relationship(
        "DeviceHardwareInstallation",
        back_populates="hardware_unit",
        cascade="all, delete-orphan",
        lazy="selectin",
        overlaps="device,hardware_installations",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "hardware_unit_id", name="uq_hardware_units_tenant_unit_id"),
        Index("ix_hardware_units_tenant_plant_type", "tenant_id", "plant_id", "unit_type"),
    )


class DeviceHardwareInstallation(Base):
    """Historical hardware installation events for a device."""

    __tablename__ = "device_hardware_installations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    plant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    hardware_unit_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    installation_role: Mapped[str] = mapped_column(String(100), nullable=False)
    commissioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    decommissioned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active_hardware_unit_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    active_device_role_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    hardware_unit: Mapped["HardwareUnit"] = relationship(
        "HardwareUnit",
        back_populates="installations",
        lazy="selectin",
        overlaps="device,hardware_installations",
    )
    device: Mapped["Device"] = relationship(
        "Device",
        lazy="selectin",
        overlaps="hardware_unit,installations,hardware_installations",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "hardware_unit_id"],
            ["hardware_units.tenant_id", "hardware_units.hardware_unit_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "active_hardware_unit_key",
            name="uq_device_hardware_installations_active_unit",
        ),
        UniqueConstraint(
            "tenant_id",
            "active_device_role_key",
            name="uq_device_hardware_installations_active_role",
        ),
        Index(
            "ix_device_hardware_installations_device_history",
            "tenant_id",
            "device_id",
            "commissioned_at",
        ),
        Index(
            "ix_device_hardware_installations_hardware_history",
            "tenant_id",
            "hardware_unit_id",
            "commissioned_at",
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.decommissioned_at is None


class MachineHealthFeatureWindow(Base):
    """Compact hourly telemetry summaries for degradation scoring and anomaly detection."""

    __tablename__ = "machine_health_feature_windows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "device_id", "window_start", name="uq_mhfw_tenant_device_window"),
        CheckConstraint(
            "telemetry_coverage >= 0 AND telemetry_coverage <= 1",
            name="ck_mhfw_coverage_range",
        ),
        CheckConstraint(
            "running_state IN ('OFF','STARTUP','STEADY_RUNNING','LOAD_CHANGE','SHUTDOWN','UNKNOWN')",
            name="ck_mhfw_running_state",
        ),
        Index("ix_mhfw_tenant_device_start", "tenant_id", "device_id", "window_start"),
        Index("ix_mhfw_tenant_start", "tenant_id", "window_start"),
        Index("ix_mhfw_device_start", "device_id", "window_start"),
        Index("ix_mhfw_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    running_state: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    current_avg_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_avg_std: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_avg_p95: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_l1_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_l2_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_l3_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    power_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    power_p95: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    power_factor_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    voltage_avg_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    voltage_imbalance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    phase_imbalance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    frequency_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    energy_kwh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    telemetry_coverage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sample_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    excluded_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<MachineHealthFeatureWindow(device_id={self.device_id}, "
            f"window_start={self.window_start}, running_state={self.running_state})>"
        )


class MachineHealthBaseline(Base):
    """Per-machine learned baseline from steady-running feature windows."""

    __tablename__ = "machine_health_baselines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('active','candidate','retired')",
            name="ck_mhb_status",
        ),
        CheckConstraint(
            "quality_score >= 0 AND quality_score <= 1",
            name="ck_mhb_quality_score_range",
        ),
        Index("ix_mhb_tenant_device_status", "tenant_id", "device_id", "status"),
        Index("ix_mhb_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    baseline_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    current_avg_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_avg_std: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    power_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    power_p95: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    power_factor_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    voltage_avg_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    phase_imbalance_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    frequency_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_band: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    signal_completeness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    steady_running_coverage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    learning_window_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    learned_from_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    learned_from_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<MachineHealthBaseline(device_id={self.device_id}, "
            f"version={self.baseline_version}, status={self.status})>"
        )


class MachineHealthLatest(Base):
    """One-row-per-device precomputed degradation snapshot for dashboard reads."""

    __tablename__ = "machine_health_latest"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "score >= 1 AND score <= 10",
            name="ck_mhl_score_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_mhl_confidence_range",
        ),
        CheckConstraint(
            "status IN ('healthy','watch','warning','critical','learning','insufficient_signals','unavailable')",
            name="ck_mhl_status_values",
        ),
        Index("ix_mhl_tenant_status", "tenant_id", "status"),
    )

    device_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), primary_key=True, nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    baseline_quality: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    top_reasons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contributions_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    signal_completeness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="1")
    updated_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<MachineHealthLatest(device_id={self.device_id}, "
            f"score={self.score}, status={self.status})>"
        )


class MachineHealthHistory(Base):
    """Time-series of past degradation scores for trend charts and trend-worsening detection."""

    __tablename__ = "machine_health_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "device_id", "computed_at", name="uq_mhh_tenant_device_time"),
        CheckConstraint(
            "score >= 1 AND score <= 10",
            name="ck_mhh_score_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_mhh_confidence_range",
        ),
        CheckConstraint(
            "status IN ('healthy','watch','warning','critical','learning','insufficient_signals','unavailable')",
            name="ck_mhh_status_values",
        ),
        Index("ix_mhh_tenant_device_time", "tenant_id", "device_id", "computed_at"),
        Index("ix_mhh_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contributions_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<MachineHealthHistory(device_id={self.device_id}, "
            f"computed_at={self.computed_at}, score={self.score})>"
        )


class MachineAnomalyBaseline(Base):
    """Per-signal statistical baseline for z-score anomaly detection."""

    __tablename__ = "machine_anomaly_baselines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "device_id", "field_name", "time_window", "baseline_version", name="uq_mab_tenant_device_field_window_version"),
        CheckConstraint(
            "status IN ('active','candidate','retired')",
            name="ck_mab_status",
        ),
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
            name="ck_mab_quality_range",
        ),
        CheckConstraint(
            "baseline_std IS NULL OR baseline_std >= 0",
            name="ck_mab_std_non_negative",
        ),
        CheckConstraint(
            "baseline_mad IS NULL OR baseline_mad >= 0",
            name="ck_mab_mad_non_negative",
        ),
        CheckConstraint(
            "time_window IN ('5min','1min')",
            name="ck_mab_time_window",
        ),
        Index("ix_mab_tenant_device_status", "tenant_id", "device_id", "status"),
        Index("ix_mab_tenant_device_field", "tenant_id", "device_id", "field_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    field_name: Mapped[str] = mapped_column(String(32), nullable=False)
    time_window: Mapped[str] = mapped_column(String(16), nullable=False, default="5min")
    baseline_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_std: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_median: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_mad: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_p05: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_p95: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reading_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    learned_from_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    learned_to_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    baseline_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<MachineAnomalyBaseline(device_id={self.device_id}, "
            f"field_name={self.field_name}, status={self.status})>"
        )


class MachineAnomalyEvent(Base):
    """Confirmed anomaly event with signal-level detail and baseline snapshot."""

    __tablename__ = "machine_anomaly_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "severity IN ('mild','strong','severe')",
            name="ck_mae_severity",
        ),
        CheckConstraint(
            "anomaly_type IN ('deviation','persistent','trend')",
            name="ck_mae_anomaly_type",
        ),
        CheckConstraint(
            "time_window IN ('5min','1min')",
            name="ck_mae_time_window",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_mae_confidence_range",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_mae_duration_non_negative",
        ),
        CheckConstraint(
            "baseline_std IS NULL OR baseline_std >= 0",
            name="ck_mae_std_non_negative",
        ),
        Index("ix_mae_tenant_device_occurred", "tenant_id", "device_id", "occurred_at"),
        Index("ix_mae_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_mae_tenant_device_severity", "tenant_id", "device_id", "severity"),
        Index("ix_mae_device_occurred", "device_id", "occurred_at"),
        Index("ix_mae_tenant_device_signal_occurred", "tenant_id", "device_id", "signal_field", "occurred_at"),
        Index("ix_mae_tenant_device_signal_severity_ended", "tenant_id", "device_id", "signal_field", "severity", "ended_at"),
        Index("ix_mae_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    signal_field: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_std: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    z_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    anomaly_type: Mapped[str] = mapped_column(String(32), nullable=False, default="deviation")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="mild")
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    supply_related: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    startup_adjacent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mode_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    time_window: Mapped[str] = mapped_column(String(16), nullable=False, default="5min")
    correlated_signals_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    baseline_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<MachineAnomalyEvent(device_id={self.device_id}, "
            f"occurred_at={self.occurred_at}, severity={self.severity})>"
        )


class TenantEmissionFactor(Base):
    """Tenant-scoped emission factor for Scope 2 CO2 calculations."""

    __tablename__ = "tenant_emission_factors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(24), nullable=False)
    country: Mapped[str] = mapped_column(String(8), nullable=False, default="IN")
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="all_india_grid")
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="location_based")
    factor_value: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    factor_unit: Mapped[str] = mapped_column(String(32), nullable=False, default="kg_co2_per_kwh")
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    factor_year: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "country", "region", "method", "is_active",
            name="uq_tenant_emission_factor_active",
        ),
        Index("ix_tenant_emission_factors_tenant_id", "tenant_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<TenantEmissionFactor(tenant_id={self.tenant_id}, "
            f"factor_value={self.factor_value}, method={self.method})>"
        )


class MachineAnomalyDailyCount(Base):
    """Pre-aggregated per-device daily anomaly counts for dashboard reads."""

    __tablename__ = "machine_anomaly_daily_counts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "device_id", "date", name="uq_madc_tenant_device_date"),
        CheckConstraint(
            "total_count >= 0 AND mild_count >= 0 AND strong_count >= 0 AND severe_count >= 0 AND supply_related_count >= 0",
            name="ck_madc_counts_non_negative",
        ),
        CheckConstraint(
            "total_count >= mild_count + strong_count + severe_count",
            name="ck_madc_total_consistency",
        ),
        CheckConstraint(
            "avg_confidence IS NULL OR (avg_confidence >= 0 AND avg_confidence <= 1)",
            name="ck_madc_confidence_range",
        ),
        Index("ix_madc_tenant_device_date", "tenant_id", "device_id", "date"),
        Index("ix_madc_tenant_date", "tenant_id", "date"),
        Index("ix_madc_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mild_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    severe_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supply_related_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top_signal: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    avg_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    signal_breakdown_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<MachineAnomalyDailyCount(device_id={self.device_id}, "
            f"date={self.date}, total_count={self.total_count})>"
        )


class MachineAnomalyWeeklyCount(Base):
    """Pre-aggregated per-device weekly anomaly counts with week-over-week delta."""

    __tablename__ = "machine_anomaly_weekly_counts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "device_id", "week_start_date", name="uq_mawc_tenant_device_week"),
        CheckConstraint(
            "total_count >= 0 AND mild_count >= 0 AND strong_count >= 0 AND severe_count >= 0 AND supply_related_count >= 0",
            name="ck_mawc_counts_non_negative",
        ),
        CheckConstraint(
            "total_count >= mild_count + strong_count + severe_count",
            name="ck_mawc_total_consistency",
        ),
        CheckConstraint(
            "avg_confidence IS NULL OR (avg_confidence >= 0 AND avg_confidence <= 1)",
            name="ck_mawc_confidence_range",
        ),
        Index("ix_mawc_tenant_device_week", "tenant_id", "device_id", "week_start_date"),
        Index("ix_mawc_tenant_week", "tenant_id", "week_start_date"),
        Index("ix_mawc_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False)
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mild_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    severe_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supply_related_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top_signal: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    avg_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    signal_breakdown_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    week_over_week_change: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareTimestamp(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship("Device", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<MachineAnomalyWeeklyCount(device_id={self.device_id}, "
            f"week_start_date={self.week_start_date}, total_count={self.total_count})>"
        )
