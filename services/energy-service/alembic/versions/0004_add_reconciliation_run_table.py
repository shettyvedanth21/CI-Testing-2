"""Add reconciliation preview run tracking table.

Revision ID: 0004_add_reconciliation_run_table
Revises: 0003_expand_reconciliation_audit_foundation
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_add_reconciliation_run_table"
down_revision = "0003_expand_reconciliation_audit_foundation"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("energy_reconcile_run"):
        op.create_table(
            "energy_reconcile_run",
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
            sa.Column("requested_start", sa.Date(), nullable=False),
            sa.Column("requested_end", sa.Date(), nullable=False),
            sa.Column("requested_by", sa.String(length=100), nullable=True),
            sa.Column("scope_filters", sa.JSON(), nullable=True),
            sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("suspicious_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("run_id"),
        )
    if not _has_index("energy_reconcile_run", "ix_energy_reconcile_run_tenant_id"):
        op.create_index("ix_energy_reconcile_run_tenant_id", "energy_reconcile_run", ["tenant_id"])
    if not _has_index("energy_reconcile_run", "ix_energy_reconcile_run_status"):
        op.create_index("ix_energy_reconcile_run_status", "energy_reconcile_run", ["status"])


def downgrade() -> None:
    op.drop_index("ix_energy_reconcile_run_status", table_name="energy_reconcile_run")
    op.drop_index("ix_energy_reconcile_run_tenant_id", table_name="energy_reconcile_run")
    op.drop_table("energy_reconcile_run")
