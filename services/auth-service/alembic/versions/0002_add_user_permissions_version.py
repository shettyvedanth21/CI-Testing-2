"""Add permissions version to users

Revision ID: 0002_perm_version
Revises: 0001_auth_schema
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_perm_version"
down_revision = "0001_auth_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "permissions_version" not in columns:
        op.add_column(
            "users",
            sa.Column("permissions_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    op.alter_column("users", "permissions_version", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "permissions_version")
