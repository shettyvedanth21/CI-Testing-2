"""Add auth action token cleanup indexes.

Revision ID: 0010_action_token_cleanup
Revises: 0009_user_lifecycle_timestamps
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_action_token_cleanup"
down_revision = "0009_user_lifecycle_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("auth_action_tokens")}

    if "ix_auth_action_tokens_expires_at" not in existing_indexes:
        op.create_index(
            "ix_auth_action_tokens_expires_at",
            "auth_action_tokens",
            ["expires_at"],
            unique=False,
        )
    if "ix_auth_action_tokens_used_at" not in existing_indexes:
        op.create_index(
            "ix_auth_action_tokens_used_at",
            "auth_action_tokens",
            ["used_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("auth_action_tokens")}

    if "ix_auth_action_tokens_used_at" in existing_indexes:
        op.drop_index("ix_auth_action_tokens_used_at", table_name="auth_action_tokens")
    if "ix_auth_action_tokens_expires_at" in existing_indexes:
        op.drop_index("ix_auth_action_tokens_expires_at", table_name="auth_action_tokens")
