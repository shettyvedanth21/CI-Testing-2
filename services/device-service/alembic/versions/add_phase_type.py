"""Add phase_type to devices table

Revision ID: add_phase_type
Revises: 
Create Date: 2026-02-23

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_phase_type'
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    device_cols = {c["name"] for c in inspector.get_columns("devices")}
    if "phase_type" not in device_cols:
        op.add_column("devices", sa.Column("phase_type", sa.String(20), nullable=True))
    existing_indexes = {i["name"] for i in inspector.get_indexes("devices")}
    if "ix_devices_phase_type" not in existing_indexes:
        op.create_index("ix_devices_phase_type", "devices", ["phase_type"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {i["name"] for i in inspector.get_indexes("devices")}
    if "ix_devices_phase_type" in existing_indexes:
        op.drop_index("ix_devices_phase_type", table_name="devices")
    device_cols = {c["name"] for c in inspector.get_columns("devices")}
    if "phase_type" in device_cols:
        op.drop_column("devices", "phase_type")
