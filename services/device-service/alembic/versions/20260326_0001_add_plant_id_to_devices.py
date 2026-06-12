"""add plant_id to devices

Revision ID: 20260326_0001
Revises: shft_ovlp_dedup_v1
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "20260326_0001"
down_revision = "shft_ovlp_dedup_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "plant_id",
            sa.String(36),
            nullable=True,
            comment="References plants.id in auth-service. Soft reference - no DB-level FK constraint across service boundaries.",
        ),
    )
    op.create_index("ix_devices_plant_id", "devices", ["plant_id"])


def downgrade() -> None:
    op.drop_index("ix_devices_plant_id", table_name="devices")
    op.drop_column("devices", "plant_id")
