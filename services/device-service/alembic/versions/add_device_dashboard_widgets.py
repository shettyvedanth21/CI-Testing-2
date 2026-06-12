"""Add per-device dashboard widget configuration table.

Revision ID: add_device_dashboard_widgets
Revises: add_idle_running_config_and_log
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa


revision = "add_device_dashboard_widgets"
down_revision = "add_idle_running_config_and_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "device_dashboard_widgets" not in existing_tables:
        op.create_table(
            "device_dashboard_widgets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("device_id", sa.String(length=50), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
            sa.Column("field_name", sa.String(length=100), nullable=False),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("device_id", "field_name", name="uq_device_dashboard_widget"),
        )

    existing_indexes = (
        {idx["name"] for idx in inspector.get_indexes("device_dashboard_widgets")}
        if "device_dashboard_widgets" in inspector.get_table_names()
        else set()
    )
    if "ix_device_dashboard_widgets_device_id" not in existing_indexes:
        op.create_index("ix_device_dashboard_widgets_device_id", "device_dashboard_widgets", ["device_id"])
    if "ix_device_dashboard_widgets_device_order" not in existing_indexes:
        op.create_index("ix_device_dashboard_widgets_device_order", "device_dashboard_widgets", ["device_id", "display_order"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device_dashboard_widgets" in inspector.get_table_names():
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("device_dashboard_widgets")}
        if "ix_device_dashboard_widgets_device_order" in existing_indexes:
            op.drop_index("ix_device_dashboard_widgets_device_order", table_name="device_dashboard_widgets")
        if "ix_device_dashboard_widgets_device_id" in existing_indexes:
            op.drop_index("ix_device_dashboard_widgets_device_id", table_name="device_dashboard_widgets")
        op.drop_table("device_dashboard_widgets")
