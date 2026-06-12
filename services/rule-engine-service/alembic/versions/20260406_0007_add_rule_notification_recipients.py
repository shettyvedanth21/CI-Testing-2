"""Add structured per-rule notification recipients.

Revision ID: 20260406_0007_add_rule_notification_recipients
Revises: 20260324_0006_backfill_legacy_tenant_ids
Create Date: 2026-04-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260406_0007_add_rule_notification_recipients"
down_revision: Union[str, None] = "20260324_0006_backfill_legacy_tenant_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("rules")}

    if "notification_recipients" not in existing_cols:
        op.add_column("rules", sa.Column("notification_recipients", sa.JSON(), nullable=True))

    bind.execute(
        sa.text(
            """
            UPDATE rules
            SET notification_recipients = '[]'
            WHERE notification_recipients IS NULL
            """
        )
    )

    op.alter_column(
        "rules",
        "notification_recipients",
        existing_type=sa.JSON(),
        nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("rules")}

    if "notification_recipients" in existing_cols:
        op.drop_column("rules", "notification_recipients")
