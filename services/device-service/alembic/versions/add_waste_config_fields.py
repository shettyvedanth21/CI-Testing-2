"""Add waste config fields for overconsumption and unoccupied windows.

Revision ID: add_waste_config_fields
Revises: 20260326_0001
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa


revision = "add_waste_config_fields"
down_revision = "20260326_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    device_cols = {c["name"] for c in inspector.get_columns("devices")}
    if "overconsumption_current_threshold_a" not in device_cols:
        op.add_column("devices", sa.Column("overconsumption_current_threshold_a", sa.Numeric(10, 4), nullable=True))
    if "unoccupied_weekday_start_time" not in device_cols:
        op.add_column("devices", sa.Column("unoccupied_weekday_start_time", sa.Time(), nullable=True))
    if "unoccupied_weekday_end_time" not in device_cols:
        op.add_column("devices", sa.Column("unoccupied_weekday_end_time", sa.Time(), nullable=True))
    if "unoccupied_weekend_start_time" not in device_cols:
        op.add_column("devices", sa.Column("unoccupied_weekend_start_time", sa.Time(), nullable=True))
    if "unoccupied_weekend_end_time" not in device_cols:
        op.add_column("devices", sa.Column("unoccupied_weekend_end_time", sa.Time(), nullable=True))

    tables = set(inspector.get_table_names())
    if "waste_site_config" not in tables:
        op.create_table(
            "waste_site_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=50), nullable=True),
            sa.Column("default_unoccupied_weekday_start_time", sa.Time(), nullable=True),
            sa.Column("default_unoccupied_weekday_end_time", sa.Time(), nullable=True),
            sa.Column("default_unoccupied_weekend_start_time", sa.Time(), nullable=True),
            sa.Column("default_unoccupied_weekend_end_time", sa.Time(), nullable=True),
            sa.Column("timezone", sa.String(length=50), nullable=True),
            sa.Column("updated_by", sa.String(length=100), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", name="uq_waste_site_config_tenant"),
        )
        op.create_index("ix_waste_site_config_tenant_id", "waste_site_config", ["tenant_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = set(inspector.get_table_names())
    if "waste_site_config" in tables:
        indexes = {x["name"] for x in inspector.get_indexes("waste_site_config")}
        if "ix_waste_site_config_tenant_id" in indexes:
            op.drop_index("ix_waste_site_config_tenant_id", table_name="waste_site_config")
        op.drop_table("waste_site_config")

    device_cols = {c["name"] for c in inspector.get_columns("devices")}
    for col in [
        "unoccupied_weekend_end_time",
        "unoccupied_weekend_start_time",
        "unoccupied_weekday_end_time",
        "unoccupied_weekday_start_time",
        "overconsumption_current_threshold_a",
    ]:
        if col in device_cols:
            op.drop_column("devices", col)
