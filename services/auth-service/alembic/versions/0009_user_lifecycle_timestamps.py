"""Add explicit lifecycle timestamps for invite/deactivate/reactivate flows.

Revision ID: 0009_user_lifecycle_timestamps
Revises: 0008_sh_tenant_ids
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_user_lifecycle_timestamps"
down_revision = "0008_sh_tenant_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("deactivated_at")
        batch_op.drop_column("activated_at")
        batch_op.drop_column("invited_at")
