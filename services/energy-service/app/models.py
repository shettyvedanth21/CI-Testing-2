from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EnergyReconcileRun(Base):
    __tablename__ = "energy_reconcile_run"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    requested_start: Mapped[date] = mapped_column(Date, nullable=False)
    requested_end: Mapped[date] = mapped_column(Date, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scope_filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suspicious_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnergyDeviceState(Base):
    __tablename__ = "energy_device_state"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_state: Mapped[str] = mapped_column(String(32), nullable=False, default="running")

    last_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_energy_counter: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)

    last_day_bucket: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_month_bucket: Mapped[date | None] = mapped_column(Date, nullable=True)

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EnergyDeviceDay(Base):
    __tablename__ = "energy_device_day"
    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", "day", name="uq_energy_device_day"),
        Index("ix_energy_device_day_day", "day"),
        Index("ix_energy_device_day_tenant_day", "tenant_id", "day"),
        Index("ix_energy_device_day_version", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(10), nullable=False)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)

    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    offhours_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overconsumption_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    quality_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EnergyDeviceMonth(Base):
    __tablename__ = "energy_device_month"
    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", "month", name="uq_energy_device_month"),
        Index("ix_energy_device_month_month", "month"),
        Index("ix_energy_device_month_tenant_month", "tenant_id", "month"),
        Index("ix_energy_device_month_version", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(10), nullable=False)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    month: Mapped[date] = mapped_column(Date, nullable=False)

    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    offhours_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overconsumption_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    quality_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EnergyFleetDay(Base):
    __tablename__ = "energy_fleet_day"
    __table_args__ = (
        Index("ix_energy_fleet_day_day", "day"),
    )

    tenant_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    offhours_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overconsumption_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EnergyFleetMonth(Base):
    __tablename__ = "energy_fleet_month"
    __table_args__ = (
        Index("ix_energy_fleet_month_month", "month"),
    )

    tenant_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    offhours_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overconsumption_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EnergyReconcileAudit(Base):
    __tablename__ = "energy_reconcile_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_energy_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    projected_energy_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    drift_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    repaired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    old_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    old_quality_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_quality_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalization_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="detected", index=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
