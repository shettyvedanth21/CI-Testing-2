"""Add tenant_emission_factors table for Scope 2 CO2 widget.

Revision ID: 20260604_0001_tenant_emission_factors
Revises: 20260524_0001_anomaly_count_updated_at
Create Date: 2026-06-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260604_0001_tenant_emission_factors"
down_revision = "20260524_0001_anomaly_count_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "tenant_emission_factors" not in tables:
        op.create_table(
            "tenant_emission_factors",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=24), nullable=False),
            sa.Column("country", sa.String(length=8), nullable=False, server_default="IN"),
            sa.Column("region", sa.String(length=64), nullable=False, server_default="all_india_grid"),
            sa.Column("method", sa.String(length=32), nullable=False, server_default="location_based"),
            sa.Column("factor_value", sa.Numeric(12, 6), nullable=False),
            sa.Column("factor_unit", sa.String(length=32), nullable=False, server_default="kg_co2_per_kwh"),
            sa.Column("source_name", sa.String(length=255), nullable=False),
            sa.Column("source_version", sa.String(length=64), nullable=True),
            sa.Column("factor_year", sa.String(length=32), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "country", "region", "method", "is_active",
                name="uq_tenant_emission_factor_active",
            ),
        )
        op.create_index(
            "ix_tenant_emission_factors_tenant_id",
            "tenant_emission_factors",
            ["tenant_id"],
        )
    else:
        op.alter_column(
            "tenant_emission_factors",
            "tenant_id",
            existing_type=sa.String(length=10),
            type_=sa.String(length=24),
            existing_nullable=False,
        )

    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM tenant_emission_factors "
            "WHERE tenant_id = '__platform_default__'"
        )
    )
    if result.scalar() == 0:
        op.execute(
            sa.text(
                "INSERT INTO tenant_emission_factors "
                "(tenant_id, country, region, method, factor_value, factor_unit, "
                "source_name, source_version, factor_year, is_active, created_at, updated_at) "
                "VALUES ("
                "'__platform_default__', 'IN', 'all_india_grid', 'location_based', "
                "0.716000, 'kg_co2_per_kwh', "
                "'Central Electricity Authority CO2 Baseline Database', "
                "'Version 19.0', 'FY2022-23', 1, "
                "UTC_TIMESTAMP(), UTC_TIMESTAMP()"
                ")"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "tenant_emission_factors" in tables:
        indexes = {x["name"] for x in inspector.get_indexes("tenant_emission_factors")}
        if "ix_tenant_emission_factors_tenant_id" in indexes:
            op.drop_index("ix_tenant_emission_factors_tenant_id", table_name="tenant_emission_factors")
        op.drop_table("tenant_emission_factors")
