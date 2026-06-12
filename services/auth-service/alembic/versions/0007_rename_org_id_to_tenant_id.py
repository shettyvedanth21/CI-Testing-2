"""Rename auth tenant ownership columns to tenant_id

Revision ID: 0007_tenant_id_columns
Revises: 0006_refresh_token_indexes
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_tenant_id_columns"
down_revision = "0006_refresh_token_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plants") as batch_op:
        batch_op.drop_constraint("plants_ibfk_1", type_="foreignkey")
        batch_op.alter_column("org_id", new_column_name="tenant_id", existing_type=sa.String(length=36), existing_nullable=False)
        batch_op.create_index("ix_plants_tenant_id", ["tenant_id"])
        batch_op.create_foreign_key("plants_ibfk_1", "organizations", ["tenant_id"], ["id"], ondelete="CASCADE")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("users_ibfk_1", type_="foreignkey")
        batch_op.alter_column("org_id", new_column_name="tenant_id", existing_type=sa.String(length=36), existing_nullable=True)
        batch_op.create_index("ix_users_tenant_id", ["tenant_id"])
        batch_op.create_foreign_key("users_ibfk_1", "organizations", ["tenant_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    raise NotImplementedError("Downgrade is not supported after the tenant_id hard cutover.")
