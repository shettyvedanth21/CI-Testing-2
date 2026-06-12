"""add telemetry outbox and reconciliation log tables

Revision ID: 20260324_0001
Revises:
Create Date: 2026-03-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260324_0001"
down_revision = None
branch_labels = None
depends_on = None


outbox_target_enum = sa.Enum("device-service", "energy-service", name="telemetry_outbox_target")
outbox_status_enum = sa.Enum("pending", "delivered", "failed", "dead", name="telemetry_outbox_status")


def upgrade() -> None:
    bind = op.get_bind()
    outbox_target_enum.create(bind, checkfirst=True)
    outbox_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "telemetry_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("telemetry_json", sa.JSON(), nullable=False),
        sa.Column("target", outbox_target_enum, nullable=False),
        sa.Column("status", outbox_status_enum, nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_attempted_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_telemetry_outbox_status_created_at",
        "telemetry_outbox",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_telemetry_outbox_device_id_status",
        "telemetry_outbox",
        ["device_id", "status"],
    )

    op.create_table(
        "reconciliation_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("influx_ts", sa.DateTime(timezone=False), nullable=True),
        sa.Column("mysql_ts", sa.DateTime(timezone=False), nullable=True),
        sa.Column("drift_seconds", sa.Integer(), nullable=True),
        sa.Column("action_taken", sa.String(length=255), nullable=False, server_default="none"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reconciliation_log_device_checked_at",
        "reconciliation_log",
        ["device_id", "checked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reconciliation_log_device_checked_at", table_name="reconciliation_log")
    op.drop_table("reconciliation_log")

    op.drop_index("ix_telemetry_outbox_device_id_status", table_name="telemetry_outbox")
    op.drop_index("ix_telemetry_outbox_status_created_at", table_name="telemetry_outbox")
    op.drop_table("telemetry_outbox")

    bind = op.get_bind()
    outbox_status_enum.drop(bind, checkfirst=True)
    outbox_target_enum.drop(bind, checkfirst=True)
