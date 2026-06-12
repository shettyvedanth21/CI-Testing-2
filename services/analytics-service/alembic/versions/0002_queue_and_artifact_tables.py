"""add queue metadata fields and model artifact registry

Revision ID: 0002_queue_artifacts
Revises: 0001_initial_schema
Create Date: 2026-03-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


revision = "0002_queue_artifacts"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analytics_jobs", sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("analytics_jobs", sa.Column("queue_position", sa.Integer(), nullable=True))
    op.add_column("analytics_jobs", sa.Column("queue_enqueued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analytics_jobs", sa.Column("queue_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analytics_jobs", sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analytics_jobs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analytics_jobs", sa.Column("error_code", sa.String(length=100), nullable=True))
    op.create_index("idx_analytics_jobs_attempt", "analytics_jobs", ["attempt"])

    op.create_table(
        "ml_model_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("analysis_type", sa.String(length=50), nullable=False),
        sa.Column("model_key", sa.String(length=100), nullable=False),
        sa.Column("feature_schema_hash", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False, server_default="v1"),
        sa.Column("artifact_payload", mysql.LONGBLOB(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ml_artifacts_lookup", "ml_model_artifacts", ["device_id", "analysis_type", "model_key"])


def downgrade() -> None:
    op.drop_index("idx_ml_artifacts_lookup", table_name="ml_model_artifacts")
    op.drop_table("ml_model_artifacts")

    op.drop_index("idx_analytics_jobs_attempt", table_name="analytics_jobs")
    op.drop_column("analytics_jobs", "error_code")
    op.drop_column("analytics_jobs", "last_heartbeat_at")
    op.drop_column("analytics_jobs", "worker_lease_expires_at")
    op.drop_column("analytics_jobs", "queue_started_at")
    op.drop_column("analytics_jobs", "queue_enqueued_at")
    op.drop_column("analytics_jobs", "queue_position")
    op.drop_column("analytics_jobs", "attempt")
