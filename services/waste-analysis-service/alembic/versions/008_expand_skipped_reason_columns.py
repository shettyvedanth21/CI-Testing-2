"""Compatibility bridge for previously-applied 008_expand_skipped_reason_columns.

This repo state was rolled back to an older FLA-era application path while
some local databases were already stamped at revision
``008_expand_skipped_reason_columns`` from a later experimentation branch.
Waste-analysis startup runs ``alembic upgrade head`` during its migration
guard, so the missing revision causes the service to fail before boot.

This bridge intentionally keeps that revision ID available as a no-op
migration so the reverted code can start safely against already-upgraded local
databases. Existing columns remain harmless for the older runtime logic, and
no data is destroyed.
"""

from typing import Sequence, Union


revision: str = "008_expand_skipped_reason_columns"
down_revision: Union[str, None] = "006_sh_tenant_id_hard_cut"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op compatibility bridge. Local databases may already include the
    # schema changes tied to this revision.
    pass


def downgrade() -> None:
    # No-op downgrade to avoid destructive rollback of local data.
    pass
