"""Unify tariff source of truth on tenant_tariffs.

Revision ID: 006_unify_tariff_source_of_truth
Revises: 005_tenant_scope_reporting_settings
Create Date: 2026-04-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_unify_tariff_source_of_truth"
down_revision: Union[str, None] = "005_tenant_scope_reporting_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    legacy_rows = bind.execute(
        sa.text(
            """
            SELECT tenant_id, rate, currency
            FROM tariff_config
            WHERE tenant_id IS NOT NULL
            """
        )
    ).mappings().all()

    for row in legacy_rows:
        exists = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM tenant_tariffs
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": row["tenant_id"]},
        ).first()
        if exists:
            continue

        bind.execute(
            sa.text(
                """
                INSERT INTO tenant_tariffs (
                    tenant_id,
                    energy_rate_per_kwh,
                    demand_charge_per_kw,
                    reactive_penalty_rate,
                    fixed_monthly_charge,
                    power_factor_threshold,
                    currency,
                    created_at,
                    updated_at
                ) VALUES (
                    :tenant_id,
                    :energy_rate_per_kwh,
                    0,
                    0,
                    0,
                    0.90,
                    :currency,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "tenant_id": row["tenant_id"],
                "energy_rate_per_kwh": row["rate"],
                "currency": row["currency"],
            },
        )

    bind.execute(sa.text("DELETE FROM tariff_config"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO tariff_config (tenant_id, rate, currency, updated_at, updated_by)
            SELECT tenant_id, energy_rate_per_kwh, currency, updated_at, NULL
            FROM tenant_tariffs
            """
        )
    )
