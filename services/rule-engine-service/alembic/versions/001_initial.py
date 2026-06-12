"""Initial migration - create rules and alerts tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-03-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=True),
        sa.Column("rule_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(length=50), nullable=False),
        sa.Column("property", sa.String(length=100), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("notification_channels", sa.JSON(), nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_ids", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("rule_id"),
    )

    op.create_index("ix_rules_tenant_id", "rules", ["tenant_id"])
    op.create_index("ix_rules_property", "rules", ["property"])
    op.create_index("ix_rules_status", "rules", ["status"])

    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=True),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("actual_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.rule_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("alert_id"),
    )

    op.create_index("ix_alerts_tenant_id", "alerts", ["tenant_id"])
    op.create_index("ix_alerts_rule_id", "alerts", ["rule_id"])
    op.create_index("ix_alerts_device_id", "alerts", ["device_id"])
    op.create_index("ix_alerts_status", "alerts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_alerts_status", table_name="alerts")
    op.drop_index("ix_alerts_device_id", table_name="alerts")
    op.drop_index("ix_alerts_rule_id", table_name="alerts")
    op.drop_index("ix_alerts_tenant_id", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_rules_status", table_name="rules")
    op.drop_index("ix_rules_property", table_name="rules")
    op.drop_index("ix_rules_tenant_id", table_name="rules")
    op.drop_table("rules")
