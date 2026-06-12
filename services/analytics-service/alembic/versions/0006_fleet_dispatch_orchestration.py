"""add explicit fleet orchestration metadata

Revision ID: 0006_fleet_dispatch_orchestration
Revises: 0005_job_phase_tracking
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_fleet_dispatch_orchestration"
down_revision = "0005_job_phase_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analytics_jobs",
        sa.Column("job_kind", sa.String(length=32), nullable=False, server_default="single"),
    )
    op.add_column("analytics_jobs", sa.Column("parent_job_id", sa.String(length=100), nullable=True))
    op.add_column("analytics_jobs", sa.Column("queue_dispatched_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_analytics_jobs_job_kind", "analytics_jobs", ["job_kind"])
    op.create_index("idx_analytics_jobs_parent_job_id", "analytics_jobs", ["parent_job_id"])
    op.create_index(
        "idx_analytics_jobs_dispatch_lookup",
        "analytics_jobs",
        ["status", "job_kind", "queue_dispatched_at", "created_at"],
    )
    op.execute(
        """
        UPDATE analytics_jobs
        SET job_kind =
            CASE
                WHEN device_id = 'ALL' THEN 'fleet_parent'
                WHEN JSON_UNQUOTE(JSON_EXTRACT(parameters, '$.parent_job_id')) IS NOT NULL THEN 'fleet_child'
                ELSE 'single'
            END,
            parent_job_id = JSON_UNQUOTE(JSON_EXTRACT(parameters, '$.parent_job_id')),
            queue_dispatched_at = COALESCE(queue_dispatched_at, queue_enqueued_at)
        """
    )
    op.alter_column("analytics_jobs", "job_kind", server_default=None)


def downgrade() -> None:
    op.drop_index("idx_analytics_jobs_dispatch_lookup", table_name="analytics_jobs")
    op.drop_index("idx_analytics_jobs_parent_job_id", table_name="analytics_jobs")
    op.drop_index("ix_analytics_jobs_job_kind", table_name="analytics_jobs")
    op.drop_column("analytics_jobs", "queue_dispatched_at")
    op.drop_column("analytics_jobs", "parent_job_id")
    op.drop_column("analytics_jobs", "job_kind")
