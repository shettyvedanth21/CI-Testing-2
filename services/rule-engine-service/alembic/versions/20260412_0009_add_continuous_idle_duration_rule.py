"""Add continuous idle duration rule support.

Revision ID: 20260412_0009
Revises: 20260411_0008_sh_tenant_id_hard_cut
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260412_0009"
down_revision = "20260411_0008_sh_tenant_id_hard_cut"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("duration_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("rules", "duration_minutes")
