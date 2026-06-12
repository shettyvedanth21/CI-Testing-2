"""Hard cut rule-engine tenant columns to SH tenant IDs.

Revision ID: 20260411_0008_sh_tenant_id_hard_cut
Revises: 20260406_0007_add_rule_notification_recipients
Create Date: 2026-04-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260411_0008_sh_tenant_id_hard_cut"
down_revision: Union[str, None] = "20260406_0007_add_rule_notification_recipients"
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


def _require_reset_for_incompatible_values(bind, table_name: str) -> None:
    rows = bind.execute(sa.text(f"SELECT tenant_id FROM {table_name} WHERE tenant_id IS NOT NULL")).scalars()
    invalid_values = [value for value in rows if not _is_valid_tenant_id(str(value))]
    if invalid_values:
        sample = ", ".join(repr(str(value)) for value in invalid_values[:3])
        raise RuntimeError(
            "Rule-engine SH tenant hard cut requires a downstream reset before migration. "
            f"Found incompatible values in {table_name}.tenant_id: {sample}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name in ("rules", "alerts", "activity_events"):
        if table_name not in inspector.get_table_names():
            continue
        _require_reset_for_incompatible_values(bind, table_name)
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "tenant_id",
                existing_type=sa.String(length=50),
                type_=sa.String(length=TENANT_ID_LENGTH),
                nullable=True,
            )


def downgrade() -> None:
    raise NotImplementedError("Downgrade is not supported after the SH tenant_id hard cut.")
