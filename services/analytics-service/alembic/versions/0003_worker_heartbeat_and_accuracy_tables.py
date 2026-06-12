"""add worker heartbeat and accuracy certification tables

Revision ID: 0003_worker_accuracy
Revises: 0002_queue_artifacts
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_worker_accuracy"
down_revision = "0002_queue_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_worker_heartbeats",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("app_role", sa.String(length=32), nullable=False, server_default="worker"),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="alive"),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "idx_analytics_worker_heartbeats_last_heartbeat_at",
        "analytics_worker_heartbeats",
        ["last_heartbeat_at"],
    )

    op.create_table(
        "failure_event_labels",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False, server_default="failure"),
        sa.Column("severity", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_failure_event_labels_device_id", "failure_event_labels", ["device_id"])
    op.create_index("idx_failure_event_labels_event_time", "failure_event_labels", ["event_time"])
    op.create_index(
        "idx_failure_event_labels_device_time",
        "failure_event_labels",
        ["device_id", "event_time"],
    )

    op.create_table(
        "analytics_accuracy_evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("analysis_type", sa.String(length=50), nullable=False),
        sa.Column("scope_device_id", sa.String(length=50), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("labeled_events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("precision", sa.Float(), nullable=True),
        sa.Column("recall", sa.Float(), nullable=True),
        sa.Column("f1_score", sa.Float(), nullable=True),
        sa.Column("false_alert_rate", sa.Float(), nullable=True),
        sa.Column("avg_lead_hours", sa.Float(), nullable=True),
        sa.Column("is_certified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_analytics_accuracy_evaluations_analysis_type",
        "analytics_accuracy_evaluations",
        ["analysis_type"],
    )
    op.create_index(
        "idx_analytics_accuracy_evaluations_scope_device_id",
        "analytics_accuracy_evaluations",
        ["scope_device_id"],
    )
    op.create_index(
        "idx_analytics_accuracy_evaluations_created_at",
        "analytics_accuracy_evaluations",
        ["created_at"],
    )
    op.create_index(
        "idx_accuracy_eval_type_scope_created",
        "analytics_accuracy_evaluations",
        ["analysis_type", "scope_device_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_accuracy_eval_type_scope_created", table_name="analytics_accuracy_evaluations")
    op.drop_index("idx_analytics_accuracy_evaluations_created_at", table_name="analytics_accuracy_evaluations")
    op.drop_index("idx_analytics_accuracy_evaluations_scope_device_id", table_name="analytics_accuracy_evaluations")
    op.drop_index("idx_analytics_accuracy_evaluations_analysis_type", table_name="analytics_accuracy_evaluations")
    op.drop_table("analytics_accuracy_evaluations")

    op.drop_index("idx_failure_event_labels_device_time", table_name="failure_event_labels")
    op.drop_index("idx_failure_event_labels_event_time", table_name="failure_event_labels")
    op.drop_index("idx_failure_event_labels_device_id", table_name="failure_event_labels")
    op.drop_table("failure_event_labels")

    op.drop_index("idx_analytics_worker_heartbeats_last_heartbeat_at", table_name="analytics_worker_heartbeats")
    op.drop_table("analytics_worker_heartbeats")
