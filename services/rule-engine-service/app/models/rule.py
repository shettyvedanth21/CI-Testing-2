"""SQLAlchemy models for Rule Engine Service."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
import uuid

from sqlalchemy import Boolean, String, DateTime, Float, Integer, Text, ForeignKey, JSON, Index, CheckConstraint, UniqueConstraint
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

TENANT_ID_LENGTH = 10


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


class NotificationDeliveryStatus(str, Enum):
    """Explicit notification delivery lifecycle for billing-safe auditing."""

    QUEUED = "queued"
    ATTEMPTED = "attempted"
    PROVIDER_ACCEPTED = "provider_accepted"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


class NotificationChannelSetting(Base):
    """Tenant-scoped notification channel settings mirrored from reporting-service."""

    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel_type", "value", name="uq_notification_channels_tenant_type_value"),
        Index("ix_notification_channels_tenant_channel_active", "tenant_id", "channel_type", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class RuleTriggerState(Base):
    """Per-device trigger state so cooldown applies independently per device."""

    __tablename__ = "rule_trigger_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "rule_id", "device_id", name="uq_rule_trigger_states_tenant_rule_device"),
        Index("ix_rule_trigger_states_tenant_rule", "tenant_id", "rule_id"),
        Index("ix_rule_trigger_states_tenant_device", "tenant_id", "device_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("rules.rule_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_once: Mapped[bool] = mapped_column(default=False, nullable=False)
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


class Rule(Base):
    """Rule model for real-time telemetry evaluation."""

    __tablename__ = "rules"

    rule_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    tenant_id: Mapped[Optional[str]] = mapped_column(String(TENANT_ID_LENGTH), nullable=True, index=True)

    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scope: Mapped[RuleScope] = mapped_column(
        String(50),
        default=RuleScope.SELECTED_DEVICES,
        nullable=False,
    )

    property: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    condition: Mapped[Optional[ConditionOperator]] = mapped_column(String(20), nullable=True)
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    rule_type: Mapped[RuleType] = mapped_column(
        String(20),
        default=RuleType.THRESHOLD,
        nullable=False,
        index=True,
    )
    cooldown_mode: Mapped[CooldownMode] = mapped_column(
        String(20),
        default=CooldownMode.INTERVAL,
        nullable=False,
    )
    cooldown_unit: Mapped[CooldownUnit] = mapped_column(
        String(20),
        default=CooldownUnit.MINUTES,
        nullable=False,
    )
    time_window_start: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    time_window_end: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    time_condition: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    triggered_once: Mapped[bool] = mapped_column(default=False, nullable=False)

    status: Mapped[RuleStatus] = mapped_column(
        String(50),
        default=RuleStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    # JSON field storing list of strings
    notification_channels: Mapped[List[str]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
        nullable=False,
    )

    notification_recipients: Mapped[List[Dict[str, Any]]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
        nullable=False,
    )

    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)

    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # JSON field storing list of strings
    device_ids: Mapped[List[str]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Rule(rule_id={self.rule_id}, name={self.rule_name}, status={self.status})>"

    def is_active(self) -> bool:
        return self.status == RuleStatus.ACTIVE and self.deleted_at is None

    def is_in_cooldown(self) -> bool:
        if self.cooldown_mode == CooldownMode.NO_REPEAT:
            return bool(self.triggered_once)

        if self.last_triggered_at is None:
            return False

        from datetime import timedelta, timezone

        cooldown_end = self.last_triggered_at + timedelta(seconds=self.effective_cooldown_seconds())
        now = datetime.now(timezone.utc)
        
        if self.last_triggered_at.tzinfo is None:
            cooldown_end = cooldown_end.replace(tzinfo=timezone.utc)
        
        return now < cooldown_end

    def effective_cooldown_seconds(self) -> int:
        if self.cooldown_mode == CooldownMode.NO_REPEAT:
            return 0

        if self.cooldown_seconds is not None:
            return max(int(self.cooldown_seconds), 0)

        return max(int(self.cooldown_minutes or 0), 0) * 60

    def applies_to_device(self, device_id: str) -> bool:
        if self.scope == RuleScope.ALL_DEVICES:
            if self.device_ids:
                return device_id in self.device_ids
            return True
        return device_id in self.device_ids


class Alert(Base):
    """Alert model for storing rule evaluation results."""

    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    tenant_id: Mapped[Optional[str]] = mapped_column(String(TENANT_ID_LENGTH), nullable=True, index=True)

    rule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("rules.rule_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    device_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actual_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[str] = mapped_column(
        String(50),
        default="open",
        nullable=False,
        index=True,
    )

    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Alert(alert_id={self.alert_id}, rule_id={self.rule_id}, status={self.status})>"


class ActivityEvent(Base):
    """Activity event model for device/rule alert history."""

    __tablename__ = "activity_events"

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    tenant_id: Mapped[Optional[str]] = mapped_column(String(TENANT_ID_LENGTH), nullable=True, index=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    rule_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    alert_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    is_read: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<ActivityEvent(event_id={self.event_id}, type={self.event_type}, device_id={self.device_id})>"


class NotificationDeliveryLog(Base):
    """Permanent audit ledger for notification delivery attempts."""

    __tablename__ = "notification_delivery_logs"
    __table_args__ = (
        Index("ix_notification_delivery_logs_tenant_attempted_at", "tenant_id", "attempted_at"),
        Index("ix_notification_delivery_logs_tenant_channel_attempted_at", "tenant_id", "channel", "attempted_at"),
        Index("ix_notification_delivery_logs_tenant_status_attempted_at", "tenant_id", "status", "attempted_at"),
        Index("ix_notification_delivery_logs_rule_id", "rule_id"),
        Index("ix_notification_delivery_logs_alert_id", "alert_id"),
        Index("ix_notification_delivery_logs_provider_message_id", "provider_message_id"),
        CheckConstraint("tenant_id IS NOT NULL", name="ck_notification_delivery_logs_tenant_required"),
        CheckConstraint(
            "status IN ('queued','attempted','provider_accepted','delivered','failed','skipped')",
            name="ck_notification_delivery_logs_valid_status",
        ),
        CheckConstraint("billable_units >= 0", name="ck_notification_delivery_logs_billable_non_negative"),
        CheckConstraint(
            "CASE "
            "WHEN status IN ('provider_accepted','delivered') AND billable_units = 1 THEN 1 "
            "WHEN status IN ('queued','attempted','failed','skipped') AND billable_units = 0 THEN 1 "
            "ELSE 0 END = 1",
            name="ck_notification_delivery_logs_billable_by_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    tenant_id: Mapped[Optional[str]] = mapped_column(String(TENANT_ID_LENGTH), nullable=True, index=True)
    alert_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("alerts.alert_id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("rules.rule_id", ondelete="SET NULL"),
        nullable=True,
    )
    device_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_masked: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(String(32), nullable=False, index=True)
    billable_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class NotificationOutbox(Base):
    """Durable outbox for asynchronous notification delivery."""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("ledger_log_id", name="uq_notification_outbox_ledger_log_id"),
        UniqueConstraint("alert_id", "channel", "recipient_hash", name="uq_notification_outbox_alert_channel_recipient"),
        Index("ix_notification_outbox_tenant_status_next_attempt", "tenant_id", "status", "next_attempt_at"),
        Index("ix_notification_outbox_tenant_channel_status", "tenant_id", "channel", "status"),
        Index("ix_notification_outbox_processing_started", "status", "processing_started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    alert_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("alerts.alert_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rule_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("rules.rule_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ledger_log_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("notification_delivery_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    device_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recipient_masked: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(String(32), nullable=False, index=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    failure_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
