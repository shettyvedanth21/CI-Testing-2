"""Database models for SQLAlchemy."""

import uuid

from sqlalchemy import JSON, Column, DateTime, Float, Index, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all models."""


class AnalyticsJob(Base):
    """Analytics job model."""

    __tablename__ = "analytics_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    job_id = Column(String(100), unique=True, nullable=False, index=True)
    tenant_id = Column(String(10), nullable=True, index=True)
    device_id = Column(String(50), nullable=False, index=True)
    job_kind = Column(String(32), nullable=False, default="single", server_default="single", index=True)
    parent_job_id = Column(String(100), nullable=True, index=True)
    analysis_type = Column(String(50), nullable=False)
    model_name = Column(String(100), nullable=False)

    date_range_start = Column(DateTime(timezone=True), nullable=False)
    date_range_end = Column(DateTime(timezone=True), nullable=False)

    parameters = Column(JSON, nullable=True)

    status = Column(String(50), nullable=False, default="pending")
    progress = Column(Float, nullable=True)
    phase = Column(String(50), nullable=True)
    phase_label = Column(String(255), nullable=True)
    phase_progress = Column(Float, nullable=True)
    message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    results = Column(JSON, nullable=True)
    accuracy_metrics = Column(JSON, nullable=True)
    execution_time_seconds = Column(Integer, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    attempt = Column(Integer, nullable=False, default=0, server_default="0")
    queue_position = Column(Integer, nullable=True)
    queue_enqueued_at = Column(DateTime(timezone=True), nullable=True)
    queue_dispatched_at = Column(DateTime(timezone=True), nullable=True)
    queue_started_at = Column(DateTime(timezone=True), nullable=True)
    worker_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(100), nullable=True)

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("idx_analytics_jobs_status", "status"),
        Index("idx_analytics_jobs_tenant_status_created", "tenant_id", "status", "created_at"),
        Index(
            "idx_analytics_jobs_dedup_lookup",
            "tenant_id",
            "device_id",
            "analysis_type",
            "model_name",
            "status",
            "date_range_start",
        ),
        Index("idx_analytics_jobs_parent_job_id", "parent_job_id"),
        Index("idx_analytics_jobs_dispatch_lookup", "status", "job_kind", "queue_dispatched_at", "created_at"),
        Index("idx_analytics_jobs_created_at", "created_at"),
        Index("idx_analytics_jobs_attempt", "attempt"),
    )


class ModelArtifact(Base):
    """Stored model artifact metadata/payload for warm reuse."""

    __tablename__ = "ml_model_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(10), nullable=True, index=True)
    device_id = Column(String(50), nullable=False, index=True)
    analysis_type = Column(String(50), nullable=False, index=True)
    model_key = Column(String(100), nullable=False, index=True)
    feature_schema_hash = Column(String(128), nullable=False)
    model_version = Column(String(64), nullable=False, default="v1", server_default="v1")
    artifact_payload = Column(LONGBLOB, nullable=False)
    metrics = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_ml_artifacts_lookup", "tenant_id", "device_id", "analysis_type", "model_key"),
    )


class WorkerHeartbeat(Base):
    """Worker liveness heartbeat used for strict active worker counting."""

    __tablename__ = "analytics_worker_heartbeats"

    worker_id = Column(String(128), primary_key=True)
    app_role = Column(String(32), nullable=False, default="worker", server_default="worker")
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    status = Column(String(32), nullable=False, default="alive", server_default="alive")


class FailureEventLabel(Base):
    """Ground-truth maintenance/failure labels for backtesting."""

    __tablename__ = "failure_event_labels"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(50), nullable=False, index=True)
    event_time = Column(DateTime(timezone=True), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, default="failure", server_default="failure")
    severity = Column(String(32), nullable=True)
    source = Column(String(100), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_failure_event_labels_device_time", "device_id", "event_time"),
    )


class AccuracyEvaluation(Base):
    """Stored evaluation summary for certification gating."""

    __tablename__ = "analytics_accuracy_evaluations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_type = Column(String(50), nullable=False, index=True)
    scope_device_id = Column(String(50), nullable=True, index=True)
    sample_size = Column(Integer, nullable=False, default=0, server_default="0")
    labeled_events = Column(Integer, nullable=False, default=0, server_default="0")
    precision = Column(Float, nullable=True)
    recall = Column(Float, nullable=True)
    f1_score = Column(Float, nullable=True)
    false_alert_rate = Column(Float, nullable=True)
    avg_lead_hours = Column(Float, nullable=True)
    is_certified = Column(Integer, nullable=False, default=0, server_default="0")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_accuracy_eval_type_scope_created", "analysis_type", "scope_device_id", "created_at"),
    )
