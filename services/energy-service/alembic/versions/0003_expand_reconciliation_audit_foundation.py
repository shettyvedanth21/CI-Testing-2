"""Expand reconciliation audit table for approval-safe historical correction.

Revision ID: 0003_expand_reconciliation_audit_foundation
Revises: 0002_add_baseline_shadow_columns
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_expand_reconciliation_audit_foundation"
down_revision = "0002_add_baseline_shadow_columns"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    columns = [
        ("run_id", sa.Column("run_id", sa.String(length=64), nullable=True)),
        ("tenant_id", sa.Column("tenant_id", sa.String(length=64), nullable=True)),
        ("period_type", sa.Column("period_type", sa.String(length=32), nullable=True)),
        ("period_start", sa.Column("period_start", sa.DateTime(timezone=True), nullable=True)),
        ("period_end", sa.Column("period_end", sa.DateTime(timezone=True), nullable=True)),
        ("old_metrics", sa.Column("old_metrics", sa.JSON(), nullable=True)),
        ("new_metrics", sa.Column("new_metrics", sa.JSON(), nullable=True)),
        ("old_quality_flags", sa.Column("old_quality_flags", sa.JSON(), nullable=True)),
        ("new_quality_flags", sa.Column("new_quality_flags", sa.JSON(), nullable=True)),
        ("algorithm_version", sa.Column("algorithm_version", sa.String(length=64), nullable=True)),
        ("normalization_version", sa.Column("normalization_version", sa.String(length=64), nullable=True)),
        ("source_window_start", sa.Column("source_window_start", sa.DateTime(timezone=True), nullable=True)),
        ("source_window_end", sa.Column("source_window_end", sa.DateTime(timezone=True), nullable=True)),
        ("status", sa.Column("status", sa.String(length=32), nullable=False, server_default="detected")),
        ("approved_by", sa.Column("approved_by", sa.String(length=100), nullable=True)),
        ("approved_at", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)),
    ]
    for column_name, column in columns:
        if not _has_column("energy_reconcile_audit", column_name):
            op.add_column("energy_reconcile_audit", column)

    indexes = [
        ("ix_energy_reconcile_audit_run_id", ["run_id"]),
        ("ix_energy_reconcile_audit_tenant_id", ["tenant_id"]),
        ("ix_energy_reconcile_audit_status", ["status"]),
        ("ix_energy_reconcile_audit_period_type", ["period_type"]),
    ]
    for index_name, columns in indexes:
        if not _has_index("energy_reconcile_audit", index_name):
            op.create_index(index_name, "energy_reconcile_audit", columns)


def downgrade() -> None:
    op.drop_index("ix_energy_reconcile_audit_period_type", table_name="energy_reconcile_audit")
    op.drop_index("ix_energy_reconcile_audit_status", table_name="energy_reconcile_audit")
    op.drop_index("ix_energy_reconcile_audit_tenant_id", table_name="energy_reconcile_audit")
    op.drop_index("ix_energy_reconcile_audit_run_id", table_name="energy_reconcile_audit")
    op.drop_column("energy_reconcile_audit", "approved_at")
    op.drop_column("energy_reconcile_audit", "approved_by")
    op.drop_column("energy_reconcile_audit", "status")
    op.drop_column("energy_reconcile_audit", "source_window_end")
    op.drop_column("energy_reconcile_audit", "source_window_start")
    op.drop_column("energy_reconcile_audit", "normalization_version")
    op.drop_column("energy_reconcile_audit", "algorithm_version")
    op.drop_column("energy_reconcile_audit", "new_quality_flags")
    op.drop_column("energy_reconcile_audit", "old_quality_flags")
    op.drop_column("energy_reconcile_audit", "new_metrics")
    op.drop_column("energy_reconcile_audit", "old_metrics")
    op.drop_column("energy_reconcile_audit", "period_end")
    op.drop_column("energy_reconcile_audit", "period_start")
    op.drop_column("energy_reconcile_audit", "period_type")
    op.drop_column("energy_reconcile_audit", "tenant_id")
    op.drop_column("energy_reconcile_audit", "run_id")
