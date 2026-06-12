"""Make device_id+tenant_id composite primary key

Revision ID: 20260329_0001
Revises: 20260324_0003_backfill_legacy_tenant_ids
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260329_0001"
down_revision = "20260324_0003_backfill_legacy_tenant_ids"
branch_labels = None
depends_on = None


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _unique_exists(inspector: sa.Inspector, table_name: str, constraint_name: str) -> bool:
    return any(constraint["name"] == constraint_name for constraint in inspector.get_unique_constraints(table_name))


def _drop_foreign_keys(inspector: sa.Inspector, table_name: str) -> None:
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("referred_table") == "devices" and foreign_key.get("name"):
            op.drop_constraint(foreign_key["name"], table_name, type_="foreignkey")


def _backfill_tenant_column(table_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name} child
            JOIN devices parent ON parent.device_id = child.device_id
            SET child.tenant_id = parent.tenant_id
            WHERE child.tenant_id IS NULL
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    child_tables = (
        "device_live_state",
        "device_shifts",
        "parameter_health_config",
        "device_properties",
        "device_dashboard_widgets",
        "device_dashboard_widget_settings",
        "device_performance_trends",
        "idle_running_log",
    )

    for table_name in child_tables:
        _drop_foreign_keys(inspector, table_name)

    legacy_tenant_id = "legacy"
    bind.execute(
        sa.text("UPDATE devices SET tenant_id = :legacy_tenant_id WHERE tenant_id IS NULL"),
        {"legacy_tenant_id": legacy_tenant_id},
    )
    op.execute("ALTER TABLE devices DROP PRIMARY KEY")
    op.alter_column("devices", "tenant_id", existing_type=sa.String(50), nullable=False)
    op.execute("ALTER TABLE devices ADD PRIMARY KEY (device_id, tenant_id)")

    for table_name in child_tables:
        if not _column_exists(inspector, table_name, "tenant_id"):
            op.add_column(table_name, sa.Column("tenant_id", sa.String(length=50), nullable=True))

    inspector = sa.inspect(bind)
    for table_name in child_tables:
        _backfill_tenant_column(table_name)
        op.alter_column(table_name, "tenant_id", existing_type=sa.String(length=50), nullable=False)

    inspector = sa.inspect(bind)
    op.create_foreign_key(
        "fk_device_live_state_device_tenant",
        "device_live_state",
        "devices",
        ["device_id", "tenant_id"],
        ["device_id", "tenant_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_shifts_device_tenant",
        "device_shifts",
        "devices",
        ["device_id", "tenant_id"],
        ["device_id", "tenant_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_parameter_health_config_device_tenant",
        "parameter_health_config",
        "devices",
        ["device_id", "tenant_id"],
        ["device_id", "tenant_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_properties_device_tenant",
        "device_properties",
        "devices",
        ["device_id", "tenant_id"],
        ["device_id", "tenant_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_dashboard_widgets_device_tenant",
        "device_dashboard_widgets",
        "devices",
        ["device_id", "tenant_id"],
        ["device_id", "tenant_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_dashboard_widget_settings_device_tenant",
        "device_dashboard_widget_settings",
        "devices",
        ["device_id", "tenant_id"],
        ["device_id", "tenant_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_performance_trends_device_tenant",
        "device_performance_trends",
        "devices",
        ["device_id", "tenant_id"],
        ["device_id", "tenant_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_idle_running_log_device_tenant",
        "idle_running_log",
        "devices",
        ["device_id", "tenant_id"],
        ["device_id", "tenant_id"],
        ondelete="CASCADE",
    )

    op.execute("ALTER TABLE device_live_state DROP PRIMARY KEY")
    op.execute("ALTER TABLE device_live_state ADD PRIMARY KEY (device_id, tenant_id)")

    op.execute("ALTER TABLE device_dashboard_widget_settings DROP PRIMARY KEY")
    op.execute("ALTER TABLE device_dashboard_widget_settings ADD PRIMARY KEY (device_id, tenant_id)")

    if _unique_exists(inspector, "device_dashboard_widgets", "uq_device_dashboard_widget"):
        op.drop_constraint("uq_device_dashboard_widget", "device_dashboard_widgets", type_="unique")
    op.create_unique_constraint(
        "uq_device_dashboard_widget",
        "device_dashboard_widgets",
        ["device_id", "tenant_id", "field_name"],
    )

    if _index_exists(inspector, "device_dashboard_widgets", "ix_device_dashboard_widgets_device_order"):
        op.drop_index("ix_device_dashboard_widgets_device_order", table_name="device_dashboard_widgets")
    op.create_index(
        "ix_device_dashboard_widgets_device_order",
        "device_dashboard_widgets",
        ["device_id", "tenant_id", "display_order"],
    )

    if _unique_exists(inspector, "device_performance_trends", "uq_perf_trend_device_bucket"):
        op.drop_constraint("uq_perf_trend_device_bucket", "device_performance_trends", type_="unique")
    op.create_unique_constraint(
        "uq_perf_trend_device_bucket",
        "device_performance_trends",
        ["device_id", "tenant_id", "bucket_start_utc"],
    )

    if _unique_exists(inspector, "idle_running_log", "uq_idle_log_device_day"):
        op.drop_constraint("uq_idle_log_device_day", "idle_running_log", type_="unique")
    op.create_unique_constraint(
        "uq_idle_log_device_day",
        "idle_running_log",
        ["device_id", "tenant_id", "period_start"],
    )

    if _index_exists(inspector, "idle_running_log", "idx_idle_log_device_period"):
        op.drop_index("idx_idle_log_device_period", table_name="idle_running_log")
    op.create_index(
        "idx_idle_log_device_period",
        "idle_running_log",
        ["device_id", "tenant_id", "period_start"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _index_exists(inspector, "idle_running_log", "idx_idle_log_device_period"):
        op.drop_index("idx_idle_log_device_period", table_name="idle_running_log")
    if _unique_exists(inspector, "idle_running_log", "uq_idle_log_device_day"):
        op.drop_constraint("uq_idle_log_device_day", "idle_running_log", type_="unique")
    op.create_unique_constraint("uq_idle_log_device_day", "idle_running_log", ["device_id", "period_start"])
    op.create_index("idx_idle_log_device_period", "idle_running_log", ["device_id", "period_start"])

    if _unique_exists(inspector, "device_performance_trends", "uq_perf_trend_device_bucket"):
        op.drop_constraint("uq_perf_trend_device_bucket", "device_performance_trends", type_="unique")
    op.create_unique_constraint("uq_perf_trend_device_bucket", "device_performance_trends", ["device_id", "bucket_start_utc"])

    if _index_exists(inspector, "device_dashboard_widgets", "ix_device_dashboard_widgets_device_order"):
        op.drop_index("ix_device_dashboard_widgets_device_order", table_name="device_dashboard_widgets")
    if _unique_exists(inspector, "device_dashboard_widgets", "uq_device_dashboard_widget"):
        op.drop_constraint("uq_device_dashboard_widget", "device_dashboard_widgets", type_="unique")
    op.create_unique_constraint("uq_device_dashboard_widget", "device_dashboard_widgets", ["device_id", "field_name"])
    op.create_index("ix_device_dashboard_widgets_device_order", "device_dashboard_widgets", ["device_id", "display_order"])

    op.execute("ALTER TABLE device_dashboard_widget_settings DROP PRIMARY KEY")
    op.execute("ALTER TABLE device_dashboard_widget_settings ADD PRIMARY KEY (device_id)")

    op.execute("ALTER TABLE device_live_state DROP PRIMARY KEY")
    op.execute("ALTER TABLE device_live_state ADD PRIMARY KEY (device_id)")

    inspector = sa.inspect(bind)
    for table_name in (
        "device_shifts",
        "parameter_health_config",
        "device_properties",
        "device_dashboard_widgets",
        "device_dashboard_widget_settings",
        "device_performance_trends",
        "idle_running_log",
    ):
        _drop_foreign_keys(inspector, table_name)
    _drop_foreign_keys(inspector, "device_live_state")

    for table_name in (
        "device_shifts",
        "parameter_health_config",
        "device_properties",
        "device_dashboard_widgets",
        "device_dashboard_widget_settings",
        "device_performance_trends",
        "idle_running_log",
        "device_live_state",
    ):
        if _column_exists(sa.inspect(bind), table_name, "tenant_id"):
            op.alter_column(table_name, "tenant_id", existing_type=sa.String(length=50), nullable=True)

    op.execute("ALTER TABLE devices DROP PRIMARY KEY")
    op.execute("ALTER TABLE devices ADD PRIMARY KEY (device_id)")
    op.alter_column("devices", "tenant_id", existing_type=sa.String(50), nullable=True)

    op.create_foreign_key(
        "fk_device_live_state_device_id",
        "device_live_state",
        "devices",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_shifts_device_id",
        "device_shifts",
        "devices",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_parameter_health_config_device_id",
        "parameter_health_config",
        "devices",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_properties_device_id",
        "device_properties",
        "devices",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_dashboard_widgets_device_id",
        "device_dashboard_widgets",
        "devices",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_dashboard_widget_settings_device_id",
        "device_dashboard_widget_settings",
        "devices",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_device_performance_trends_device_id",
        "device_performance_trends",
        "devices",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_idle_running_log_device_id",
        "idle_running_log",
        "devices",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
