"""Add auth action tokens

Revision ID: 0003_auth_action_tokens
Revises: 0002_perm_version
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_auth_action_tokens"
down_revision = "0002_perm_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_action_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "action_type",
            sa.Enum(
                "invite_set_password",
                "password_reset",
                name="authactiontype",
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_role", sa.String(length=50), nullable=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("metadata_json", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_auth_action_tokens_token_hash"),
    )
    op.create_index("ix_auth_action_tokens_user_id", "auth_action_tokens", ["user_id"])
    op.create_index("ix_auth_action_tokens_token_hash", "auth_action_tokens", ["token_hash"])
    op.create_index("ix_auth_action_tokens_tenant_id", "auth_action_tokens", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_action_tokens_tenant_id", table_name="auth_action_tokens")
    op.drop_index("ix_auth_action_tokens_token_hash", table_name="auth_action_tokens")
    op.drop_index("ix_auth_action_tokens_user_id", table_name="auth_action_tokens")
    op.drop_table("auth_action_tokens")
    sa.Enum(name="authactiontype").drop(op.get_bind(), checkfirst=True)
