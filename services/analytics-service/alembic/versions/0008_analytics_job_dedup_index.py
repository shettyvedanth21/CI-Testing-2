"""Add analytics job dedup lookup index.

Revision ID: 0008_analytics_job_dedup_index
Revises: 0007_first_class_tenant_columns
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "0008_analytics_job_dedup_index"
down_revision = "0007_first_class_tenant_columns"
branch_labels = None
depends_on = None


def _has_index(indexes: list[dict[str, object]], name: str) -> bool:
    return any(index.get("name") == name for index in indexes)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = inspector.get_indexes("analytics_jobs")
    if not _has_index(indexes, "idx_analytics_jobs_dedup_lookup"):
        op.create_index(
            "idx_analytics_jobs_dedup_lookup",
            "analytics_jobs",
            ["tenant_id", "device_id", "analysis_type", "model_name", "status", "date_range_start"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = inspector.get_indexes("analytics_jobs")
    if _has_index(indexes, "idx_analytics_jobs_dedup_lookup"):
        op.drop_index("idx_analytics_jobs_dedup_lookup", table_name="analytics_jobs")
