"""Add signal_breakdown_json to machine_anomaly_daily_counts.

Revision ID: 20260522_0002_anomaly_signal_breakdown_json
Revises: 20260522_0001_machine_anomaly_activity
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260522_0002_anomaly_signal_breakdown_json"
down_revision = "20260522_0001_machine_anomaly_activity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_anomaly_daily_counts" in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns("machine_anomaly_daily_counts")}
        if "signal_breakdown_json" not in existing_columns:
            op.add_column(
                "machine_anomaly_daily_counts",
                sa.Column("signal_breakdown_json", sa.Text(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_anomaly_daily_counts" in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns("machine_anomaly_daily_counts")}
        if "signal_breakdown_json" in existing_columns:
            op.drop_column("machine_anomaly_daily_counts", "signal_breakdown_json")
