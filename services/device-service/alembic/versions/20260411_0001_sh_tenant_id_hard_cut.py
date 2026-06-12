"""Hard cut device-service tenant columns to SH tenant IDs.

Revision ID: 20260411_0001_sh_tenant_id_hard_cut
Revises: 20260409_0001_signed_telemetry_device_config
Create Date: 2026-04-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260411_0001_sh_tenant_id_hard_cut"
down_revision = "20260409_0001_signed_telemetry_device_config"
branch_labels = None
depends_on = None

TENANT_ID_LENGTH = 10
TENANT_ID_PREFIX = "SH"


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _is_valid_tenant_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == TENANT_ID_LENGTH
        and value.startswith(TENANT_ID_PREFIX)
        and value[2:].isdigit()
    )


def _require_reset_for_incompatible_values(table_name: str, column_name: str) -> None:
    if not _table_exists(table_name) or not _column_exists(table_name, column_name):
        return

    rows = op.get_bind().execute(
        sa.text(f"SELECT {column_name} FROM {table_name} WHERE {column_name} IS NOT NULL")
    ).scalars()
    invalid_values = [value for value in rows if not _is_valid_tenant_id(str(value))]
    if invalid_values:
        sample = ", ".join(repr(str(value)) for value in invalid_values[:3])
        raise RuntimeError(
            "Device-service SH tenant hard cut requires a downstream reset before migration. "
            f"Found incompatible values in {table_name}.{column_name}: {sample}"
        )


def _drop_foreign_key(
    table_name: str,
    constrained_columns: list[str],
    referred_table: str,
) -> list[tuple[str, dict[str, object]]]:
    dropped: list[tuple[str, dict[str, object]]] = []
    if not _table_exists(table_name):
        return dropped

    for foreign_key in _inspector().get_foreign_keys(table_name):
        if foreign_key.get("referred_table") != referred_table:
            continue
        if list(foreign_key.get("constrained_columns") or []) != constrained_columns:
            continue
        foreign_key_name = foreign_key.get("name")
        if foreign_key_name:
            op.drop_constraint(foreign_key_name, table_name, type_="foreignkey")
            dropped.append((table_name, foreign_key))
    return dropped


def _create_foreign_key_from_metadata(table_name: str, foreign_key: dict[str, object]) -> None:
    name = foreign_key.get("name")
    if not name:
        return
    op.create_foreign_key(
        name,
        table_name,
        str(foreign_key["referred_table"]),
        list(foreign_key.get("constrained_columns") or []),
        list(foreign_key.get("referred_columns") or []),
        ondelete=foreign_key.get("options", {}).get("ondelete"),
    )


def _alter_tenant_column(table_name: str, column_name: str, *, nullable: bool) -> None:
    if not _table_exists(table_name) or not _column_exists(table_name, column_name):
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.alter_column(
            column_name,
            existing_type=sa.String(length=50),
            type_=sa.String(length=TENANT_ID_LENGTH),
            nullable=nullable,
        )


def upgrade() -> None:
    tenant_columns = (
        ("devices", "tenant_id"),
        ("device_shifts", "tenant_id"),
        ("parameter_health_config", "tenant_id"),
        ("device_performance_trends", "tenant_id"),
        ("device_properties", "tenant_id"),
        ("device_dashboard_widgets", "tenant_id"),
        ("device_dashboard_widget_settings", "tenant_id"),
        ("idle_running_log", "tenant_id"),
        ("device_live_state", "tenant_id"),
        ("waste_site_config", "tenant_id"),
        ("dashboard_snapshots", "tenant_id"),
        ("hardware_units", "tenant_id"),
        ("device_hardware_installations", "tenant_id"),
        ("tenant_security_audit_log", "caller_tenant_id"),
        ("tenant_security_audit_log", "target_tenant_id"),
    )
    for table_name, column_name in tenant_columns:
        _require_reset_for_incompatible_values(table_name, column_name)

    dropped_fks = []
    dropped_fks.extend(_drop_foreign_key("device_shifts", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("parameter_health_config", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("device_properties", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("device_dashboard_widgets", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("device_dashboard_widget_settings", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("device_performance_trends", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("idle_running_log", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("device_live_state", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("device_hardware_installations", ["device_id", "tenant_id"], "devices"))
    dropped_fks.extend(_drop_foreign_key("device_hardware_installations", ["tenant_id", "hardware_unit_id"], "hardware_units"))

    nullable_columns = {
        ("device_shifts", "tenant_id"),
        ("parameter_health_config", "tenant_id"),
        ("waste_site_config", "tenant_id"),
        ("tenant_security_audit_log", "caller_tenant_id"),
        ("tenant_security_audit_log", "target_tenant_id"),
    }
    for table_name, column_name in tenant_columns:
        _alter_tenant_column(
            table_name,
            column_name,
            nullable=(table_name, column_name) in nullable_columns,
        )

    for table_name, foreign_key in dropped_fks:
        if _table_exists(table_name):
            _create_foreign_key_from_metadata(table_name, foreign_key)


def downgrade() -> None:
    raise NotImplementedError("Downgrade is not supported after the SH tenant_id hard cut.")
