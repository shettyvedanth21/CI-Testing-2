"""Deduplicate exact duplicate device shifts.

Revision ID: shft_ovlp_dedup_v1
Revises: add_dash_widget_cfg_state
Create Date: 2026-03-11
"""

from collections import defaultdict

from alembic import op
import sqlalchemy as sa


revision = "shft_ovlp_dedup_v1"
down_revision = "add_dash_widget_cfg_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device_shifts" not in inspector.get_table_names():
        print("[shift-dedup] device_shifts table missing; skipping")
        return

    rows = bind.execute(
        sa.text(
            """
            SELECT id, device_id, tenant_id, day_of_week, shift_start, shift_end, created_at
            FROM device_shifts
            ORDER BY device_id, tenant_id, day_of_week, shift_start, shift_end, created_at, id
            """
        )
    ).fetchall()

    groups = defaultdict(list)
    for row in rows:
        key = (row.device_id, row.tenant_id, row.day_of_week, row.shift_start, row.shift_end)
        groups[key].append(row.id)

    to_delete: list[int] = []
    for ids in groups.values():
        if len(ids) > 1:
            # Keep oldest (first due to ORDER BY created_at, id), remove newer.
            to_delete.extend(ids[1:])

    if not to_delete:
        print("[shift-dedup] no exact duplicate shifts found")
        return

    deleted_total = 0
    chunk_size = 500
    for i in range(0, len(to_delete), chunk_size):
        chunk = to_delete[i : i + chunk_size]
        params = {f"id_{idx}": shift_id for idx, shift_id in enumerate(chunk)}
        placeholders = ",".join(f":id_{idx}" for idx in range(len(chunk)))
        result = bind.execute(
            sa.text(f"DELETE FROM device_shifts WHERE id IN ({placeholders})"),
            params,
        )
        deleted_total += result.rowcount or 0

    print(f"[shift-dedup] removed {deleted_total} exact duplicate shift rows")


def downgrade() -> None:
    # Data cleanup migration is intentionally non-reversible.
    pass
