"""Pydantic schemas for Rule Engine Service API."""

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict, EmailStr, TypeAdapter, field_validator, model_validator

from app.utils.recipients import normalize_phone_recipient
from app.utils.threshold_properties import normalize_threshold_property_name


class RuleStatus(str, Enum):
    """Rule status enumeration."""
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class RuleScope(str, Enum):
    """Rule scope enumeration."""
    ALL_DEVICES = "all_devices"
    SELECTED_DEVICES = "selected_devices"


class ConditionOperator(str, Enum):
    """Condition operator enumeration."""
    GREATER_THAN = ">"
    LESS_THAN = "<"
    EQUAL = "="
    NOT_EQUAL = "!="
    GREATER_THAN_OR_EQUAL = ">="
    LESS_THAN_OR_EQUAL = "<="


class RuleType(str, Enum):
    """Rule type enumeration."""

    THRESHOLD = "threshold"
    TIME_BASED = "time_based"
    CONTINUOUS_IDLE_DURATION = "continuous_idle_duration"


class CooldownMode(str, Enum):
    """Cooldown behavior mode."""

    INTERVAL = "interval"
    NO_REPEAT = "no_repeat"


class CooldownUnit(str, Enum):
    """Cooldown unit for interval-based rules."""

    MINUTES = "minutes"
    SECONDS = "seconds"


class TimeCondition(str, Enum):
    """Time-based trigger condition."""

    RUNNING_IN_WINDOW = "running_in_window"


class NotificationChannel(str, Enum):
    """Notification channel enumeration."""
    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"


_EMAIL_VALIDATOR = TypeAdapter(EmailStr)


class NotificationRecipientTarget(BaseModel):
    """Structured notification recipient target stored on each rule."""

    channel: NotificationChannel = Field(..., description="Notification channel for this recipient target")
    value: str = Field(..., min_length=1, max_length=255, description="Recipient address/value for the channel")

    @model_validator(mode="after")
    def validate_target(self):
        normalized = self.value.strip()
        if self.channel == NotificationChannel.EMAIL:
            normalized = str(_EMAIL_VALIDATOR.validate_python(normalized)).lower()
        elif self.channel in {NotificationChannel.SMS, NotificationChannel.WHATSAPP}:
            normalized = normalize_phone_recipient(normalized)
        else:
            raise ValueError(f"Recipient targets for channel '{self.channel.value}' are not supported yet")
        self.value = normalized
        return self


