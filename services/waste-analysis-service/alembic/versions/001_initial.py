"""initial waste analysis tables

Revision ID: 001_initial
Revises:
Create Date: 2026-03-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "waste_analysis_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_name", sa.String(length=255), nullable=True),
        sa.Column("scope", sa.Enum("all", "selected", name="wastescope"), nullable=False),
        sa.Column("device_ids", sa.JSON(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("granularity", sa.Enum("daily", "weekly", "monthly", name="wastegranularity"), nullable=False),
        sa.Column("status", sa.Enum("pending", "running", "completed", "failed", name="wastestatus"), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=255), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("s3_key", sa.String(length=500), nullable=True),
        sa.Column("download_url", sa.String(length=500), nullable=True),
        sa.Column("tariff_rate_used", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "waste_device_summary",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=100), nullable=False),
        sa.Column("device_name", sa.String(length=255), nullable=True),
        sa.Column("data_source_type", sa.String(length=20), nullable=True),
        sa.Column("idle_duration_sec", sa.Integer(), nullable=False),
        sa.Column("idle_energy_kwh", sa.Float(), nullable=False),
        sa.Column("idle_cost", sa.Float(), nullable=True),
        sa.Column("standby_power_kw", sa.Float(), nullable=True),
        sa.Column("standby_energy_kwh", sa.Float(), nullable=True),
        sa.Column("standby_cost", sa.Float(), nullable=True),
        sa.Column("total_energy_kwh", sa.Float(), nullable=False),
        sa.Column("total_cost", sa.Float(), nullable=True),
        sa.Column("offhours_energy_kwh", sa.Float(), nullable=True),
        sa.Column("offhours_cost", sa.Float(), nullable=True),
        sa.Column("data_quality", sa.String(length=20), nullable=True),
        sa.Column("pf_estimated", sa.Boolean(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("calculation_method", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "device_id", name="uq_waste_job_device"),
    )

    op.create_index("idx_waste_job_device", "waste_device_summary", ["job_id", "device_id"])


def downgrade() -> None:
    op.drop_index("idx_waste_job_device", table_name="waste_device_summary")
    op.drop_table("waste_device_summary")
    op.drop_table("waste_analysis_jobs")
    op.execute("DROP TYPE IF EXISTS wastescope")
    op.execute("DROP TYPE IF EXISTS wastegranularity")
    op.execute("DROP TYPE IF EXISTS wastestatus")
