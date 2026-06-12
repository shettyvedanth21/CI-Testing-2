"""add first-class tenant columns to energy aggregate tables"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_add_tenant_id_to_energy_aggregates"
down_revision = "0005_add_reconciliation_decision_columns"
branch_labels = None
depends_on = None


TENANT_ID_TYPE = sa.String(length=10)
DEVICE_TABLE = "devices"


def _get_columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _get_indexes(inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _get_unique_constraints(inspector, table_name: str) -> set[str]:
    return {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}


def _ensure_tenant_column(inspector, table_name: str) -> None:
    if "tenant_id" in _get_columns(inspector, table_name):
        return
    op.add_column(table_name, sa.Column("tenant_id", TENANT_ID_TYPE, nullable=True))


def _backfill_tenant_column(table_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name} AS target
            INNER JOIN {DEVICE_TABLE} AS devices
                ON devices.device_id = target.device_id
            SET target.tenant_id = devices.tenant_id
            WHERE target.tenant_id IS NULL
            """
        )
    )


def _orphaned_rows(table_name: str, period_column: str) -> tuple[int, list[dict[str, str]]]:
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*) AS row_count
            FROM {table_name} AS target
            LEFT JOIN {DEVICE_TABLE} AS devices
                ON devices.device_id = target.device_id
            WHERE target.tenant_id IS NULL
              AND devices.device_id IS NULL
            """
        )
    ).scalar_one()
    samples = bind.execute(
        sa.text(
            f"""
            SELECT CAST(target.id AS CHAR) AS id,
                   target.device_id AS device_id,
                   CAST(target.{period_column} AS CHAR) AS bucket
            FROM {table_name} AS target
            LEFT JOIN {DEVICE_TABLE} AS devices
                ON devices.device_id = target.device_id
            WHERE target.tenant_id IS NULL
              AND devices.device_id IS NULL
            ORDER BY id
            LIMIT 10
            """
        )
    ).mappings().all()
    return int(count or 0), [dict(row) for row in samples]


def _assert_no_orphans(table_name: str, period_column: str) -> None:
    orphan_count, samples = _orphaned_rows(table_name, period_column)
    if orphan_count == 0:
        return
    sample_text = ", ".join(
        f"id={row['id']} device_id={row['device_id']} bucket={row['bucket']}"
        for row in samples
    )
    raise RuntimeError(
        f"{table_name} tenant backfill blocked by {orphan_count} orphaned rows with no authoritative device owner. "
        f"Sample rows: {sample_text}"
    )


def _tenant_mismatches(table_name: str, period_column: str) -> tuple[int, list[dict[str, str]]]:
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*) AS row_count
            FROM {table_name} AS target
            INNER JOIN {DEVICE_TABLE} AS devices
                ON devices.device_id = target.device_id
            WHERE target.tenant_id IS NOT NULL
              AND target.tenant_id <> devices.tenant_id
            """
        )
    ).scalar_one()
    samples = bind.execute(
        sa.text(
            f"""
            SELECT CAST(target.id AS CHAR) AS id,
                   target.device_id AS device_id,
                   CAST(target.{period_column} AS CHAR) AS bucket,
                   target.tenant_id AS stored_tenant_id,
                   devices.tenant_id AS authoritative_tenant_id
            FROM {table_name} AS target
            INNER JOIN {DEVICE_TABLE} AS devices
                ON devices.device_id = target.device_id
            WHERE target.tenant_id IS NOT NULL
              AND target.tenant_id <> devices.tenant_id
            ORDER BY id
            LIMIT 10
            """
        )
    ).mappings().all()
    return int(count or 0), [dict(row) for row in samples]


def _assert_no_tenant_mismatches(table_name: str, period_column: str) -> None:
    mismatch_count, samples = _tenant_mismatches(table_name, period_column)
    if mismatch_count == 0:
        return
    sample_text = ", ".join(
        f"id={row['id']} device_id={row['device_id']} bucket={row['bucket']} "
        f"stored={row['stored_tenant_id']} authoritative={row['authoritative_tenant_id']}"
        for row in samples
    )
    raise RuntimeError(
        f"{table_name} contains {mismatch_count} tenant ownership mismatches against devices. "
        f"Sample rows: {sample_text}"
    )


def _ensure_index(table_name: str, index_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    if index_name in _get_indexes(inspector, table_name):
        return
    op.create_index(index_name, table_name, columns, unique=False)


def _ensure_unique_constraint(table_name: str, constraint_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    if constraint_name in _get_unique_constraints(inspector, table_name):
        op.drop_constraint(constraint_name, table_name, type_="unique")
    op.create_unique_constraint(constraint_name, table_name, columns)


def _alter_tenant_non_nullable(table_name: str) -> None:
    op.alter_column(table_name, "tenant_id", existing_type=TENANT_ID_TYPE, nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _ensure_tenant_column(inspector, "energy_device_day")
    _ensure_tenant_column(inspector, "energy_device_month")

    _backfill_tenant_column("energy_device_day")
    _backfill_tenant_column("energy_device_month")

    _assert_no_orphans("energy_device_day", "day")
    _assert_no_orphans("energy_device_month", "month")
    _assert_no_tenant_mismatches("energy_device_day", "day")
    _assert_no_tenant_mismatches("energy_device_month", "month")

    _alter_tenant_non_nullable("energy_device_day")
    _alter_tenant_non_nullable("energy_device_month")

    _ensure_unique_constraint("energy_device_day", "uq_energy_device_day", ["tenant_id", "device_id", "day"])
    _ensure_unique_constraint("energy_device_month", "uq_energy_device_month", ["tenant_id", "device_id", "month"])

    _ensure_index("energy_device_day", "ix_energy_device_day_tenant_day", ["tenant_id", "day"])
    _ensure_index("energy_device_month", "ix_energy_device_month_tenant_month", ["tenant_id", "month"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "ix_energy_device_month_tenant_month" in _get_indexes(inspector, "energy_device_month"):
        op.drop_index("ix_energy_device_month_tenant_month", table_name="energy_device_month")
    if "ix_energy_device_day_tenant_day" in _get_indexes(inspector, "energy_device_day"):
        op.drop_index("ix_energy_device_day_tenant_day", table_name="energy_device_day")

    if "uq_energy_device_month" in _get_unique_constraints(inspector, "energy_device_month"):
        op.drop_constraint("uq_energy_device_month", "energy_device_month", type_="unique")
        op.create_unique_constraint("uq_energy_device_month", "energy_device_month", ["device_id", "month"])
    if "uq_energy_device_day" in _get_unique_constraints(inspector, "energy_device_day"):
        op.drop_constraint("uq_energy_device_day", "energy_device_day", type_="unique")
        op.create_unique_constraint("uq_energy_device_day", "energy_device_day", ["device_id", "day"])

    month_columns = _get_columns(inspector, "energy_device_month")
    if "tenant_id" in month_columns:
        op.alter_column("energy_device_month", "tenant_id", existing_type=TENANT_ID_TYPE, nullable=True)
        op.drop_column("energy_device_month", "tenant_id")

    day_columns = _get_columns(inspector, "energy_device_day")
    if "tenant_id" in day_columns:
        op.alter_column("energy_device_day", "tenant_id", existing_type=TENANT_ID_TYPE, nullable=True)
        op.drop_column("energy_device_day", "tenant_id")
