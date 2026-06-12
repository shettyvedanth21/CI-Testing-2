"""Add rules v2 fields for time-based rules and no-repeat cooldown.

Revision ID: 003_rules_v2
Revises: 002_activity_events
Create Date: 2026-03-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003_rules_v2"
down_revision: Union[str, None] = "002_activity_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("rules")}

    if "rule_type" not in existing_cols:
        op.add_column("rules", sa.Column("rule_type", sa.String(length=20), nullable=False, server_default="threshold"))
    if "cooldown_mode" not in existing_cols:
        op.add_column("rules", sa.Column("cooldown_mode", sa.String(length=20), nullable=False, server_default="interval"))
    if "time_window_start" not in existing_cols:
        op.add_column("rules", sa.Column("time_window_start", sa.String(length=5), nullable=True))
    if "time_window_end" not in existing_cols:
        op.add_column("rules", sa.Column("time_window_end", sa.String(length=5), nullable=True))
    if "timezone" not in existing_cols:
        op.add_column("rules", sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"))
    if "time_condition" not in existing_cols:
        op.add_column("rules", sa.Column("time_condition", sa.String(length=50), nullable=True))
    if "triggered_once" not in existing_cols:
        op.add_column("rules", sa.Column("triggered_once", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.alter_column("rules", "property", existing_type=sa.String(length=100), nullable=True)
    op.alter_column("rules", "condition", existing_type=sa.String(length=20), nullable=True)
    op.alter_column("rules", "threshold", existing_type=sa.Float(), nullable=True)

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("rules")}
    if "ix_rules_rule_type" not in existing_indexes:
        op.create_index("ix_rules_rule_type", "rules", ["rule_type"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("rules")}
    existing_cols = {col["name"] for col in inspector.get_columns("rules")}

    if "ix_rules_rule_type" in existing_indexes:
        op.drop_index("ix_rules_rule_type", table_name="rules")

    op.alter_column("rules", "threshold", existing_type=sa.Float(), nullable=False)
    op.alter_column("rules", "condition", existing_type=sa.String(length=20), nullable=False)
    op.alter_column("rules", "property", existing_type=sa.String(length=100), nullable=False)

    if "triggered_once" in existing_cols:
        op.drop_column("rules", "triggered_once")
    if "time_condition" in existing_cols:
        op.drop_column("rules", "time_condition")
    if "timezone" in existing_cols:
        op.drop_column("rules", "timezone")
    if "time_window_end" in existing_cols:
        op.drop_column("rules", "time_window_end")
    if "time_window_start" in existing_cols:
        op.drop_column("rules", "time_window_start")
    if "cooldown_mode" in existing_cols:
        op.drop_column("rules", "cooldown_mode")
    if "rule_type" in existing_cols:
        op.drop_column("rules", "rule_type")
