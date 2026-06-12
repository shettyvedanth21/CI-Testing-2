"""Add unit-aware cooldown fields to rules.

Revision ID: 005_add_rule_cooldown_units
Revises: 004_add_rule_alert_indexes
Create Date: 2026-03-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005_add_rule_cooldown_units"
down_revision: Union[str, None] = "004_add_rule_alert_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("rules")}

    if "cooldown_unit" not in existing_cols:
        op.add_column("rules", sa.Column("cooldown_unit", sa.String(length=20), nullable=True))
    if "cooldown_seconds" not in existing_cols:
        op.add_column("rules", sa.Column("cooldown_seconds", sa.Integer(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE rules
            SET
                cooldown_unit = COALESCE(cooldown_unit, 'minutes'),
                cooldown_seconds = CASE
                    WHEN cooldown_mode = 'no_repeat' THEN 0
                    ELSE COALESCE(cooldown_seconds, cooldown_minutes * 60)
                END
            """
        )
    )

    op.alter_column(
        "rules",
        "cooldown_unit",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="minutes",
    )
    op.alter_column(
        "rules",
        "cooldown_seconds",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="900",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("rules")}

    if "cooldown_seconds" in existing_cols:
        op.drop_column("rules", "cooldown_seconds")
    if "cooldown_unit" in existing_cols:
        op.drop_column("rules", "cooldown_unit")
