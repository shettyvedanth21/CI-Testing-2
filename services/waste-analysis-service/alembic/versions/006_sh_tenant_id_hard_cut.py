"""Hard cut waste-analysis tenant columns to SH tenant IDs.

Revision ID: 006_sh_tenant_id_hard_cut
Revises: 005_add_tenant_scope_to_waste_jobs
Create Date: 2026-04-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "006_sh_tenant_id_hard_cut"
down_revision: Union[str, None] = "005_add_tenant_scope_to_waste_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_ID_LENGTH = 10
TENANT_ID_PREFIX = "SH"


def _is_valid_tenant_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == TENANT_ID_LENGTH
        and value.startswith(TENANT_ID_PREFIX)
        and value[2:].isdigit()
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "waste_analysis_jobs" not in inspector.get_table_names():
        return

    rows = bind.execute(
        sa.text("SELECT tenant_id FROM waste_analysis_jobs WHERE tenant_id IS NOT NULL")
    ).scalars()
    invalid_values = [value for value in rows if not _is_valid_tenant_id(str(value))]
    if invalid_values:
        sample = ", ".join(repr(str(value)) for value in invalid_values[:3])
        raise RuntimeError(
            "Waste-analysis SH tenant hard cut requires a downstream reset before migration. "
            f"Found incompatible values in waste_analysis_jobs.tenant_id: {sample}"
        )

    with op.batch_alter_table("waste_analysis_jobs") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            existing_type=sa.String(length=50),
            type_=sa.String(length=TENANT_ID_LENGTH),
            nullable=False,
        )


def downgrade() -> None:
    raise NotImplementedError("Downgrade is not supported after the SH tenant_id hard cut.")
