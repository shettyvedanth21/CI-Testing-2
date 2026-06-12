"""Add report revision metadata and tariff version history foundation.

Revision ID: 010_report_revision_tariff_versions
Revises: 009_add_report_worker_claim_fields
Create Date: 2026-04-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "010_report_revision_tariff_versions"
down_revision: Union[str, None] = "009_add_report_worker_claim_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("energy_reports", sa.Column("root_report_id", sa.String(length=36), nullable=True))
    op.add_column("energy_reports", sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("energy_reports", sa.Column("supersedes_report_id", sa.String(length=36), nullable=True))
    op.add_column("energy_reports", sa.Column("superseded_by_report_id", sa.String(length=36), nullable=True))
    op.add_column("energy_reports", sa.Column("is_authoritative", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("energy_reports", sa.Column("revision_reason", sa.Text(), nullable=True))
    op.add_column("energy_reports", sa.Column("generated_from_reconciliation_run_id", sa.String(length=64), nullable=True))
    op.add_column("energy_reports", sa.Column("tariff_version_id", sa.Integer(), nullable=True))
    op.create_index("ix_energy_reports_root_report_id", "energy_reports", ["root_report_id"])
    op.create_index(
        "ix_energy_reports_authoritative",
        "energy_reports",
        ["tenant_id", "root_report_id", "is_authoritative"],
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE energy_reports
            SET root_report_id = report_id
            WHERE root_report_id IS NULL
            """
        )
    )

    op.create_table(
        "tenant_tariff_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("effective_start_at", sa.DateTime(), nullable=False),
        sa.Column("effective_end_at", sa.DateTime(), nullable=True),
        sa.Column("energy_rate_per_kwh", sa.Float(), nullable=False),
        sa.Column("demand_charge_per_kw", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reactive_penalty_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fixed_monthly_charge", sa.Float(), nullable=False, server_default="0"),
        sa.Column("power_factor_threshold", sa.Float(), nullable=False, server_default="0.90"),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("change_reason", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_by_version_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "version_number", name="uq_tenant_tariff_versions_tenant_version"),
    )
    op.create_index("ix_tenant_tariff_versions_tenant_id", "tenant_tariff_versions", ["tenant_id"])
    op.create_index(
        "ix_tenant_tariff_versions_effective_window",
        "tenant_tariff_versions",
        ["tenant_id", "effective_start_at", "effective_end_at"],
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO tenant_tariff_versions (
                tenant_id,
                version_number,
                effective_start_at,
                effective_end_at,
                energy_rate_per_kwh,
                demand_charge_per_kw,
                reactive_penalty_rate,
                fixed_monthly_charge,
                power_factor_threshold,
                currency,
                change_reason,
                created_by,
                created_at,
                superseded_by_version_id
            )
            SELECT
                tenant_id,
                1,
                COALESCE(created_at, updated_at, CURRENT_TIMESTAMP),
                NULL,
                energy_rate_per_kwh,
                demand_charge_per_kw,
                reactive_penalty_rate,
                fixed_monthly_charge,
                power_factor_threshold,
                currency,
                'bootstrap_current_tariff',
                NULL,
                COALESCE(created_at, updated_at, CURRENT_TIMESTAMP),
                NULL
            FROM tenant_tariffs
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_tariff_versions_effective_window", table_name="tenant_tariff_versions")
    op.drop_index("ix_tenant_tariff_versions_tenant_id", table_name="tenant_tariff_versions")
    op.drop_table("tenant_tariff_versions")

    op.drop_index("ix_energy_reports_authoritative", table_name="energy_reports")
    op.drop_index("ix_energy_reports_root_report_id", table_name="energy_reports")
    op.drop_column("energy_reports", "tariff_version_id")
    op.drop_column("energy_reports", "generated_from_reconciliation_run_id")
    op.drop_column("energy_reports", "revision_reason")
    op.drop_column("energy_reports", "is_authoritative")
    op.drop_column("energy_reports", "superseded_by_report_id")
    op.drop_column("energy_reports", "supersedes_report_id")
    op.drop_column("energy_reports", "revision_number")
    op.drop_column("energy_reports", "root_report_id")
