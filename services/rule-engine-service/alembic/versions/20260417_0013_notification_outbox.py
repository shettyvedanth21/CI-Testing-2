"""Add durable notification outbox.

Revision ID: 20260417_0013_notification_outbox
Revises: 20260416_0012_rule_device_trigger_state
Create Date: 2026-04-17
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260417_0013_notification_outbox"
down_revision: Union[str, None] = "20260416_0012_rule_device_trigger_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("alert_id", sa.String(length=36), nullable=True),
        sa.Column("rule_id", sa.String(length=36), nullable=True),
        sa.Column("ledger_log_id", sa.String(length=36), nullable=True),
        sa.Column("device_id", sa.String(length=50), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("recipient_raw", sa.Text(), nullable=False),
        sa.Column("recipient_masked", sa.String(length=255), nullable=False),
        sa.Column("recipient_hash", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.alert_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.rule_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ledger_log_id"], ["notification_delivery_logs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ledger_log_id", name="uq_notification_outbox_ledger_log_id"),
        sa.UniqueConstraint("alert_id", "channel", "recipient_hash", name="uq_notification_outbox_alert_channel_recipient"),
    )
    op.create_index(
        "ix_notification_outbox_tenant_status_next_attempt",
        "notification_outbox",
        ["tenant_id", "status", "next_attempt_at"],
    )
    op.create_index(
        "ix_notification_outbox_tenant_channel_status",
        "notification_outbox",
        ["tenant_id", "channel", "status"],
    )
    op.create_index(
        "ix_notification_outbox_processing_started",
        "notification_outbox",
        ["status", "processing_started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_outbox_processing_started", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_tenant_channel_status", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_tenant_status_next_attempt", table_name="notification_outbox")
    op.drop_table("notification_outbox")
