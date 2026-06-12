"""Add first-class tenant_id to waste device summaries.

Revision ID: 010_waste_device_summary_tenant_id
Revises: 009_waste_async_job_contract_foundation
Create Date: 2026-04-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = "010_waste_device_summary_tenant_id"
down_revision: Union[str, None] = "009_waste_async_job_contract_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("waste_device_summary")}
    indexes = {index["name"] for index in inspector.get_indexes("waste_device_summary")}

    if "tenant_id" not in columns:
        op.add_column("waste_device_summary", sa.Column("tenant_id", sa.String(length=10), nullable=True))

    bind.execute(
        text(
            """
            UPDATE waste_device_summary summary
            JOIN waste_analysis_jobs jobs
              ON jobs.id = summary.job_id
            SET summary.tenant_id = jobs.tenant_id
            WHERE summary.tenant_id IS NULL
            """
        )
    )

    bind.execute(
        text(
            """
            DELETE summary
            FROM waste_device_summary summary
            LEFT JOIN waste_analysis_jobs jobs
              ON jobs.id = summary.job_id
            WHERE summary.tenant_id IS NULL
               OR jobs.id IS NULL
            """
        )
    )

    if "idx_waste_summary_tenant_job" not in indexes:
        op.create_index("idx_waste_summary_tenant_job", "waste_device_summary", ["tenant_id", "job_id"], unique=False)
    if "ix_waste_device_summary_tenant_id" not in indexes:
        op.create_index("ix_waste_device_summary_tenant_id", "waste_device_summary", ["tenant_id"], unique=False)

    op.alter_column("waste_device_summary", "tenant_id", existing_type=sa.String(length=10), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("waste_device_summary")}
    indexes = {index["name"] for index in inspector.get_indexes("waste_device_summary")}

    if "idx_waste_summary_tenant_job" in indexes:
        op.drop_index("idx_waste_summary_tenant_job", table_name="waste_device_summary")
    if "ix_waste_device_summary_tenant_id" in indexes:
        op.drop_index("ix_waste_device_summary_tenant_id", table_name="waste_device_summary")
    if "tenant_id" in columns:
        op.drop_column("waste_device_summary", "tenant_id")
