"""Backfill legacy tenant ids for rule-engine tables.

Revision ID: 20260324_0006_backfill_legacy_tenant_ids
Revises: 005_add_rule_cooldown_units
Create Date: 2026-03-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260324_0006_backfill_legacy_tenant_ids"
down_revision: Union[str, None] = "005_add_rule_cooldown_units"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    legacy_tenant_id = "legacy"
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE rules SET tenant_id = :legacy_tenant_id WHERE tenant_id IS NULL"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE alerts SET tenant_id = :legacy_tenant_id WHERE tenant_id IS NULL"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE activity_events SET tenant_id = :legacy_tenant_id WHERE tenant_id IS NULL"), {"legacy_tenant_id": legacy_tenant_id})


def downgrade() -> None:
    legacy_tenant_id = "legacy"
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE activity_events SET tenant_id = NULL WHERE tenant_id = :legacy_tenant_id"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE alerts SET tenant_id = NULL WHERE tenant_id = :legacy_tenant_id"), {"legacy_tenant_id": legacy_tenant_id})
    conn.execute(sa.text("UPDATE rules SET tenant_id = NULL WHERE tenant_id = :legacy_tenant_id"), {"legacy_tenant_id": legacy_tenant_id})
