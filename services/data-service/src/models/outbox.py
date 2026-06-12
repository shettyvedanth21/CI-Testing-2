"""SQLAlchemy models for telemetry outbox and reconciliation logs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import BIGINT, JSON, DateTime, Enum as SAEnum, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarative model for local data-service tables."""


class OutboxTarget(str, Enum):
    """Supported outbox delivery targets."""

    DEVICE_SERVICE = "device-service"
    ENERGY_SERVICE = "energy-service"


class OutboxStatus(str, Enum):
    """Outbox row lifecycle states."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"


class OutboxMessage(Base):
    """Durable telemetry outbox row."""

    __tablename__ = "telemetry_outbox"
    __table_args__ = (
        Index("ix_telemetry_outbox_status_created_at", "status", "created_at"),
        Index("ix_telemetry_outbox_device_id_status", "device_id", "status"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    telemetry_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    target: Mapped[OutboxTarget] = mapped_column(
        SAEnum(
            OutboxTarget,
            name="telemetry_outbox_target",
            native_enum=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    status: Mapped[OutboxStatus] = mapped_column(
        SAEnum(
            OutboxStatus,
            name="telemetry_outbox_status",
            native_enum=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=OutboxStatus.PENDING,
        server_default=OutboxStatus.PENDING.value,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReconciliationLog(Base):
    """Audit log for reconciliation checks."""

    __tablename__ = "reconciliation_log"
    __table_args__ = (
        Index("ix_reconciliation_log_device_checked_at", "device_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    influx_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    mysql_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    drift_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_taken: Mapped[str] = mapped_column(String(255), nullable=False, server_default="none")
