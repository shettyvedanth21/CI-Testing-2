"""Compatibility bridge for previously-applied 0002_add_baseline_shadow_columns.

This repo state was rolled back to the older FLA-era application logic while
some local databases were already stamped at revision
``0002_add_baseline_shadow_columns`` from a later baseline experimentation
branch. Energy-service startup runs ``alembic upgrade head`` during its
migration guard, so the missing revision causes the service to fail before
boot.

This bridge intentionally preserves that revision ID as a no-op migration so
the current codebase can start cleanly against already-upgraded local
databases. Extra columns that may still exist remain harmless for the reverted
app logic, and no data is destroyed.
"""


# revision identifiers, used by Alembic.
revision = "0002_add_baseline_shadow_columns"
down_revision = "0001_create_energy_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op compatibility bridge. Local databases may already contain the
    # baseline-era schema additions tied to this revision.
    pass


def downgrade() -> None:
    # No-op downgrade to avoid destructive rollback of local data that may
    # still reference baseline-era columns.
    pass
