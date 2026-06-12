"""add quality gate fields

Revision ID: 002_quality_gate_fields
Revises: 001_initial
Create Date: 2026-03-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002_quality_gate_fields"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    job_cols = {c["name"] for c in inspector.get_columns("waste_analysis_jobs")}
    if "error_code" not in job_cols:
        op.add_column("waste_analysis_jobs", sa.Column("error_code", sa.String(length=64), nullable=True))

    summary_cols = {c["name"] for c in inspector.get_columns("waste_device_summary")}
    if "energy_quality" not in summary_cols:
        op.add_column("waste_device_summary", sa.Column("energy_quality", sa.String(length=20), nullable=True))
    if "idle_quality" not in summary_cols:
        op.add_column("waste_device_summary", sa.Column("idle_quality", sa.String(length=20), nullable=True))
    if "standby_quality" not in summary_cols:
        op.add_column("waste_device_summary", sa.Column("standby_quality", sa.String(length=20), nullable=True))
    if "overall_quality" not in summary_cols:
        op.add_column("waste_device_summary", sa.Column("overall_quality", sa.String(length=20), nullable=True))
    if "idle_status" not in summary_cols:
        op.add_column("waste_device_summary", sa.Column("idle_status", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("waste_device_summary", "idle_status")
    op.drop_column("waste_device_summary", "overall_quality")
    op.drop_column("waste_device_summary", "standby_quality")
    op.drop_column("waste_device_summary", "idle_quality")
    op.drop_column("waste_device_summary", "energy_quality")
    op.drop_column("waste_analysis_jobs", "error_code")
