"""Repair organization feature entitlement JSON values

Revision ID: 0005_fix_org_feat_json
Revises: 0004_org_feature_entitlements
Create Date: 2026-04-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_fix_org_feat_json"
down_revision = "0004_org_feature_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {column["name"] for column in inspector.get_columns("organizations")}
    required_columns = {
        "entitlements_version",
        "premium_feature_grants_json",
        "role_feature_matrix_json",
    }
    if not required_columns.issubset(existing_columns):
        return

    connection.execute(
        sa.text(
            """
            UPDATE organizations
            SET
                entitlements_version = COALESCE(entitlements_version, 0),
                premium_feature_grants_json = CASE
                    WHEN premium_feature_grants_json IS NULL THEN JSON_ARRAY()
                    WHEN JSON_TYPE(premium_feature_grants_json) = 'STRING'
                        THEN CAST(JSON_UNQUOTE(premium_feature_grants_json) AS JSON)
                    ELSE premium_feature_grants_json
                END,
                role_feature_matrix_json = CASE
                    WHEN role_feature_matrix_json IS NULL THEN JSON_OBJECT(
                        'plant_manager', JSON_ARRAY(),
                        'operator', JSON_ARRAY(),
                        'viewer', JSON_ARRAY()
                    )
                    WHEN JSON_TYPE(role_feature_matrix_json) = 'STRING'
                        THEN CAST(JSON_UNQUOTE(role_feature_matrix_json) AS JSON)
                    ELSE role_feature_matrix_json
                END
            """
        )
    )


def downgrade() -> None:
    # No downgrade transformation needed; the values remain valid JSON.
    return
