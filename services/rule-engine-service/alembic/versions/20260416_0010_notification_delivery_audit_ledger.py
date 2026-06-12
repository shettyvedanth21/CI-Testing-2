"""Add permanent notification delivery audit ledger.

Revision ID: 20260416_0010_notification_delivery_audit_ledger
Revises: 20260412_0009
Create Date: 2026-04-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0010_notification_delivery_audit_ledger"
down_revision: Union[str, None] = "20260412_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_delivery_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=True),
        sa.Column("alert_id", sa.String(length=36), nullable=True),
        sa.Column("rule_id", sa.String(length=36), nullable=True),
        sa.Column("device_id", sa.String(length=50), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("recipient_masked", sa.String(length=255), nullable=False),
        sa.Column("recipient_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("billable_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.alert_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.rule_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_delivery_logs_tenant_id", "notification_delivery_logs", ["tenant_id"])
    op.create_index("ix_notification_delivery_logs_status", "notification_delivery_logs", ["status"])
    op.create_index(
        "ix_notification_delivery_logs_tenant_attempted_at",
        "notification_delivery_logs",
        ["tenant_id", "attempted_at"],
    )
    op.create_index(
        "ix_notification_delivery_logs_tenant_channel_attempted_at",
        "notification_delivery_logs",
        ["tenant_id", "channel", "attempted_at"],
    )
    op.create_index(
        "ix_notification_delivery_logs_tenant_status_attempted_at",
        "notification_delivery_logs",
        ["tenant_id", "status", "attempted_at"],
    )
    op.create_index("ix_notification_delivery_logs_rule_id", "notification_delivery_logs", ["rule_id"])
    op.create_index("ix_notification_delivery_logs_alert_id", "notification_delivery_logs", ["alert_id"])
    op.create_index(
        "ix_notification_delivery_logs_provider_message_id",
        "notification_delivery_logs",
        ["provider_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_delivery_logs_provider_message_id", table_name="notification_delivery_logs")
    op.drop_index("ix_notification_delivery_logs_alert_id", table_name="notification_delivery_logs")
    op.drop_index("ix_notification_delivery_logs_rule_id", table_name="notification_delivery_logs")
    op.drop_index("ix_notification_delivery_logs_tenant_status_attempted_at", table_name="notification_delivery_logs")
    op.drop_index("ix_notification_delivery_logs_tenant_channel_attempted_at", table_name="notification_delivery_logs")
    op.drop_index("ix_notification_delivery_logs_tenant_attempted_at", table_name="notification_delivery_logs")
    op.drop_index("ix_notification_delivery_logs_status", table_name="notification_delivery_logs")
    op.drop_index("ix_notification_delivery_logs_tenant_id", table_name="notification_delivery_logs")
    op.drop_table("notification_delivery_logs")
