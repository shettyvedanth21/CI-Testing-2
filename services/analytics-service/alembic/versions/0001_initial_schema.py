"""Create initial analytics-service schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=100), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("analysis_type", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("date_range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("accuracy_metrics", sa.JSON(), nullable=True),
        sa.Column("execution_time_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            server_onupdate=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_analytics_jobs_job_id"),
    )
    op.create_index("ix_analytics_jobs_job_id", "analytics_jobs", ["job_id"])
    op.create_index("ix_analytics_jobs_device_id", "analytics_jobs", ["device_id"])
    op.create_index("idx_analytics_jobs_status", "analytics_jobs", ["status"])
    op.create_index("idx_analytics_jobs_created_at", "analytics_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_analytics_jobs_created_at", table_name="analytics_jobs")
    op.drop_index("idx_analytics_jobs_status", table_name="analytics_jobs")
    op.drop_index("ix_analytics_jobs_device_id", table_name="analytics_jobs")
    op.drop_index("ix_analytics_jobs_job_id", table_name="analytics_jobs")
    op.drop_table("analytics_jobs")
