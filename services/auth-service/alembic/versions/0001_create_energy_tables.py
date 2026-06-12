"""Compatibility shim for the pre-existing shared DB stamp.

Revision ID: 0001_create_energy_tables
Revises:
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_create_energy_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
