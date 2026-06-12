"""Add persisted dashboard snapshots for low-latency reads.

Revision ID: add_dashboard_snapshots
Revises: add_waste_config_fields
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa


revision = "add_dashboard_snapshots"
down_revision = "add_waste_config_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "dashboard_snapshots" not in tables:
        op.create_table(
            "dashboard_snapshots",
            sa.Column("snapshot_key", sa.String(length=120), nullable=False, primary_key=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_dashboard_snapshots_generated_at", "dashboard_snapshots", ["generated_at"])
        op.create_index("ix_dashboard_snapshots_expires_at", "dashboard_snapshots", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "dashboard_snapshots" in tables:
        indexes = {x["name"] for x in inspector.get_indexes("dashboard_snapshots")}
        if "ix_dashboard_snapshots_expires_at" in indexes:
            op.drop_index("ix_dashboard_snapshots_expires_at", table_name="dashboard_snapshots")
        if "ix_dashboard_snapshots_generated_at" in indexes:
            op.drop_index("ix_dashboard_snapshots_generated_at", table_name="dashboard_snapshots")
        op.drop_table("dashboard_snapshots")
