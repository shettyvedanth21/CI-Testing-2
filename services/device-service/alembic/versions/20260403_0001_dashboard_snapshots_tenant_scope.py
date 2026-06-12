"""Enforce tenant-scoped ownership for dashboard snapshots.

Revision ID: 20260403_0001_dashboard_snapshots_tenant_scope
Revises: 20260401_0001
Create Date: 2026-04-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260403_0001_dashboard_snapshots_tenant_scope"
down_revision = "20260401_0001"
branch_labels = None
depends_on = None

OLD_TABLE = "dashboard_snapshots"
NEW_TABLE = "dashboard_snapshots_v2"


def _split_legacy_snapshot_key(snapshot_key: str) -> tuple[str, str]:
    tenant_id, separator, logical_key = (snapshot_key or "").partition(":")
    tenant_id = tenant_id.strip()
    logical_key = logical_key.strip()
    if (
        not separator
        or not tenant_id
        or not logical_key
        or not logical_key.startswith("dashboard:")
    ):
        raise RuntimeError(
            "dashboard_snapshots migration requires legacy keys in '<tenant_id>:<snapshot_key>' format. "
            f"Found invalid snapshot_key={snapshot_key!r}."
        )
    return tenant_id, logical_key


def _create_new_table() -> None:
    op.create_table(
        NEW_TABLE,
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("snapshot_key", sa.String(length=120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("s3_key", sa.String(length=512), nullable=True),
        sa.Column(
            "storage_backend",
            sa.Enum("mysql", "minio", name="dashboard_snapshot_storage_backend"),
            nullable=False,
            server_default=sa.text("'mysql'"),
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "snapshot_key"),
    )
    op.create_index("ix_dashboard_snapshots_tenant_id", NEW_TABLE, ["tenant_id"])
    op.create_index("ix_dashboard_snapshots_generated_at", NEW_TABLE, ["generated_at"])
    op.create_index("ix_dashboard_snapshots_expires_at", NEW_TABLE, ["expires_at"])


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if OLD_TABLE not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns(OLD_TABLE)}
    pk_columns = list((inspector.get_pk_constraint(OLD_TABLE) or {}).get("constrained_columns") or [])
    if "tenant_id" in columns and pk_columns == ["tenant_id", "snapshot_key"]:
        return

    if NEW_TABLE in inspector.get_table_names():
        op.drop_table(NEW_TABLE)

    _create_new_table()

    rows = bind.execute(
        sa.text(
            f"""
            SELECT
                snapshot_key,
                payload_json,
                s3_key,
                storage_backend,
                generated_at,
                expires_at,
                created_at,
                updated_at
            FROM {OLD_TABLE}
            ORDER BY snapshot_key ASC
            """
        )
    ).mappings()

    for row in rows:
        tenant_id, logical_key = _split_legacy_snapshot_key(str(row["snapshot_key"]))
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {NEW_TABLE} (
                    tenant_id,
                    snapshot_key,
                    payload_json,
                    s3_key,
                    storage_backend,
                    generated_at,
                    expires_at,
                    created_at,
                    updated_at
                ) VALUES (
                    :tenant_id,
                    :snapshot_key,
                    :payload_json,
                    :s3_key,
                    :storage_backend,
                    :generated_at,
                    :expires_at,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "snapshot_key": logical_key,
                "payload_json": row.get("payload_json"),
                "s3_key": row.get("s3_key") if "s3_key" in columns else None,
                "storage_backend": row.get("storage_backend") if "storage_backend" in columns else "mysql",
                "generated_at": row["generated_at"],
                "expires_at": row.get("expires_at"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    op.drop_table(OLD_TABLE)
    op.rename_table(NEW_TABLE, OLD_TABLE)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if OLD_TABLE not in inspector.get_table_names():
        return

    if NEW_TABLE in inspector.get_table_names():
        op.drop_table(NEW_TABLE)

    op.create_table(
        NEW_TABLE,
        sa.Column("snapshot_key", sa.String(length=120), nullable=False, primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("s3_key", sa.String(length=512), nullable=True),
        sa.Column(
            "storage_backend",
            sa.Enum("mysql", "minio", name="dashboard_snapshot_storage_backend"),
            nullable=False,
            server_default=sa.text("'mysql'"),
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dashboard_snapshots_generated_at", NEW_TABLE, ["generated_at"])
    op.create_index("ix_dashboard_snapshots_expires_at", NEW_TABLE, ["expires_at"])

    rows = bind.execute(
        sa.text(
            f"""
            SELECT
                tenant_id,
                snapshot_key,
                payload_json,
                s3_key,
                storage_backend,
                generated_at,
                expires_at,
                created_at,
                updated_at
            FROM {OLD_TABLE}
            ORDER BY tenant_id ASC, snapshot_key ASC
            """
        )
    ).mappings()

    for row in rows:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {NEW_TABLE} (
                    snapshot_key,
                    payload_json,
                    s3_key,
                    storage_backend,
                    generated_at,
                    expires_at,
                    created_at,
                    updated_at
                ) VALUES (
                    :snapshot_key,
                    :payload_json,
                    :s3_key,
                    :storage_backend,
                    :generated_at,
                    :expires_at,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "snapshot_key": f"{row['tenant_id']}:{row['snapshot_key']}",
                "payload_json": row.get("payload_json"),
                "s3_key": row.get("s3_key"),
                "storage_backend": row.get("storage_backend") or "mysql",
                "generated_at": row["generated_at"],
                "expires_at": row.get("expires_at"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    op.drop_table(OLD_TABLE)
    op.rename_table(NEW_TABLE, OLD_TABLE)
