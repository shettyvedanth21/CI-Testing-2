"""add phase-aware progress columns for analytics jobs

Revision ID: 0005_job_phase_tracking
Revises: 0004_artifact_longblob
Create Date: 2026-04-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_job_phase_tracking"
down_revision = "0004_artifact_longblob"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analytics_jobs", sa.Column("phase", sa.String(length=50), nullable=True))
    op.add_column("analytics_jobs", sa.Column("phase_label", sa.String(length=255), nullable=True))
    op.add_column("analytics_jobs", sa.Column("phase_progress", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("analytics_jobs", "phase_progress")
    op.drop_column("analytics_jobs", "phase_label")
    op.drop_column("analytics_jobs", "phase")
