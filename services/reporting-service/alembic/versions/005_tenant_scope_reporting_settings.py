"""Tenant-scope reporting settings tables.

Revision ID: 005_tenant_scope_reporting_settings
Revises: 004_add_reporting_indexes
Create Date: 2026-04-03
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "005_tenant_scope_reporting_settings"
down_revision: Union[str, None] = "004_add_reporting_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _is_column_nullable(bind, table_name: str, column_name: str) -> bool:
    inspector = inspect(bind)
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return bool(column.get("nullable", True))
    raise RuntimeError(f"Column {table_name}.{column_name} not found")


def _has_index(bind, table_name: str, index_name: str) -> bool:
    inspector = inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _has_unique_constraint(bind, table_name: str, constraint_name: str) -> bool:
    inspector = inspect(bind)
    return any(
        constraint["name"] == constraint_name
        for constraint in inspector.get_unique_constraints(table_name)
    )


def _normalize_channel_value(channel_type: str, value: str) -> str:
    normalized = str(value or "").strip()
    if channel_type == "email":
        normalized = normalized.lower()
    return normalized


def _build_tariff_backfill_rows(
    tenant_ids: Iterable[str],
    legacy_rows: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    ordered_rows = list(legacy_rows)
    if not ordered_rows:
        return []

    canonical = ordered_rows[0]
    return [
        {
            "tenant_id": tenant_id,
            "rate": canonical["rate"],
            "currency": canonical["currency"],
            "updated_at": canonical["updated_at"],
            "updated_by": canonical["updated_by"],
        }
        for tenant_id in tenant_ids
    ]


def _build_notification_backfill_rows(
    tenant_ids: Iterable[str],
    legacy_rows: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    canonical_by_channel: dict[tuple[str, str], dict[str, object]] = {}
    for row in legacy_rows:
        channel_type = str(row["channel_type"])
        value = _normalize_channel_value(channel_type, str(row["value"]))
        key = (channel_type, value)
        candidate = {
            "channel_type": channel_type,
            "value": value,
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }
        current = canonical_by_channel.get(key)
        if current is None:
            canonical_by_channel[key] = candidate
            continue

        current["is_active"] = bool(current["is_active"] or candidate["is_active"])
        if candidate["created_at"] < current["created_at"]:
            current["created_at"] = candidate["created_at"]

    backfill_rows: list[dict[str, object]] = []
    for tenant_id in tenant_ids:
        for channel in canonical_by_channel.values():
            backfill_rows.append(
                {
                    "tenant_id": tenant_id,
                    "channel_type": channel["channel_type"],
                    "value": channel["value"],
                    "is_active": channel["is_active"],
                    "created_at": channel["created_at"],
                }
            )
    return backfill_rows


def upgrade() -> None:
    bind = op.get_bind()

    tariff_has_tenant = _has_column(bind, "tariff_config", "tenant_id")
    channel_has_tenant = _has_column(bind, "notification_channels", "tenant_id")

    if not tariff_has_tenant:
        op.add_column("tariff_config", sa.Column("tenant_id", sa.String(length=50), nullable=True))
    if not channel_has_tenant:
        op.add_column("notification_channels", sa.Column("tenant_id", sa.String(length=50), nullable=True))

    tenant_ids = [str(row[0]) for row in bind.execute(sa.text("SELECT id FROM organizations ORDER BY created_at ASC")).fetchall()]

    legacy_tariffs = bind.execute(
        sa.text(
            """
            SELECT id, rate, currency, updated_at, updated_by
            FROM tariff_config
            WHERE tenant_id IS NULL
            ORDER BY id ASC
            """
        )
    ).mappings().all()
    existing_tariff_tenants = {
        str(row["tenant_id"])
        for row in bind.execute(
            sa.text(
                """
                SELECT tenant_id
                FROM tariff_config
                WHERE tenant_id IS NOT NULL
                """
            )
        ).mappings().all()
    }
    if legacy_tariffs:
        if not tenant_ids:
            raise RuntimeError("Cannot backfill tariff_config without organizations")
        missing_tenant_ids = [tenant_id for tenant_id in tenant_ids if tenant_id not in existing_tariff_tenants]
        backfill_tariffs = _build_tariff_backfill_rows(missing_tenant_ids, legacy_tariffs)
        if backfill_tariffs:
            op.bulk_insert(
                sa.table(
                    "tariff_config",
                    sa.column("tenant_id", sa.String()),
                    sa.column("rate", sa.Numeric()),
                    sa.column("currency", sa.String()),
                    sa.column("updated_at", sa.DateTime()),
                    sa.column("updated_by", sa.String()),
                ),
                backfill_tariffs,
            )
        bind.execute(sa.text("DELETE FROM tariff_config WHERE tenant_id IS NULL"))

    legacy_channels = bind.execute(
        sa.text(
            """
            SELECT id, channel_type, value, is_active, created_at
            FROM notification_channels
            WHERE tenant_id IS NULL
            ORDER BY id ASC
            """
        )
    ).mappings().all()
    if legacy_channels:
        if not tenant_ids:
            raise RuntimeError("Cannot backfill notification_channels without organizations")
        canonical_channels = _build_notification_backfill_rows(["__PLACEHOLDER__"], legacy_channels)
        for tenant_id in tenant_ids:
            for channel in canonical_channels:
                exists = bind.execute(
                    sa.text(
                        """
                        SELECT 1
                        FROM notification_channels
                        WHERE tenant_id = :tenant_id
                          AND channel_type = :channel_type
                          AND value = :value
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "channel_type": channel["channel_type"],
                        "value": channel["value"],
                    },
                ).first()
                if exists:
                    continue
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO notification_channels (
                            tenant_id,
                            channel_type,
                            value,
                            is_active,
                            created_at
                        ) VALUES (
                            :tenant_id,
                            :channel_type,
                            :value,
                            :is_active,
                            :created_at
                        )
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "channel_type": channel["channel_type"],
                        "value": channel["value"],
                        "is_active": channel["is_active"],
                        "created_at": channel["created_at"],
                    },
                )
        bind.execute(sa.text("DELETE FROM notification_channels WHERE tenant_id IS NULL"))

    null_tariff_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM tariff_config WHERE tenant_id IS NULL")
    ).scalar_one()
    null_channel_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM notification_channels WHERE tenant_id IS NULL")
    ).scalar_one()
    if null_tariff_count or null_channel_count:
        raise RuntimeError("Reporting settings tenant backfill left null tenant_id values")

    if _is_column_nullable(bind, "tariff_config", "tenant_id"):
        op.alter_column("tariff_config", "tenant_id", existing_type=sa.String(length=50), nullable=False)
    if _is_column_nullable(bind, "notification_channels", "tenant_id"):
        op.alter_column("notification_channels", "tenant_id", existing_type=sa.String(length=50), nullable=False)

    if not _has_unique_constraint(bind, "tariff_config", "uq_tariff_config_tenant_id"):
        op.create_unique_constraint("uq_tariff_config_tenant_id", "tariff_config", ["tenant_id"])
    if not _has_index(bind, "tariff_config", "ix_tariff_config_tenant_id"):
        op.create_index("ix_tariff_config_tenant_id", "tariff_config", ["tenant_id"], unique=False)
    if not _has_unique_constraint(bind, "notification_channels", "uq_notification_channels_tenant_type_value"):
        op.create_unique_constraint(
            "uq_notification_channels_tenant_type_value",
            "notification_channels",
            ["tenant_id", "channel_type", "value"],
        )
    if not _has_index(bind, "notification_channels", "ix_notification_channels_tenant_channel_active"):
        op.create_index(
            "ix_notification_channels_tenant_channel_active",
            "notification_channels",
            ["tenant_id", "channel_type", "is_active"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_notification_channels_tenant_channel_active", table_name="notification_channels")
    op.drop_constraint("uq_notification_channels_tenant_type_value", "notification_channels", type_="unique")
    op.drop_index("ix_tariff_config_tenant_id", table_name="tariff_config")
    op.drop_constraint("uq_tariff_config_tenant_id", "tariff_config", type_="unique")
    op.drop_column("notification_channels", "tenant_id")
    op.drop_column("tariff_config", "tenant_id")
