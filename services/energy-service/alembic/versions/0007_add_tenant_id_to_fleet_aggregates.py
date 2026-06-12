"""add first-class tenant columns to fleet aggregate tables"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_add_tenant_id_to_fleet_aggregates"
down_revision = "0006_add_tenant_id_to_energy_aggregates"
branch_labels = None
depends_on = None


TENANT_ID_TYPE = sa.String(length=10)


def _get_columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _get_indexes(inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _get_pk_columns(inspector, table_name: str) -> list[str]:
    pk = inspector.get_pk_constraint(table_name) or {}
    return list(pk.get("constrained_columns") or [])


def _ensure_tenant_column(inspector, table_name: str) -> None:
    if "tenant_id" in _get_columns(inspector, table_name):
        return
    op.add_column(table_name, sa.Column("tenant_id", TENANT_ID_TYPE, nullable=True))


def _quote_columns(columns: list[str]) -> str:
    return ", ".join(f"`{column}`" for column in columns)


def _ensure_primary_key(table_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    current = _get_pk_columns(inspector, table_name)
    if current == columns:
        return
    if current:
        op.execute(sa.text(f"ALTER TABLE `{table_name}` DROP PRIMARY KEY"))
    op.execute(sa.text(f"ALTER TABLE `{table_name}` ADD PRIMARY KEY ({_quote_columns(columns)})"))


def _ensure_index(table_name: str, index_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    if index_name in _get_indexes(inspector, table_name):
        return
    op.create_index(index_name, table_name, columns, unique=False)


def _assert_legacy_fleet_rebuildable(table_name: str, period_column: str, source_table: str) -> None:
    bind = op.get_bind()
    orphan_count = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*) AS row_count
            FROM {table_name} AS fleet
            LEFT JOIN {source_table} AS source
                ON source.{period_column} = fleet.{period_column}
            WHERE source.{period_column} IS NULL
            """
        )
    ).scalar_one()
    if int(orphan_count or 0) == 0:
        return

    samples = bind.execute(
        sa.text(
            f"""
            SELECT CAST(fleet.{period_column} AS CHAR) AS bucket
            FROM {table_name} AS fleet
            LEFT JOIN {source_table} AS source
                ON source.{period_column} = fleet.{period_column}
            WHERE source.{period_column} IS NULL
            ORDER BY fleet.{period_column}
            LIMIT 10
            """
        )
    ).mappings().all()
    sample_text = ", ".join(str(row["bucket"]) for row in samples)
    raise RuntimeError(
        f"{table_name} contains {int(orphan_count)} legacy rows that cannot be rebuilt from {source_table}. "
        f"Sample buckets: {sample_text}"
    )


def _clear_legacy_rows(table_name: str) -> None:
    op.execute(sa.text(f"DELETE FROM `{table_name}`"))


def _repopulate_fleet_day() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO energy_fleet_day (
                tenant_id,
                day,
                energy_kwh,
                energy_cost_inr,
                idle_kwh,
                offhours_kwh,
                overconsumption_kwh,
                loss_kwh,
                loss_cost_inr,
                version,
                updated_at
            )
            SELECT
                device_day.tenant_id,
                device_day.day,
                SUM(device_day.energy_kwh) AS energy_kwh,
                SUM(device_day.energy_cost_inr) AS energy_cost_inr,
                SUM(device_day.idle_kwh) AS idle_kwh,
                SUM(device_day.offhours_kwh) AS offhours_kwh,
                SUM(device_day.overconsumption_kwh) AS overconsumption_kwh,
                SUM(device_day.loss_kwh) AS loss_kwh,
                SUM(device_day.loss_cost_inr) AS loss_cost_inr,
                MAX(device_day.version) AS version,
                MAX(device_day.updated_at) AS updated_at
            FROM energy_device_day AS device_day
            WHERE device_day.tenant_id IS NOT NULL
            GROUP BY device_day.tenant_id, device_day.day
            """
        )
    )


def _repopulate_fleet_month() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO energy_fleet_month (
                tenant_id,
                month,
                energy_kwh,
                energy_cost_inr,
                idle_kwh,
                offhours_kwh,
                overconsumption_kwh,
                loss_kwh,
                loss_cost_inr,
                version,
                updated_at
            )
            SELECT
                device_month.tenant_id,
                device_month.month,
                SUM(device_month.energy_kwh) AS energy_kwh,
                SUM(device_month.energy_cost_inr) AS energy_cost_inr,
                SUM(device_month.idle_kwh) AS idle_kwh,
                SUM(device_month.offhours_kwh) AS offhours_kwh,
                SUM(device_month.overconsumption_kwh) AS overconsumption_kwh,
                SUM(device_month.loss_kwh) AS loss_kwh,
                SUM(device_month.loss_cost_inr) AS loss_cost_inr,
                MAX(device_month.version) AS version,
                MAX(device_month.updated_at) AS updated_at
            FROM energy_device_month AS device_month
            WHERE device_month.tenant_id IS NOT NULL
            GROUP BY device_month.tenant_id, device_month.month
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _ensure_tenant_column(inspector, "energy_fleet_day")
    _ensure_tenant_column(inspector, "energy_fleet_month")

    _assert_legacy_fleet_rebuildable("energy_fleet_day", "day", "energy_device_day")
    _assert_legacy_fleet_rebuildable("energy_fleet_month", "month", "energy_device_month")

    _clear_legacy_rows("energy_fleet_day")
    _clear_legacy_rows("energy_fleet_month")

    op.alter_column("energy_fleet_day", "tenant_id", existing_type=TENANT_ID_TYPE, nullable=False)
    op.alter_column("energy_fleet_month", "tenant_id", existing_type=TENANT_ID_TYPE, nullable=False)

    _ensure_primary_key("energy_fleet_day", ["tenant_id", "day"])
    _ensure_primary_key("energy_fleet_month", ["tenant_id", "month"])

    _ensure_index("energy_fleet_day", "ix_energy_fleet_day_day", ["day"])
    _ensure_index("energy_fleet_month", "ix_energy_fleet_month_month", ["month"])

    _repopulate_fleet_day()
    _repopulate_fleet_month()


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    _clear_legacy_rows("energy_fleet_month")
    _clear_legacy_rows("energy_fleet_day")

    _ensure_primary_key("energy_fleet_month", ["month"])
    _ensure_primary_key("energy_fleet_day", ["day"])

    if "ix_energy_fleet_month_month" in _get_indexes(inspector, "energy_fleet_month"):
        op.drop_index("ix_energy_fleet_month_month", table_name="energy_fleet_month")
    if "ix_energy_fleet_day_day" in _get_indexes(inspector, "energy_fleet_day"):
        op.drop_index("ix_energy_fleet_day_day", table_name="energy_fleet_day")

    month_columns = _get_columns(inspector, "energy_fleet_month")
    if "tenant_id" in month_columns:
        op.alter_column("energy_fleet_month", "tenant_id", existing_type=TENANT_ID_TYPE, nullable=True)
        op.drop_column("energy_fleet_month", "tenant_id")

    day_columns = _get_columns(inspector, "energy_fleet_day")
    if "tenant_id" in day_columns:
        op.alter_column("energy_fleet_day", "tenant_id", existing_type=TENANT_ID_TYPE, nullable=True)
        op.drop_column("energy_fleet_day", "tenant_id")
