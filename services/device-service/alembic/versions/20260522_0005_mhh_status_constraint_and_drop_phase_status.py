"""Add status CHECK constraint to MachineHealthHistory and drop dead phase_status column.

Adds:
- Data sanitization for MHH rows with invalid status values
- ck_mhh_status_values: same allowed set as MachineHealthLatest
- Drop machine_health_latest.phase_status (dead column, never written)

Revision ID: 20260522_0005_mhh_status_constraint_and_drop_phase_status
Revises: 20260522_0004_mh_constraint_and_columns
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260522_0005_mhh_status_constraint_and_drop_phase_status"
down_revision = "20260522_0004_mh_constraint_and_columns"
branch_labels = None
depends_on = None

_ALLOWED_STATUSES = (
    "healthy",
    "watch",
    "warning",
    "critical",
    "learning",
    "insufficient_signals",
    "unavailable",
)
_ALLOWED_SET_SQL = ",".join(f"'{s}'" for s in _ALLOWED_STATUSES)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_health_history" in existing_tables:
        op.execute(
            f"UPDATE machine_health_history SET status='unavailable' "
            f"WHERE status IS NOT NULL AND status NOT IN ({_ALLOWED_SET_SQL})"
        )
        existing_constraints = {
            c["name"] for c in inspector.get_check_constraints("machine_health_history")
        }
        if "ck_mhh_status_values" in existing_constraints:
            op.drop_constraint("ck_mhh_status_values", "machine_health_history", type_="check")
        op.create_check_constraint(
            "ck_mhh_status_values",
            "machine_health_history",
            f"status IN ({_ALLOWED_SET_SQL})",
        )

    if "machine_health_latest" in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns("machine_health_latest")}
        if "phase_status" in existing_columns:
            op.drop_column("machine_health_latest", "phase_status")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_health_latest" in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns("machine_health_latest")}
        if "phase_status" not in existing_columns:
            op.add_column(
                "machine_health_latest",
                sa.Column("phase_status", sa.String(32), nullable=True),
            )

    if "machine_health_history" in existing_tables:
        op.execute(
            "UPDATE machine_health_history SET status='learning' WHERE status='insufficient_signals'"
        )
        existing_constraints = {
            c["name"] for c in inspector.get_check_constraints("machine_health_history")
        }
        if "ck_mhh_status_values" in existing_constraints:
            op.drop_constraint("ck_mhh_status_values", "machine_health_history", type_="check")
