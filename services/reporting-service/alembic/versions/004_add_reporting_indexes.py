"""Add reporting/settings composite indexes for list and lookup paths.

Revision ID: 004_add_reporting_indexes
Revises: 003_settings_tables
Create Date: 2026-03-08
"""

from alembic import op
from sqlalchemy import inspect


revision = "004_add_reporting_indexes"
down_revision = "003_settings_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    report_indexes = {idx["name"] for idx in inspector.get_indexes("energy_reports")}
    if "ix_energy_reports_status_created" not in report_indexes:
        op.create_index("ix_energy_reports_status_created", "energy_reports", ["status", "created_at"])

    notif_indexes = {idx["name"] for idx in inspector.get_indexes("notification_channels")}
    if "ix_notification_channels_type_active" not in notif_indexes:
        op.create_index(
            "ix_notification_channels_type_active",
            "notification_channels",
            ["channel_type", "is_active"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    notif_indexes = {idx["name"] for idx in inspector.get_indexes("notification_channels")}
    if "ix_notification_channels_type_active" in notif_indexes:
        op.drop_index("ix_notification_channels_type_active", table_name="notification_channels")

    report_indexes = {idx["name"] for idx in inspector.get_indexes("energy_reports")}
    if "ix_energy_reports_status_created" in report_indexes:
        op.drop_index("ix_energy_reports_status_created", table_name="energy_reports")
