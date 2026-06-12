"""Add device identity class and prefixed sequence allocation.

Revision ID: 20260407_0001_prefixed_device_ids
Revises: 20260403_0001_dashboard_snapshots_tenant_scope
Create Date: 2026-04-07
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260407_0001_prefixed_device_ids"
down_revision = "20260403_0001_dashboard_snapshots_tenant_scope"
branch_labels = None
depends_on = None

_DEVICE_ID_PATTERN = re.compile(r"^(AD|TD|VD)(\d{8})$")
_CLASS_BY_PREFIX = {"AD": "active", "TD": "test", "VD": "virtual"}


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _seed_sequence_rows() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT device_id FROM devices")).mappings().all()

    max_by_prefix = {"AD": 0, "TD": 0, "VD": 0}
    for row in rows:
        device_id = str(row["device_id"])
        match = _DEVICE_ID_PATTERN.fullmatch(device_id)
        if match is None:
            continue
        prefix, numeric = match.groups()
        max_by_prefix[prefix] = max(max_by_prefix[prefix], int(numeric))

    now = datetime.utcnow()
    for prefix in ("AD", "TD", "VD"):
        bind.execute(
            sa.text(
                """
                INSERT INTO device_id_sequences (prefix, next_value, updated_at)
                VALUES (:prefix, :next_value, :updated_at)
                """
            ),
            {
                "prefix": prefix,
                "next_value": max_by_prefix[prefix] + 1,
                "updated_at": now,
            },
        )


def _backfill_known_device_classes() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT device_id, tenant_id FROM devices")).mappings().all()
    for row in rows:
        device_id = str(row["device_id"])
        match = _DEVICE_ID_PATTERN.fullmatch(device_id)
        if match is None:
            continue
        prefix, _ = match.groups()
        bind.execute(
            sa.text(
                """
                UPDATE devices
                SET device_id_class = :device_id_class
                WHERE device_id = :device_id AND tenant_id = :tenant_id
                """
            ),
            {
                "device_id_class": _CLASS_BY_PREFIX[prefix],
                "device_id": device_id,
                "tenant_id": row["tenant_id"],
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    device_columns = _existing_columns("devices")

    if "device_id_class" not in device_columns:
        op.add_column("devices", sa.Column("device_id_class", sa.String(length=20), nullable=True))
    existing_indexes = {index["name"] for index in inspector.get_indexes("devices")}
    if "ix_devices_device_id_class" not in existing_indexes:
        op.create_index("ix_devices_device_id_class", "devices", ["device_id_class"])
    existing_uniques = {constraint["name"] for constraint in inspector.get_unique_constraints("devices")}
    if "uq_devices_device_id" not in existing_uniques:
        op.create_unique_constraint("uq_devices_device_id", "devices", ["device_id"])

    if "device_id_sequences" not in inspector.get_table_names():
        op.create_table(
            "device_id_sequences",
            sa.Column("prefix", sa.String(length=2), nullable=False),
            sa.Column("next_value", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("prefix"),
        )
        _seed_sequence_rows()

    _backfill_known_device_classes()


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device_id_sequences" in inspector.get_table_names():
        op.drop_table("device_id_sequences")

    existing_uniques = {constraint["name"] for constraint in inspector.get_unique_constraints("devices")}
    if "uq_devices_device_id" in existing_uniques:
        op.drop_constraint("uq_devices_device_id", "devices", type_="unique")

    existing_indexes = {index["name"] for index in inspector.get_indexes("devices")}
    if "ix_devices_device_id_class" in existing_indexes:
        op.drop_index("ix_devices_device_id_class", table_name="devices")

    device_columns = _existing_columns("devices")
    if "device_id_class" in device_columns:
        op.drop_column("devices", "device_id_class")
