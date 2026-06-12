"""Add composite indexes for rules/alerts performance.

Revision ID: 004_add_rule_alert_indexes
Revises: 003_rules_v2_time_based_and_cooldown
Create Date: 2026-03-08
"""

from alembic import op
from sqlalchemy import inspect


revision = "004_add_rule_alert_indexes"
down_revision = "003_rules_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    rule_indexes = {idx["name"] for idx in inspector.get_indexes("rules")}
    if "idx_rules_status_scope" not in rule_indexes:
        op.create_index("idx_rules_status_scope", "rules", ["status", "scope"])

    alert_indexes = {idx["name"] for idx in inspector.get_indexes("alerts")}
    if "idx_alerts_device_created" not in alert_indexes:
        op.create_index("idx_alerts_device_created", "alerts", ["device_id", "created_at"])
    if "idx_alerts_rule_device_created" not in alert_indexes:
        op.create_index("idx_alerts_rule_device_created", "alerts", ["rule_id", "device_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    alert_indexes = {idx["name"] for idx in inspector.get_indexes("alerts")}
    if "idx_alerts_rule_device_created" in alert_indexes:
        op.drop_index("idx_alerts_rule_device_created", table_name="alerts")
    if "idx_alerts_device_created" in alert_indexes:
        op.drop_index("idx_alerts_device_created", table_name="alerts")

    rule_indexes = {idx["name"] for idx in inspector.get_indexes("rules")}
    if "idx_rules_status_scope" in rule_indexes:
        op.drop_index("idx_rules_status_scope", table_name="rules")
