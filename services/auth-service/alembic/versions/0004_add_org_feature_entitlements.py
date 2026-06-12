"""Add organization feature entitlements

Revision ID: 0004_org_feature_entitlements
Revises: 0003_add_auth_action_tokens
Create Date: 2026-04-01
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0004_org_feature_entitlements"
down_revision = "0003_auth_action_tokens"
branch_labels = None
depends_on = None


DEFAULT_ROLE_MATRIX = {
    "plant_manager": [],
    "operator": [],
    "viewer": [],
}


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {column["name"] for column in inspector.get_columns("organizations")}

    if "entitlements_version" not in existing_columns:
        op.add_column("organizations", sa.Column("entitlements_version", sa.Integer(), nullable=True))
    if "premium_feature_grants_json" not in existing_columns:
        op.add_column("organizations", sa.Column("premium_feature_grants_json", sa.JSON(), nullable=True))
    if "role_feature_matrix_json" not in existing_columns:
        op.add_column("organizations", sa.Column("role_feature_matrix_json", sa.JSON(), nullable=True))

    connection.execute(
        sa.text(
            """
            UPDATE organizations
            SET
                entitlements_version = COALESCE(entitlements_version, 0),
                premium_feature_grants_json = COALESCE(premium_feature_grants_json, :premium_feature_grants_json),
                role_feature_matrix_json = COALESCE(role_feature_matrix_json, :role_feature_matrix_json)
            """
        ),
        {
            "premium_feature_grants_json": _json([]),
            "role_feature_matrix_json": _json(DEFAULT_ROLE_MATRIX),
        },
    )

    op.alter_column(
        "organizations",
        "entitlements_version",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "organizations",
        "premium_feature_grants_json",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.alter_column(
        "organizations",
        "role_feature_matrix_json",
        existing_type=sa.JSON(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("organizations", "role_feature_matrix_json")
    op.drop_column("organizations", "premium_feature_grants_json")
    op.drop_column("organizations", "entitlements_version")
