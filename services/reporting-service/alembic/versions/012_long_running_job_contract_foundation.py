"""Add long-running reporting job contract foundation.

Revision ID: 012_long_running_job_contract_foundation
Revises: 011_add_report_history_index
Create Date: 2026-04-26
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "012_long_running_job_contract_foundation"
down_revision: Union[str, None] = "011_add_report_history_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("energy_reports")}
    tables = set(inspector.get_table_names())

    if "phase" not in columns:
        op.add_column("energy_reports", sa.Column("phase", sa.String(length=50), nullable=True))
    if "phase_label" not in columns:
        op.add_column("energy_reports", sa.Column("phase_label", sa.String(length=255), nullable=True))
    if "phase_progress" not in columns:
        op.add_column("energy_reports", sa.Column("phase_progress", sa.Float(), nullable=True))

    bind.execute(
        sa.text(
            """
            UPDATE energy_reports
            SET
                phase = CASE
                    WHEN status = 'pending' THEN COALESCE(phase, 'queued')
                    WHEN status = 'processing' THEN COALESCE(phase, 'execution')
                    WHEN status = 'completed' THEN COALESCE(phase, 'completed')
                    WHEN status = 'failed' THEN COALESCE(phase, 'failed')
                    ELSE phase
                END,
                phase_label = CASE
                    WHEN status = 'pending' THEN COALESCE(phase_label, 'Queued')
                    WHEN status = 'processing' THEN COALESCE(phase_label, 'Running report generation')
                    WHEN status = 'completed' THEN COALESCE(phase_label, 'Completed')
                    WHEN status = 'failed' THEN COALESCE(phase_label, 'Failed')
                    ELSE phase_label
                END,
                phase_progress = CASE
                    WHEN status = 'pending' THEN COALESCE(phase_progress, 0)
                    WHEN status = 'processing' THEN COALESCE(phase_progress, 0.5)
                    WHEN status IN ('completed', 'failed') THEN COALESCE(phase_progress, 1)
                    ELSE phase_progress
                END
            """
        )
    )

    if "report_worker_heartbeats" not in tables:
        op.create_table(
            "report_worker_heartbeats",
            sa.Column("worker_id", sa.String(length=128), primary_key=True, nullable=False),
            sa.Column("app_role", sa.String(length=32), nullable=False, server_default="worker"),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="alive"),
        )
        op.create_index(
            "ix_report_worker_heartbeats_last_heartbeat_at",
            "report_worker_heartbeats",
            ["last_heartbeat_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("energy_reports")}
    tables = set(inspector.get_table_names())
    indexes = {
        index["name"]
        for index in inspector.get_indexes("report_worker_heartbeats")
    } if "report_worker_heartbeats" in tables else set()

    if "ix_report_worker_heartbeats_last_heartbeat_at" in indexes:
        op.drop_index(
            "ix_report_worker_heartbeats_last_heartbeat_at",
            table_name="report_worker_heartbeats",
        )
    if "report_worker_heartbeats" in tables:
        op.drop_table("report_worker_heartbeats")
    if "phase_progress" in columns:
        op.drop_column("energy_reports", "phase_progress")
    if "phase_label" in columns:
        op.drop_column("energy_reports", "phase_label")
    if "phase" in columns:
        op.drop_column("energy_reports", "phase")
