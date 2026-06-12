"""Hard cut auth tenant IDs to persistent SH-prefixed business identifiers.

Revision ID: 0008_sh_tenant_ids
Revises: 0007_tenant_id_columns
Create Date: 2026-04-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_sh_tenant_ids"
down_revision = "0007_tenant_id_columns"
branch_labels = None
depends_on = None

TENANT_ID_PREFIX = "SH"
TENANT_ID_LENGTH = 10
TENANT_SEQUENCE_WIDTH = 8


def _is_valid_tenant_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == TENANT_ID_LENGTH
        and value.startswith(TENANT_ID_PREFIX)
        and value[2:].isdigit()
    )


def _require_reset_if_incompatible_rows(connection) -> None:
    checks = (
        ("organizations", "id"),
        ("plants", "tenant_id"),
        ("users", "tenant_id"),
        ("auth_action_tokens", "tenant_id"),
    )

    for table_name, column_name in checks:
        rows = connection.execute(
            sa.text(f"SELECT {column_name} FROM {table_name} WHERE {column_name} IS NOT NULL")
        ).scalars()
        invalid_values = [value for value in rows if not _is_valid_tenant_id(str(value))]
        if invalid_values:
            sample = ", ".join(repr(str(value)) for value in invalid_values[:3])
            raise RuntimeError(
                "Auth tenant hard cut requires a development reset before migration. "
                f"Found incompatible values in {table_name}.{column_name}: {sample}"
            )


def _initialize_allocator_state(connection) -> None:
    existing_ids = connection.execute(sa.text("SELECT id FROM organizations WHERE id IS NOT NULL")).scalars()
    current_max = 0
    for tenant_id in existing_ids:
        tenant_value = str(tenant_id)
        if not _is_valid_tenant_id(tenant_value):
            continue
        current_max = max(current_max, int(tenant_value[2:]))

    connection.execute(
        sa.text(
            """
            INSERT INTO tenant_id_sequences (prefix, next_value)
            VALUES (:prefix, :next_value)
            """
        ),
        {
            "prefix": TENANT_ID_PREFIX,
            "next_value": current_max + 1 if current_max else 1,
        },
    )


def _drop_fk_if_present(batch_op, inspector, table_name: str, constrained_columns: list[str], referred_table: str) -> None:
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("referred_table") != referred_table:
            continue
        if list(foreign_key.get("constrained_columns") or []) != constrained_columns:
            continue
        foreign_key_name = foreign_key.get("name")
        if foreign_key_name:
            batch_op.drop_constraint(foreign_key_name, type_="foreignkey")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _require_reset_if_incompatible_rows(bind)

    existing_tables = set(inspector.get_table_names())
    if "tenant_id_sequences" not in existing_tables:
        op.create_table(
            "tenant_id_sequences",
            sa.Column("prefix", sa.String(length=2), primary_key=True, nullable=False),
            sa.Column("next_value", sa.BigInteger(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        _initialize_allocator_state(bind)

    with op.batch_alter_table("plants") as batch_op:
        _drop_fk_if_present(batch_op, inspector, "plants", ["tenant_id"], "organizations")
        batch_op.alter_column(
            "tenant_id",
            existing_type=sa.String(length=36),
            type_=sa.String(length=TENANT_ID_LENGTH),
            nullable=False,
        )

    with op.batch_alter_table("users") as batch_op:
        _drop_fk_if_present(batch_op, inspector, "users", ["tenant_id"], "organizations")
        batch_op.alter_column(
            "tenant_id",
            existing_type=sa.String(length=36),
            type_=sa.String(length=TENANT_ID_LENGTH),
            nullable=True,
        )

    with op.batch_alter_table("organizations") as batch_op:
        batch_op.alter_column(
            "id",
            existing_type=sa.String(length=36),
            type_=sa.String(length=TENANT_ID_LENGTH),
            nullable=False,
        )

    with op.batch_alter_table("plants") as batch_op:
        batch_op.create_foreign_key("plants_ibfk_1", "organizations", ["tenant_id"], ["id"], ondelete="CASCADE")

    with op.batch_alter_table("users") as batch_op:
        batch_op.create_foreign_key("users_ibfk_1", "organizations", ["tenant_id"], ["id"], ondelete="SET NULL")

    with op.batch_alter_table("auth_action_tokens") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            existing_type=sa.String(length=36),
            type_=sa.String(length=TENANT_ID_LENGTH),
            nullable=True,
        )


def downgrade() -> None:
    raise NotImplementedError("Downgrade is not supported after the SH tenant_id hard cut.")
