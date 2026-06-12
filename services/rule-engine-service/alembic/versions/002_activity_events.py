"""Add activity events table for rule/alert history.

Revision ID: 002_activity_events
Revises: 001_initial
Create Date: 2026-03-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_activity_events"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=True),
        sa.Column("device_id", sa.String(length=50), nullable=True),
        sa.Column("rule_id", sa.String(length=36), nullable=True),
        sa.Column("alert_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )

    op.create_index("ix_activity_events_tenant_id", "activity_events", ["tenant_id"])
    op.create_index("ix_activity_events_device_id", "activity_events", ["device_id"])
    op.create_index("ix_activity_events_rule_id", "activity_events", ["rule_id"])
    op.create_index("ix_activity_events_alert_id", "activity_events", ["alert_id"])
    op.create_index("ix_activity_events_event_type", "activity_events", ["event_type"])
    op.create_index("ix_activity_events_is_read", "activity_events", ["is_read"])
    op.create_index("ix_activity_events_created_at", "activity_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_activity_events_created_at", table_name="activity_events")
    op.drop_index("ix_activity_events_is_read", table_name="activity_events")
    op.drop_index("ix_activity_events_event_type", table_name="activity_events")
    op.drop_index("ix_activity_events_alert_id", table_name="activity_events")
    op.drop_index("ix_activity_events_rule_id", table_name="activity_events")
    op.drop_index("ix_activity_events_device_id", table_name="activity_events")
    op.drop_index("ix_activity_events_tenant_id", table_name="activity_events")
    op.drop_table("activity_events")
