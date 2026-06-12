"""Add tenant_id to scoped tables and backfill ownership.

Revision ID: 20260331_0001_add_tenant_id_to_scoped_tables
Revises: 20260329_0001
Create Date: 2026-03-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260331_0001_add_tenant_id_to_scoped_tables"
down_revision = "20260329_0001"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    # Step 1
    if not _column_exists("device_performance_trends", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE device_performance_trends
                ADD COLUMN tenant_id VARCHAR(50) NULL
                """
            )
        )
    # Step 2
    op.execute(
        sa.text(
            """
            UPDATE device_performance_trends dpt
            JOIN devices d ON dpt.device_id = d.device_id
            SET dpt.tenant_id = d.tenant_id
            WHERE dpt.tenant_id IS NULL
            """
        )
    )

    # Step 3
    if not _column_exists("device_properties", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE device_properties
                ADD COLUMN tenant_id VARCHAR(50) NULL
                """
            )
        )
    op.execute(
        sa.text(
            """
            UPDATE device_properties dp
            JOIN devices d ON dp.device_id = d.device_id
            SET dp.tenant_id = d.tenant_id
            WHERE dp.tenant_id IS NULL
            """
        )
    )

    # Step 4
    op.execute(
        sa.text(
            """
            UPDATE idle_running_log irl
            JOIN devices d ON irl.device_id = d.device_id
            SET irl.tenant_id = d.tenant_id
            WHERE irl.tenant_id IS NULL
            """
        )
    )

    # Step 5
    if not _index_exists("device_performance_trends", "ix_dpt_tenant_id"):
        op.execute(
            sa.text(
                """
                CREATE INDEX ix_dpt_tenant_id
                ON device_performance_trends(tenant_id)
                """
            )
        )
    if not _index_exists("device_properties", "ix_dp_tenant_id"):
        op.execute(
            sa.text(
                """
                CREATE INDEX ix_dp_tenant_id
                ON device_properties(tenant_id)
                """
            )
        )


def downgrade() -> None:
    if _index_exists("device_properties", "ix_dp_tenant_id"):
        op.execute(sa.text("DROP INDEX ix_dp_tenant_id ON device_properties"))
    if _index_exists("device_performance_trends", "ix_dpt_tenant_id"):
        op.execute(sa.text("DROP INDEX ix_dpt_tenant_id ON device_performance_trends"))

    op.execute(
        sa.text(
            """
            UPDATE idle_running_log
            SET tenant_id = NULL
            """
        )
    )
    if _column_exists("device_properties", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE device_properties
                DROP COLUMN tenant_id
                """
            )
        )
    if _column_exists("device_performance_trends", "tenant_id"):
        op.execute(
            sa.text(
                """
                ALTER TABLE device_performance_trends
                DROP COLUMN tenant_id
                """
            )
        )
