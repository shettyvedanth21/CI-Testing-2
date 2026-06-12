"""Add tenant security audit log table.

Revision ID: 20260331_0003_add_tenant_security_audit_log
Revises: 20260331_0002_enforce_tenant_not_null
Create Date: 2026-03-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260331_0003_add_tenant_security_audit_log"
down_revision = "20260331_0002_enforce_tenant_not_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_security_audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("caller_tenant_id", sa.String(length=50), nullable=True),
        sa.Column("caller_user_id", sa.String(length=100), nullable=True),
        sa.Column("target_tenant_id", sa.String(length=50), nullable=True),
        sa.Column("target_resource_type", sa.String(length=50), nullable=True),
        sa.Column("target_resource_id", sa.String(length=100), nullable=True),
        sa.Column("http_path", sa.String(length=500), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_audit_caller_tenant",
        "tenant_security_audit_log",
        ["caller_tenant_id"],
    )
    op.create_index(
        "ix_audit_created_at",
        "tenant_security_audit_log",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_created_at", table_name="tenant_security_audit_log")
    op.drop_index("ix_audit_caller_tenant", table_name="tenant_security_audit_log")
    op.drop_table("tenant_security_audit_log")
