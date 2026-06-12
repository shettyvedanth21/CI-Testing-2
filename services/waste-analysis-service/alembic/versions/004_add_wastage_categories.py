"""Add off-hours, overconsumption, and unoccupied wastage columns.

Revision ID: 004_add_wastage_categories
Revises: 003_add_waste_job_indexes
Create Date: 2026-03-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004_add_wastage_categories"
down_revision: Union[str, None] = "003_add_waste_job_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("waste_device_summary")}

    additions = [
        ("offhours_duration_sec", sa.Integer()),
        ("offhours_skipped_reason", sa.String(length=100)),
        ("offhours_pf_estimated", sa.Boolean(), sa.text("0")),
        ("overconsumption_duration_sec", sa.Integer()),
        ("overconsumption_kwh", sa.Float()),
        ("overconsumption_cost", sa.Float()),
        ("overconsumption_skipped_reason", sa.String(length=100)),
        ("overconsumption_pf_estimated", sa.Boolean(), sa.text("0")),
        ("unoccupied_duration_sec", sa.Integer()),
        ("unoccupied_energy_kwh", sa.Float()),
        ("unoccupied_cost", sa.Float()),
        ("unoccupied_skipped_reason", sa.String(length=100)),
        ("unoccupied_pf_estimated", sa.Boolean(), sa.text("0")),
    ]

    for item in additions:
        name = item[0]
        if name in cols:
            continue
        if len(item) == 3:
            op.add_column(
                "waste_device_summary",
                sa.Column(name, item[1], nullable=False, server_default=item[2]),
            )
            op.alter_column("waste_device_summary", name, server_default=None)
        else:
            op.add_column("waste_device_summary", sa.Column(name, item[1], nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("waste_device_summary")}

    for name in [
        "unoccupied_pf_estimated",
        "unoccupied_skipped_reason",
        "unoccupied_cost",
        "unoccupied_energy_kwh",
        "unoccupied_duration_sec",
        "overconsumption_pf_estimated",
        "overconsumption_skipped_reason",
        "overconsumption_cost",
        "overconsumption_kwh",
        "overconsumption_duration_sec",
        "offhours_pf_estimated",
        "offhours_skipped_reason",
        "offhours_duration_sec",
    ]:
        if name in cols:
            op.drop_column("waste_device_summary", name)
