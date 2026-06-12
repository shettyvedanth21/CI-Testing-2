"""Add per-device widget settings table for explicit config state.

Revision ID: add_dash_widget_cfg_state
Revises: add_device_dashboard_widgets
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa


revision = "add_dash_widget_cfg_state"
down_revision = "add_device_dashboard_widgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device_dashboard_widget_settings" not in inspector.get_table_names():
        op.create_table(
            "device_dashboard_widget_settings",
            sa.Column(
                "device_id",
                sa.String(length=50),
                sa.ForeignKey("devices.device_id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("is_configured", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device_dashboard_widget_settings" in inspector.get_table_names():
        op.drop_table("device_dashboard_widget_settings")
