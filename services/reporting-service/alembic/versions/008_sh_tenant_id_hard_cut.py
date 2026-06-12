"""Hard cut reporting-service tenant columns to SH tenant IDs.

Revision ID: 008_sh_tenant_id_hard_cut
Revises: 007_add_schedule_processing_claim
Create Date: 2026-04-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "008_sh_tenant_id_hard_cut"
down_revision: Union[str, None] = "007_add_schedule_processing_claim"
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


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def _require_reset_for_incompatible_values(bind, table_name: str, column_name: str) -> None:
    if not _table_exists(bind, table_name):
        return
    rows = bind.execute(sa.text(f"SELECT {column_name} FROM {table_name} WHERE {column_name} IS NOT NULL")).scalars()
    invalid_values = [value for value in rows if not _is_valid_tenant_id(str(value))]
    if invalid_values:
        sample = ", ".join(repr(str(value)) for value in invalid_values[:3])
        raise RuntimeError(
            "Reporting-service SH tenant hard cut requires a downstream reset before migration. "
            f"Found incompatible values in {table_name}.{column_name}: {sample}"
        )


def _alter_tenant_column(table_name: str) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.alter_column(
            "tenant_id",
            existing_type=sa.String(length=50),
            type_=sa.String(length=TENANT_ID_LENGTH),
            nullable=False,
        )


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "energy_reports",
        "scheduled_reports",
        "tenant_tariffs",
        "tariff_config",
        "notification_channels",
    ):
        _require_reset_for_incompatible_values(bind, table_name, "tenant_id")
        if _table_exists(bind, table_name):
            _alter_tenant_column(table_name)


def downgrade() -> None:
    raise NotImplementedError("Downgrade is not supported after the SH tenant_id hard cut.")
