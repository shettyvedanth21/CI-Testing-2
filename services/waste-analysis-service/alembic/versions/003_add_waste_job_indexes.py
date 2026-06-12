"""Add waste job composite indexes for history/status queries.

Revision ID: 003_add_waste_job_indexes
Revises: 002_quality_gate_fields
Create Date: 2026-03-08
"""

from alembic import op
from sqlalchemy import inspect


revision = "003_add_waste_job_indexes"
down_revision = "002_quality_gate_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    job_indexes = {idx["name"] for idx in inspector.get_indexes("waste_analysis_jobs")}
    if "idx_waste_jobs_status_created" not in job_indexes:
        op.create_index("idx_waste_jobs_status_created", "waste_analysis_jobs", ["status", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    job_indexes = {idx["name"] for idx in inspector.get_indexes("waste_analysis_jobs")}
    if "idx_waste_jobs_status_created" in job_indexes:
        op.drop_index("idx_waste_jobs_status_created", table_name="waste_analysis_jobs")
