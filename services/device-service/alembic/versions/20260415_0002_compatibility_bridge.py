"""Compatibility bridge for previously-applied 20260415_0002 revision.

This repo state was rolled back to the FLA-era application logic while some
local databases were already stamped at revision ``20260415_0002`` from a
later experimentation branch. Device-service startup runs ``alembic upgrade
head`` during its migration guard, so the missing revision causes the service
to fail before boot.

This bridge intentionally preserves that revision ID as a no-op migration so
the current codebase can start cleanly against already-upgraded local
databases. The extra columns/tables remain harmless for the reverted app
logic, and no data is destroyed.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260415_0002"
down_revision = "20260414_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op compatibility bridge. The schema changes tied to this revision may
    # already exist in local databases; the reverted FLA-era code does not
    # require any additional DDL here.
    pass


def downgrade() -> None:
    # No-op downgrade to avoid destructive rollback of columns/tables that may
    # still be referenced by historical local data.
    pass
