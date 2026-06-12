"""Add refresh token cleanup indexes

Revision ID: 0006_refresh_token_indexes
Revises: 0005_fix_org_feat_json
Create Date: 2026-04-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_refresh_token_indexes"
down_revision = "0005_fix_org_feat_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_indexes = {index["name"] for index in inspector.get_indexes("refresh_tokens")}

    if "ix_refresh_tokens_expires_at" not in existing_indexes:
        op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
    if "ix_refresh_tokens_revoked_at" not in existing_indexes:
        op.create_index("ix_refresh_tokens_revoked_at", "refresh_tokens", ["revoked_at"])


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_indexes = {index["name"] for index in inspector.get_indexes("refresh_tokens")}

    if "ix_refresh_tokens_revoked_at" in existing_indexes:
        op.drop_index("ix_refresh_tokens_revoked_at", table_name="refresh_tokens")
    if "ix_refresh_tokens_expires_at" in existing_indexes:
        op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
