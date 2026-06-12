"""add report history tenant-created index

Revision ID: 011_add_report_history_index
Revises: 010_report_revision_tariff_versions
Create Date: 2026-04-22 13:25:00
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "011_add_report_history_index"
down_revision = "010_report_revision_tariff_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_energy_reports_tenant_created",
        "energy_reports",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_energy_reports_tenant_created", table_name="energy_reports")
