"""Add durable worker claim fields for reporting jobs.

Revision ID: 009_add_report_worker_claim_fields
Revises: 008_sh_tenant_id_hard_cut
Create Date: 2026-04-17
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "009_add_report_worker_claim_fields"
down_revision: Union[str, None] = "008_sh_tenant_id_hard_cut"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("energy_reports")}
    indexes = {index["name"] for index in inspector.get_indexes("energy_reports")}

    if "enqueued_at" not in columns:
        op.add_column("energy_reports", sa.Column("enqueued_at", sa.DateTime(), nullable=True))
    if "processing_started_at" not in columns:
        op.add_column("energy_reports", sa.Column("processing_started_at", sa.DateTime(), nullable=True))
    if "worker_id" not in columns:
        op.add_column("energy_reports", sa.Column("worker_id", sa.String(length=128), nullable=True))
    if "retry_count" not in columns:
        op.add_column(
            "energy_reports",
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "timeout_count" not in columns:
        op.add_column(
            "energy_reports",
            sa.Column("timeout_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "last_attempt_at" not in columns:
        op.add_column("energy_reports", sa.Column("last_attempt_at", sa.DateTime(), nullable=True))

    bind.execute(sa.text("UPDATE energy_reports SET enqueued_at = COALESCE(enqueued_at, created_at)"))

    if "ix_energy_reports_status_processing_started" not in indexes:
        op.create_index(
            "ix_energy_reports_status_processing_started",
            "energy_reports",
            ["status", "processing_started_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("energy_reports")}
    indexes = {index["name"] for index in inspector.get_indexes("energy_reports")}

    if "ix_energy_reports_status_processing_started" in indexes:
        op.drop_index("ix_energy_reports_status_processing_started", table_name="energy_reports")
    if "last_attempt_at" in columns:
        op.drop_column("energy_reports", "last_attempt_at")
    if "timeout_count" in columns:
        op.drop_column("energy_reports", "timeout_count")
    if "retry_count" in columns:
        op.drop_column("energy_reports", "retry_count")
    if "worker_id" in columns:
        op.drop_column("energy_reports", "worker_id")
    if "processing_started_at" in columns:
        op.drop_column("energy_reports", "processing_started_at")
    if "enqueued_at" in columns:
        op.drop_column("energy_reports", "enqueued_at")