class RuleBase(BaseModel):
    """Base schema with common rule fields."""
    
    rule_type: RuleType = Field(
        default=RuleType.THRESHOLD,
        description="Rule type"
    )
    rule_name: str = Field(
        ..., 
        min_length=1, 
        max_length=255, 
        description="Human-readable rule name"
    )
    description: Optional[str] = Field(
        None, 
        max_length=1000, 
        description="Rule description"
    )
    scope: RuleScope = Field(
        default=RuleScope.SELECTED_DEVICES,
        description="Rule scope - all devices or selected devices"
    )
    property: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Property to monitor (e.g., temperature, voltage, power)"
    )
    condition: Optional[ConditionOperator] = Field(
        default=None,
        description="Condition operator (>, <, =, !=, >=, <=)"
    )
    threshold: Optional[float] = Field(
        default=None,
        description="Threshold value for condition"
    )
    time_window_start: Optional[str] = Field(
        default=None,
        pattern=r"^\d{2}:\d{2}$",
        description="Start time (HH:MM)"
    )
    time_window_end: Optional[str] = Field(
        default=None,
        pattern=r"^\d{2}:\d{2}$",
        description="End time (HH:MM)"
    )
    timezone: str = Field(
        default="Asia/Kolkata",
        max_length=64,
        description="Timezone name for time-window evaluation"
    )
    time_condition: Optional[TimeCondition] = Field(
        default=None,
        description="Time-based trigger condition"
    )
    duration_minutes: Optional[int] = Field(
        default=None,
        ge=1,
        le=7 * 24 * 60,
        description="Continuous idle duration threshold in minutes"
    )
    notification_channels: List[NotificationChannel] = Field(
        default_factory=list,
        min_length=1,
        description="List of notification channels"
    )
    notification_recipients: List[NotificationRecipientTarget] = Field(
        default_factory=list,
        description="Structured recipient targets attached directly to this rule"
    )
    cooldown_minutes: Optional[int] = Field(
        default=None,
        ge=0,
        le=7 * 24 * 60,
        description="Legacy cooldown period in minutes between notifications"
    )
    cooldown_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        le=7 * 24 * 60 * 60,
        description="Canonical cooldown period in seconds between notifications"
    )
    cooldown_unit: Optional[CooldownUnit] = Field(
        default=None,
        description="Cooldown unit for interval-based rules"
    )
    cooldown_mode: CooldownMode = Field(
        default=CooldownMode.INTERVAL,
        description="Cooldown behavior mode"
    )

    @field_validator("property")
    @classmethod
    def normalize_threshold_property(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_threshold_property_name(value)

    @model_validator(mode="after")
    def validate_rule_by_type(self):
        if self.scope == RuleScope.SELECTED_DEVICES and getattr(self, "device_ids", None) == []:
            # device_ids is validated in RuleCreate with richer context; keep lightweight here.
            pass

        if self.rule_type == RuleType.THRESHOLD:
            if self.property is None or self.condition is None or self.threshold is None:
                raise ValueError("property, condition, and threshold are required for threshold rules")
        elif self.rule_type == RuleType.TIME_BASED:
            if not self.time_window_start or not self.time_window_end:
                raise ValueError("time_window_start and time_window_end are required for time-based rules")
            if self.time_condition is None:
                self.time_condition = TimeCondition.RUNNING_IN_WINDOW
            self.duration_minutes = None
        elif self.rule_type == RuleType.CONTINUOUS_IDLE_DURATION:
            if self.duration_minutes is None:
                raise ValueError("duration_minutes is required for continuous idle duration rules")
            self.property = None
            self.condition = None
            self.threshold = None
            self.time_window_start = None
            self.time_window_end = None
            self.time_condition = None

        return self


class RuleCreate(RuleBase):
    """Schema for creating a new rule."""
    
    tenant_id: Optional[str] = Field(
        None, 
        max_length=50, 
        description="Tenant ID for multi-tenancy"
    )
    device_ids: List[str] = Field(
        default_factory=list,
        description="List of device IDs for selected_devices scope"
    )

    @model_validator(mode="after")
    def validate_scope_devices(self):
        if self.scope == RuleScope.SELECTED_DEVICES and not self.device_ids:
            raise ValueError("device_ids is required when scope is 'selected_devices'")
        return self


class RuleUpdate(BaseModel):
    """Schema for updating an existing rule."""
    
    rule_type: Optional[RuleType] = None
    rule_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    scope: Optional[RuleScope] = None
    device_ids: Optional[List[str]] = None
    property: Optional[str] = Field(None, min_length=1, max_length=100)
    condition: Optional[ConditionOperator] = None
    threshold: Optional[float] = None
    time_window_start: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    time_window_end: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    timezone: Optional[str] = Field(default=None, max_length=64)
    time_condition: Optional[TimeCondition] = None
    duration_minutes: Optional[int] = Field(default=None, ge=1, le=7 * 24 * 60)
    notification_channels: Optional[List[NotificationChannel]] = None
    notification_recipients: Optional[List[NotificationRecipientTarget]] = None
    cooldown_minutes: Optional[int] = Field(None, ge=0, le=7 * 24 * 60)
    cooldown_seconds: Optional[int] = Field(None, ge=0, le=7 * 24 * 60 * 60)
    cooldown_unit: Optional[CooldownUnit] = None
    cooldown_mode: Optional[CooldownMode] = None

    @field_validator("property")
    @classmethod
    def normalize_threshold_property(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_threshold_property_name(value)


class RuleStatusUpdate(BaseModel):
    """Schema for updating rule status (pause/resume)."""
    
    status: RuleStatus = Field(
        ...,
        description="New rule status (active, paused, archived)"
    )


class RuleResponse(RuleBase):
    """Schema for rule response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    rule_id: UUID
    tenant_id: Optional[str] = None
    device_ids: List[str]
    status: RuleStatus
    triggered_once: bool = False
    last_triggered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


class RuleListResponse(BaseModel):
    """Schema for paginated rule list response."""
    
    success: bool = True
    data: List[RuleResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class RuleSingleResponse(BaseModel):
    """Schema for single rule response."""
    
    success: bool = True
    data: RuleResponse


class RuleStatusResponse(BaseModel):
    """Schema for rule status update response."""
    
    success: bool = True
    message: str
    rule_id: UUID
    status: RuleStatus


class RuleDeleteResponse(BaseModel):
    """Schema for rule deletion response."""
    
    success: bool = True
    message: str
    rule_id: UUID


class TelemetryPayload(BaseModel):
    """Schema for telemetry payload from Data Service - supports dynamic fields."""
    
    model_config = ConfigDict(extra="allow")
    
    device_id: str = Field(..., description="Device identifier")
    timestamp: datetime = Field(..., description="Telemetry timestamp")
    schema_version: Optional[str] = Field(None, description="Schema version")
    enrichment_status: Optional[str] = Field(None, description="Enrichment status")
    device_type: Optional[str] = Field(None, description="Device type")
    device_location: Optional[str] = Field(None, description="Device location")
    projected_load_state: Optional[str] = Field(None, description="Load state persisted by device live projection")
    idle_streak_duration_sec: Optional[int] = Field(None, description="Persisted continuous idle streak duration in seconds")
    idle_streak_started_at: Optional[datetime] = Field(None, description="Persisted continuous idle streak start time")
    
    def get_dynamic_fields(self) -> Dict[str, float]:
        """Get all dynamic numeric fields from the payload."""
        dynamic_fields = {}
        for key, value in self.model_dump().items():
            if key not in (
                'device_id',
                'timestamp',
                'schema_version',
                'enrichment_status',
                'device_type',
                'device_location',
                'projected_load_state',
                'idle_streak_duration_sec',
                'idle_streak_started_at',
            ) and isinstance(value, (int, float)):
                dynamic_fields[key] = float(value)
        return dynamic_fields
    
    def get_field_value(self, field_name: str) -> Optional[float]:
        """Get a specific field value by name."""
        value = getattr(self, field_name, None)
        if isinstance(value, (int, float)):
            return float(value)
        return None


class EvaluationRequest(BaseModel):
    """Schema for rule evaluation request - supports dynamic fields."""
    
    model_config = ConfigDict(extra="allow")
    
    device_id: str = Field(..., description="Device identifier")
    timestamp: datetime = Field(..., description="Telemetry timestamp")
    schema_version: Optional[str] = Field(None, description="Schema version")
    enrichment_status: Optional[str] = Field(None, description="Enrichment status")
    device_type: Optional[str] = Field(None, description="Device type")
    device_location: Optional[str] = Field(None, description="Device location")
    projected_load_state: Optional[str] = Field(None, description="Load state persisted by device live projection")
    idle_streak_duration_sec: Optional[int] = Field(None, description="Persisted continuous idle streak duration in seconds")
    idle_streak_started_at: Optional[datetime] = Field(None, description="Persisted continuous idle streak start time")
    
    def get_dynamic_fields(self) -> Dict[str, float]:
        """Get all dynamic numeric fields from the payload."""
        dynamic_fields = {}
        for key, value in self.model_dump().items():
            if key not in (
                'device_id',
                'timestamp',
                'schema_version',
                'enrichment_status',
                'device_type',
                'device_location',
                'projected_load_state',
                'idle_streak_duration_sec',
                'idle_streak_started_at',
            ) and isinstance(value, (int, float)):
                dynamic_fields[key] = float(value)
        return dynamic_fields


class EvaluationResult(BaseModel):
    """Schema for individual rule evaluation result."""
    
    rule_id: UUID
    rule_name: str
    triggered: bool
    actual_value: float
    threshold: float
    condition: str
    message: Optional[str] = None


class EvaluationResponse(BaseModel):
    """Schema for rule evaluation response."""
    
    success: bool = True
    device_id: str
    evaluated_at: datetime
    rules_evaluated: int
    rules_triggered: int
    triggered_rules: List[EvaluationResult]


class AlertBase(BaseModel):
    """Base schema for alerts."""
    
    severity: str = Field(..., max_length=50)
    message: str = Field(...)
    actual_value: float
    threshold_value: float


class AlertResponse(AlertBase):
    """Schema for alert response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    alert_id: UUID
    rule_id: UUID
    device_id: str
    status: str
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime


class ActivityEventResponse(BaseModel):
    """Schema for activity event response."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    tenant_id: Optional[str] = None
    device_id: Optional[str] = None
    rule_id: Optional[UUID] = None
    alert_id: Optional[UUID] = None
    event_type: str
    title: str
    message: str
    metadata_json: Dict[str, Any]
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime


class ErrorResponse(BaseModel):
    """Schema for error responses."""
    
    success: bool = False
    error: dict
    timestamp: datetime = Field(default_factory=datetime.utcnow)
