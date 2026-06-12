"""Add device MQTT credential and ACL foundation tables.

Revision ID: 20260423_0001_device_mqtt_credentials
Revises: 20260416_0001
Create Date: 2026-04-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0001_device_mqtt_credentials"
down_revision = "20260416_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_mqtt_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("mqtt_username", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("password_algorithm", sa.String(length=32), nullable=False, server_default="sha256"),
        sa.Column("publish_topic", sa.String(length=255), nullable=False),
        sa.Column("subscribe_topic", sa.String(length=255), nullable=True),
        sa.Column("chip_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "device_id", name="uq_device_mqtt_credentials_device"),
        sa.UniqueConstraint("mqtt_username", name="uq_device_mqtt_credentials_username"),
    )
    op.create_index(
        "ix_device_mqtt_credentials_tenant_id",
        "device_mqtt_credentials",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_device_mqtt_credentials_device_id",
        "device_mqtt_credentials",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        "ix_device_mqtt_credentials_is_active",
        "device_mqtt_credentials",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "device_mqtt_acl",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("mqtt_username", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("access", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=32), nullable=False, server_default="allow"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("access IN ('publish', 'subscribe')", name="ck_device_mqtt_acl_access"),
        sa.CheckConstraint("permission IN ('allow', 'deny')", name="ck_device_mqtt_acl_permission"),
        sa.ForeignKeyConstraint(["credential_id"], ["device_mqtt_credentials.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "credential_id",
            "topic",
            "access",
            "permission",
            name="uq_device_mqtt_acl_rule",
        ),
    )
    op.create_index("ix_device_mqtt_acl_credential_id", "device_mqtt_acl", ["credential_id"], unique=False)
    op.create_index("ix_device_mqtt_acl_tenant_id", "device_mqtt_acl", ["tenant_id"], unique=False)
    op.create_index("ix_device_mqtt_acl_device_id", "device_mqtt_acl", ["device_id"], unique=False)
    op.create_index("ix_device_mqtt_acl_mqtt_username", "device_mqtt_acl", ["mqtt_username"], unique=False)
    op.create_index("ix_device_mqtt_acl_is_active", "device_mqtt_acl", ["is_active"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_device_mqtt_acl_is_active", table_name="device_mqtt_acl")
    op.drop_index("ix_device_mqtt_acl_mqtt_username", table_name="device_mqtt_acl")
    op.drop_index("ix_device_mqtt_acl_device_id", table_name="device_mqtt_acl")
    op.drop_index("ix_device_mqtt_acl_tenant_id", table_name="device_mqtt_acl")
    op.drop_index("ix_device_mqtt_acl_credential_id", table_name="device_mqtt_acl")
    op.drop_table("device_mqtt_acl")

    op.drop_index("ix_device_mqtt_credentials_is_active", table_name="device_mqtt_credentials")
    op.drop_index("ix_device_mqtt_credentials_device_id", table_name="device_mqtt_credentials")
    op.drop_index("ix_device_mqtt_credentials_tenant_id", table_name="device_mqtt_credentials")
    op.drop_table("device_mqtt_credentials")
