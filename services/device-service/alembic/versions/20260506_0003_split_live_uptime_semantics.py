"""split live uptime semantics

Revision ID: 20260506_0003_split_live_uptime_semantics
Revises: 20260506_0002_add_recent_telemetry_samples
Create Date: 2026-05-06 16:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260506_0003_split_live_uptime_semantics"
down_revision = "20260506_0002_add_recent_telemetry_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device_live_state", sa.Column("today_uptime_percentage", sa.Float(), nullable=True))
    op.add_column("device_live_state", sa.Column("current_shift_uptime_percentage", sa.Float(), nullable=True))
    op.execute(
        """
        UPDATE device_live_state
        SET today_uptime_percentage = uptime_percentage
        WHERE uptime_percentage IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("device_live_state", "current_shift_uptime_percentage")
    op.drop_column("device_live_state", "today_uptime_percentage")
