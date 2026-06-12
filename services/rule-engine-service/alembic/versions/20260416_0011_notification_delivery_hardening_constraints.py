"""Harden notification delivery ledger invariants and billing safety.

Revision ID: 20260416_0011_notification_delivery_hardening_constraints
Revises: 20260416_0010_notification_delivery_audit_ledger
Create Date: 2026-04-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0011_notification_delivery_hardening_constraints"
down_revision: Union[str, None] = "20260416_0010_notification_delivery_audit_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE notification_delivery_logs
        SET status = 'failed',
            failed_at = COALESCE(failed_at, attempted_at),
            billable_units = 0
        WHERE status NOT IN ('queued','attempted','provider_accepted','delivered','failed','skipped')
        """
    )
    op.execute(
        """
        UPDATE notification_delivery_logs
        SET billable_units = CASE
            WHEN status IN ('provider_accepted','delivered') THEN 1
            ELSE 0
        END
        """
    )
    connection = op.get_bind()
    missing_tenant_count = connection.execute(
        sa.text("SELECT COUNT(1) FROM notification_delivery_logs WHERE tenant_id IS NULL")
    ).scalar_one()
    if int(missing_tenant_count or 0) > 0:
        raise RuntimeError(
            "Cannot harden notification_delivery_logs: tenant_id contains NULL rows. "
            "Backfill tenant_id before applying this migration."
        )
    op.alter_column(
        "notification_delivery_logs",
        "tenant_id",
        existing_type=sa.String(length=10),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_notification_delivery_logs_valid_status",
        "notification_delivery_logs",
        "status IN ('queued','attempted','provider_accepted','delivered','failed','skipped')",
    )
    op.create_check_constraint(
        "ck_notification_delivery_logs_billable_non_negative",
        "notification_delivery_logs",
        "billable_units >= 0",
    )
    op.create_check_constraint(
        "ck_notification_delivery_logs_billable_by_status",
        "notification_delivery_logs",
        "CASE "
        "WHEN status IN ('provider_accepted','delivered') AND billable_units = 1 THEN 1 "
        "WHEN status IN ('queued','attempted','failed','skipped') AND billable_units = 0 THEN 1 "
        "ELSE 0 END = 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_notification_delivery_logs_billable_by_status", "notification_delivery_logs", type_="check")
    op.drop_constraint("ck_notification_delivery_logs_billable_non_negative", "notification_delivery_logs", type_="check")
    op.drop_constraint("ck_notification_delivery_logs_valid_status", "notification_delivery_logs", type_="check")
    op.alter_column(
        "notification_delivery_logs",
        "tenant_id",
        existing_type=sa.String(length=10),
        nullable=True,
    )
