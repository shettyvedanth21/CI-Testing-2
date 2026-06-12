"""Add platform maintenance announcement tables.

Revision ID: 0011_platform_maintenance
Revises: 0010_action_token_cleanup
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_platform_maintenance"
down_revision = "0010_action_token_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_maintenance_announcements",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "info",
                "warning",
                "critical",
                name="platformmaintenanceseverity",
            ),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estimated_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "scheduled",
                "active",
                "completed",
                "cancelled",
                name="platformmaintenancestatus",
            ),
            nullable=False,
        ),
        sa.Column("broadcast_all_tenants", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
    )
    op.create_index(
        "ix_platform_maintenance_announcements_status",
        "platform_maintenance_announcements",
        ["status"],
    )
    op.create_index(
        "ix_platform_maintenance_announcements_starts_at",
        "platform_maintenance_announcements",
        ["starts_at"],
    )
    op.create_index(
        "ix_platform_maintenance_announcements_ends_at",
        "platform_maintenance_announcements",
        ["ends_at"],
    )
    op.create_index(
        "ix_platform_maintenance_announcements_created_by_user_id",
        "platform_maintenance_announcements",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_platform_maintenance_announcements_updated_by_user_id",
        "platform_maintenance_announcements",
        ["updated_by_user_id"],
    )
    op.create_index(
        "ix_platform_maintenance_announcements_delivery_status",
        "platform_maintenance_announcements",
        ["status", "broadcast_all_tenants"],
    )

    op.create_table(
        "platform_maintenance_announcement_targets",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("announcement_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["announcement_id"],
            ["platform_maintenance_announcements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("announcement_id", "tenant_id", name="uq_platform_maintenance_target_announcement_tenant"),
    )
    op.create_index(
        "ix_platform_maintenance_targets_announcement_id",
        "platform_maintenance_announcement_targets",
        ["announcement_id"],
    )
    op.create_index(
        "ix_platform_maintenance_targets_tenant_id",
        "platform_maintenance_announcement_targets",
        ["tenant_id"],
    )

    op.create_table(
        "platform_maintenance_email_deliveries",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("announcement_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("user_role", sa.String(length=50), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("recipient_full_name", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "sent",
                "failed",
                "skipped",
                "cancelled",
                name="platformmaintenanceemaildeliverystatus",
            ),
            nullable=False,
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["announcement_id"], ["platform_maintenance_announcements.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "announcement_id",
            "user_id",
            name="uq_platform_maintenance_email_delivery_announcement_user",
        ),
    )
    op.create_index(
        "ix_platform_maintenance_email_deliveries_status_next_attempt",
        "platform_maintenance_email_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_platform_maintenance_email_deliveries_tenant_status",
        "platform_maintenance_email_deliveries",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_platform_maintenance_email_deliveries_announcement_status",
        "platform_maintenance_email_deliveries",
        ["announcement_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_maintenance_email_deliveries_announcement_status",
        table_name="platform_maintenance_email_deliveries",
    )
    op.drop_index(
        "ix_platform_maintenance_email_deliveries_tenant_status",
        table_name="platform_maintenance_email_deliveries",
    )
    op.drop_index(
        "ix_platform_maintenance_email_deliveries_status_next_attempt",
        table_name="platform_maintenance_email_deliveries",
    )
    op.drop_table("platform_maintenance_email_deliveries")
    op.drop_index(
        "ix_platform_maintenance_targets_tenant_id",
        table_name="platform_maintenance_announcement_targets",
    )
    op.drop_index(
        "ix_platform_maintenance_targets_announcement_id",
        table_name="platform_maintenance_announcement_targets",
    )
    op.drop_table("platform_maintenance_announcement_targets")
    op.drop_index(
        "ix_platform_maintenance_announcements_updated_by_user_id",
        table_name="platform_maintenance_announcements",
    )
    op.drop_index(
        "ix_platform_maintenance_announcements_delivery_status",
        table_name="platform_maintenance_announcements",
    )
    op.drop_index(
        "ix_platform_maintenance_announcements_created_by_user_id",
        table_name="platform_maintenance_announcements",
    )
    op.drop_index(
        "ix_platform_maintenance_announcements_ends_at",
        table_name="platform_maintenance_announcements",
    )
    op.drop_index(
        "ix_platform_maintenance_announcements_starts_at",
        table_name="platform_maintenance_announcements",
    )
    op.drop_index(
        "ix_platform_maintenance_announcements_status",
        table_name="platform_maintenance_announcements",
    )
    op.drop_table("platform_maintenance_announcements")
    sa.Enum(name="platformmaintenanceemaildeliverystatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="platformmaintenancestatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="platformmaintenanceseverity").drop(op.get_bind(), checkfirst=True)
