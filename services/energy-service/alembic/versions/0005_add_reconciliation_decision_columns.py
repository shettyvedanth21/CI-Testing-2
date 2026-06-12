"""Add decision and apply metadata columns to reconciliation audit.

Revision ID: 0005_add_reconciliation_decision_columns
Revises: 0004_add_reconciliation_run_table
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_add_reconciliation_decision_columns"
down_revision = "0004_add_reconciliation_run_table"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    columns = [
        ("rejected_by", sa.Column("rejected_by", sa.String(length=100), nullable=True)),
        ("rejected_at", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True)),
        ("rejection_reason", sa.Column("rejection_reason", sa.Text(), nullable=True)),
        ("applied_by", sa.Column("applied_by", sa.String(length=100), nullable=True)),
        ("applied_at", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True)),
    ]
    for column_name, column in columns:
        if not _has_column("energy_reconcile_audit", column_name):
            op.add_column("energy_reconcile_audit", column)


def downgrade() -> None:
    op.drop_column("energy_reconcile_audit", "applied_at")
    op.drop_column("energy_reconcile_audit", "applied_by")
    op.drop_column("energy_reconcile_audit", "rejection_reason")
    op.drop_column("energy_reconcile_audit", "rejected_at")
    op.drop_column("energy_reconcile_audit", "rejected_by")
