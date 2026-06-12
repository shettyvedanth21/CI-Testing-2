"""Add updated_at column to anomaly daily/weekly count tables.

Adds updated_at to machine_anomaly_daily_counts and
machine_anomaly_weekly_counts so that re-aggregation refreshes
the freshness timestamp without altering the original created_at.

Backfills updated_at = created_at for existing rows.

Revision ID: 20260524_0001_anomaly_count_updated_at
Revises: 20260522_0005_mhh_status_constraint_and_drop_phase_status
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260524_0001_anomaly_count_updated_at"
down_revision = "20260522_0005_mhh_status_constraint_and_drop_phase_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    daily_table = "machine_anomaly_daily_counts"
    weekly_table = "machine_anomaly_weekly_counts"

    if daily_table in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns(daily_table)}
        if "updated_at" not in existing_columns:
            op.add_column(
                daily_table,
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                ),
            )
            bind.execute(
                sa.text(
                    f"UPDATE {daily_table} SET updated_at = created_at"
                )
            )

    if weekly_table in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns(weekly_table)}
        if "updated_at" not in existing_columns:
            op.add_column(
                weekly_table,
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                ),
            )
            bind.execute(
                sa.text(
                    f"UPDATE {weekly_table} SET updated_at = created_at"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    daily_table = "machine_anomaly_daily_counts"
    weekly_table = "machine_anomaly_weekly_counts"

    if daily_table in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns(daily_table)}
        if "updated_at" in existing_columns:
            op.drop_column(daily_table, "updated_at")

    if weekly_table in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns(weekly_table)}
        if "updated_at" in existing_columns:
            op.drop_column(weekly_table, "updated_at")
