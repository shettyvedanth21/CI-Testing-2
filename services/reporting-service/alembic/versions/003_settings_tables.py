"""Add settings tables for tariff and notification channels

Revision ID: 003_settings_tables
Revises: 002_add_last_result_url
Create Date: 2026-03-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003_settings_tables"
down_revision: Union[str, None] = "002_add_last_result_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tariff_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rate", sa.Numeric(10, 4), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_type", sa.String(20), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_channels_channel_type", "notification_channels", ["channel_type"])
    op.create_index("ix_notification_channels_is_active", "notification_channels", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_notification_channels_is_active", table_name="notification_channels")
    op.drop_index("ix_notification_channels_channel_type", table_name="notification_channels")
    op.drop_table("notification_channels")
    op.drop_table("tariff_config")
