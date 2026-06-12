"""Add worker tracking columns and heartbeat table for queue/worker architecture.

Revision ID: 011_waste_worker_queue_columns
Revises: 010_waste_device_summary_tenant_id
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = "011_waste_worker_queue_columns"
down_revision: Union[str, None] = "010_waste_device_summary_tenant_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("waste_analysis_jobs")}

    new_columns = [
        ("worker_id", sa.String(255), True),
        # Add new numeric worker-state columns as nullable first so existing
        # rows can be backfilled before the MySQL tighten-to-NOT-NULL step.
        ("retry_count", sa.Integer(), True),
        ("timeout_count", sa.Integer(), True),
        ("processing_started_at", sa.DateTime(), True),
        ("worker_lease_expires_at", sa.DateTime(), True),
        ("last_heartbeat_at", sa.DateTime(), True),
    ]

    for col_name, col_type, nullable in new_columns:
        if col_name not in existing_columns:
            op.add_column(
                "waste_analysis_jobs",
                sa.Column(col_name, col_type, nullable=nullable),
            )

    if "retry_count" not in existing_columns:
        op.execute(
            text(
                "UPDATE waste_analysis_jobs SET retry_count = 0 WHERE retry_count IS NULL"
            )
        )
        op.alter_column(
            "waste_analysis_jobs",
            "retry_count",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )

    if "timeout_count" not in existing_columns:
        op.execute(
            text(
                "UPDATE waste_analysis_jobs SET timeout_count = 0 WHERE timeout_count IS NULL"
            )
        )
        op.alter_column(
            "waste_analysis_jobs",
            "timeout_count",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )

    existing_tables = inspector.get_table_names()
    if "waste_worker_heartbeat" not in existing_tables:
        op.create_table(
            "waste_worker_heartbeat",
            sa.Column("worker_id", sa.String(255), primary_key=True),
            sa.Column("app_role", sa.String(64), nullable=False, server_default="worker"),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="alive"),
        )

    existing_status_col = None
    for col in inspector.get_columns("waste_analysis_jobs"):
        if col["name"] == "status":
            existing_status_col = col
            break
    if existing_status_col is not None:
        status_type = getattr(existing_status_col.get("type"), "enums", None)
        if status_type and "enqueue_failed" not in status_type:
            op.alter_column(
                "waste_analysis_jobs",
                "status",
                type_=sa.Enum(
                    "pending",
                    "running",
                    "completed",
                    "failed",
                    "enqueue_failed",
                    name="wastestatus",
                ),
                existing_type=sa.Enum("pending", "running", "completed", "failed", name="wastestatus"),
                existing_nullable=False,
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("waste_analysis_jobs")}

    for col_name in [
        "last_heartbeat_at",
        "worker_lease_expires_at",
        "processing_started_at",
        "timeout_count",
        "retry_count",
        "worker_id",
    ]:
        if col_name in existing_columns:
            op.drop_column("waste_analysis_jobs", col_name)

    existing_tables = inspector.get_table_names()
    if "waste_worker_heartbeat" in existing_tables:
        op.drop_table("waste_worker_heartbeat")
