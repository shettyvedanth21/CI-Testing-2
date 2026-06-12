from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Boolean, Column, Integer, String, Text, DateTime, JSON, Enum, Index, Float
from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class ReportType(PyEnum):
    consumption = "consumption"
    comparison = "comparison"


class ReportStatus(PyEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    enqueue_failed = "enqueue_failed"


class ComputationMode(PyEnum):
    direct_power = "direct_power"
    derived_single = "derived_single"
    derived_three = "derived_three"


TENANT_ID_LENGTH = 10


class EnergyReport(Base):
    __tablename__ = "energy_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(String(36), unique=True, nullable=False)
    tenant_id = Column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    report_type = Column(Enum(ReportType), nullable=False)
    status = Column(Enum(ReportStatus), default=ReportStatus.pending, nullable=False)
    params = Column(JSON, nullable=False)
    computation_mode = Column(Enum(ComputationMode), nullable=True)
    phase_type_used = Column(String(20), nullable=True)
    root_report_id = Column(String(36), nullable=True)
    revision_number = Column(Integer, nullable=False, default=1)
    supersedes_report_id = Column(String(36), nullable=True)
    superseded_by_report_id = Column(String(36), nullable=True)
    is_authoritative = Column(Boolean, default=True, nullable=False)
    revision_reason = Column(Text, nullable=True)
    generated_from_reconciliation_run_id = Column(String(64), nullable=True)
    tariff_version_id = Column(Integer, nullable=True)
    result_json = Column(JSON, nullable=True)
    s3_key = Column(String(500), nullable=True)
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    progress = Column(Integer, default=0, nullable=False)
    phase = Column(String(50), nullable=True)
    phase_label = Column(String(255), nullable=True)
    phase_progress = Column(Float, nullable=True)
    enqueued_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    worker_id = Column(String(128), nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    timeout_count = Column(Integer, default=0, nullable=False)
    last_attempt_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_energy_reports_tenant_status", "tenant_id", "status"),
        Index("ix_energy_reports_tenant_created", "tenant_id", "created_at"),
        Index("ix_energy_reports_tenant_type_created", "tenant_id", "report_type", "created_at"),
        Index("ix_energy_reports_status_processing_started", "status", "processing_started_at"),
        Index("ix_energy_reports_root_report_id", "root_report_id"),
        Index("ix_energy_reports_authoritative", "tenant_id", "root_report_id", "is_authoritative"),
    )


class ReportWorkerHeartbeat(Base):
    __tablename__ = "report_worker_heartbeats"

    worker_id = Column(String(128), primary_key=True)
    app_role = Column(String(32), nullable=False, default="worker")
    last_heartbeat_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    status = Column(String(32), nullable=False, default="alive")
