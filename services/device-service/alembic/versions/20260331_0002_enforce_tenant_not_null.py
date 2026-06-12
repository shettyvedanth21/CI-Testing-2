"""Enforce tenant_id NOT NULL on scoped device tables.

Revision ID: 20260331_0002_enforce_tenant_not_null
Revises: 20260331_0001_add_tenant_id_to_scoped_tables
Create Date: 2026-03-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260331_0002_enforce_tenant_not_null"
down_revision = "20260331_0001_add_tenant_id_to_scoped_tables"
branch_labels = None
depends_on = None


_TABLES = (
    "device_performance_trends",
    "device_properties",
    "idle_running_log",
    "device_live_state",
)


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _table_null_count(table_name: str) -> int:
    bind = op.get_bind()
    result = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE tenant_id IS NULL"))
    value = result.scalar()
    return int(value or 0)


def upgrade() -> None:
    bind = op.get_bind()
    verification = bind.execute(
        sa.text(
            """
            SELECT
              SUM(CASE WHEN (SELECT COUNT(*) FROM device_performance_trends WHERE tenant_id IS NULL) > 0 THEN 1 ELSE 0 END) +
              SUM(CASE WHEN (SELECT COUNT(*) FROM device_properties WHERE tenant_id IS NULL) > 0 THEN 1 ELSE 0 END) +
              SUM(CASE WHEN (SELECT COUNT(*) FROM idle_running_log WHERE tenant_id IS NULL) > 0 THEN 1 ELSE 0 END) +
              SUM(CASE WHEN (SELECT COUNT(*) FROM device_live_state WHERE tenant_id IS NULL) > 0 THEN 1 ELSE 0 END)
            AS total_nulls FROM dual
            """
        )
    ).scalar()
    counts = int(verification or 0)
    if counts > 0:
        null_tables = [table for table in _TABLES if _table_null_count(table) > 0]
        raise Exception(
            f"Migration aborted: {counts} tables still have NULL tenant_id rows: {null_tables}. "
            "Fix application layer first."
        )

    if _column_exists("device_performance_trends", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE device_performance_trends
                MODIFY COLUMN tenant_id VARCHAR(50) NOT NULL
                """
            )
        )

    if _column_exists("device_properties", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE device_properties
                MODIFY COLUMN tenant_id VARCHAR(50) NOT NULL
                """
            )
        )

    if _column_exists("idle_running_log", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE idle_running_log
                MODIFY COLUMN tenant_id VARCHAR(50) NOT NULL
                """
            )
        )


def downgrade() -> None:
    if _column_exists("idle_running_log", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE idle_running_log
                MODIFY COLUMN tenant_id VARCHAR(50) NULL
                """
            )
        )

    if _column_exists("device_properties", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE device_properties
                MODIFY COLUMN tenant_id VARCHAR(50) NULL
                """
            )
        )

    if _column_exists("device_performance_trends", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE device_performance_trends
                MODIFY COLUMN tenant_id VARCHAR(50) NULL
                """
            )
        )
