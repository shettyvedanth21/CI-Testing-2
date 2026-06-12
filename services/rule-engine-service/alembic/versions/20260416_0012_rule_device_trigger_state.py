"""Track rule cooldown state per device instead of per rule.

Revision ID: 20260416_0012_rule_device_trigger_state
Revises: 20260416_0011_notification_delivery_hardening_constraints
Create Date: 2026-04-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0012_rule_device_trigger_state"
down_revision: Union[str, None] = "20260416_0011_notification_delivery_hardening_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rule_trigger_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_once", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            server_onupdate=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.rule_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "rule_id", "device_id", name="uq_rule_trigger_states_tenant_rule_device"),
    )
    op.create_index("ix_rule_trigger_states_tenant_rule", "rule_trigger_states", ["tenant_id", "rule_id"])
    op.create_index("ix_rule_trigger_states_tenant_device", "rule_trigger_states", ["tenant_id", "device_id"])


def downgrade() -> None:
    op.drop_index("ix_rule_trigger_states_tenant_device", table_name="rule_trigger_states")
    op.drop_index("ix_rule_trigger_states_tenant_rule", table_name="rule_trigger_states")
    op.drop_table("rule_trigger_states")
