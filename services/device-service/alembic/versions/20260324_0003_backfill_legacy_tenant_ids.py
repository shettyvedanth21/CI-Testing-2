"""Backfill legacy tenant ids for device-service tables.

Revision ID: 20260324_0003_backfill_legacy_tenant_ids
Revises: add_dashboard_snapshot_minio_storage, add_waste_config_fields
Create Date: 2026-03-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260324_0003_backfill_legacy_tenant_ids"
down_revision = (
    "add_dashboard_snapshot_minio_storage",
    "add_waste_config_fields",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    legacy_tenant_id = "legacy"
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE devices SET tenant_id = :legacy_tenant_id WHERE tenant_id IS NULL"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE device_shifts SET tenant_id = :legacy_tenant_id WHERE tenant_id IS NULL"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE parameter_health_config SET tenant_id = :legacy_tenant_id WHERE tenant_id IS NULL"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE waste_site_config SET tenant_id = :legacy_tenant_id WHERE tenant_id IS NULL"), {"legacy_tenant_id": legacy_tenant_id})


def downgrade() -> None:
    legacy_tenant_id = "legacy"
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE waste_site_config SET tenant_id = NULL WHERE tenant_id = :legacy_tenant_id"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE parameter_health_config SET tenant_id = NULL WHERE tenant_id = :legacy_tenant_id"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE device_shifts SET tenant_id = NULL WHERE tenant_id = :legacy_tenant_id"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE devices SET tenant_id = NULL WHERE tenant_id = :legacy_tenant_id"), {"legacy_tenant_id": legacy_tenant_id})
