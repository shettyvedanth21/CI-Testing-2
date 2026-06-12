"""Add immutable first telemetry timestamp to devices.

Revision ID: 20260412_0001
Revises: 20260411_0001_sh_tenant_id_hard_cut
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260412_0001"
down_revision = "20260411_0001_sh_tenant_id_hard_cut"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "first_telemetry_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_devices_first_telemetry_timestamp",
        "devices",
        ["first_telemetry_timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_devices_first_telemetry_timestamp", table_name="devices")
    op.drop_column("devices", "first_telemetry_timestamp")
