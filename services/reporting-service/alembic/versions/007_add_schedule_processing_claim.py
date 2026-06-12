"""Add processing claim state for scheduled reports.

Revision ID: 007_add_schedule_processing_claim
Revises: 006_unify_tariff_source_of_truth
Create Date: 2026-04-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "007_add_schedule_processing_claim"
down_revision: Union[str, None] = "006_unify_tariff_source_of_truth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("scheduled_reports")}
    indexes = {index["name"] for index in inspector.get_indexes("scheduled_reports")}

    if "processing_started_at" not in columns:
        op.add_column(
            "scheduled_reports",
            sa.Column("processing_started_at", sa.DateTime(), nullable=True),
        )

    if "ix_scheduled_reports_due_claim" not in indexes:
        op.create_index(
            "ix_scheduled_reports_due_claim",
            "scheduled_reports",
            ["tenant_id", "is_active", "next_run_at", "processing_started_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("scheduled_reports")}
    columns = {column["name"] for column in inspector.get_columns("scheduled_reports")}

    if "ix_scheduled_reports_due_claim" in indexes:
        op.drop_index("ix_scheduled_reports_due_claim", table_name="scheduled_reports")
    if "processing_started_at" in columns:
        op.drop_column("scheduled_reports", "processing_started_at")

