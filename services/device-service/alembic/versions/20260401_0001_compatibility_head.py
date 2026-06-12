"""Compatibility head for device-service legacy databases.

Revision ID: 20260401_0001
Revises: 20260331_0003_add_tenant_security_audit_log
Create Date: 2026-04-01

This revision intentionally carries no schema changes. It preserves startup
compatibility for databases that were already stamped with 20260401_0001 by a
prior deployment or simulator run.
"""

from typing import Sequence, Union

# Alembic identifiers.
revision = "20260401_0001"
down_revision = "20260331_0003_add_tenant_security_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op on purpose: this file exists only to restore Alembic continuity.
    pass


def downgrade() -> None:
    # No-op on purpose: downgrading past the compatibility head is not needed.
    pass
