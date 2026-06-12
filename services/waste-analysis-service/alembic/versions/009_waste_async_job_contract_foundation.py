"""Add waste async job contract foundation fields.

Revision ID: 009_waste_async_job_contract_foundation
Revises: 008_expand_skipped_reason_columns
Create Date: 2026-04-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = "009_waste_async_job_contract_foundation"
down_revision: Union[str, None] = "008_expand_skipped_reason_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("waste_analysis_jobs")}

    if "started_at" not in columns:
        op.add_column("waste_analysis_jobs", sa.Column("started_at", sa.DateTime(), nullable=True))

    bind.execute(
        text(
            """
            UPDATE waste_analysis_jobs
            SET started_at = COALESCE(started_at, created_at)
            WHERE started_at IS NULL
              AND status IN ('running', 'completed', 'failed')
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("waste_analysis_jobs")}
    if "started_at" in columns:
        op.drop_column("waste_analysis_jobs", "started_at")
