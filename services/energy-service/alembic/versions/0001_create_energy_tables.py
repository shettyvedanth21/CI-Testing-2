"""create energy projection tables"""

from alembic import op
import sqlalchemy as sa

revision = "0001_create_energy_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "energy_device_state",
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("session_state", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("last_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_energy_counter", sa.Float(), nullable=True),
        sa.Column("last_power_kw", sa.Float(), nullable=True),
        sa.Column("last_day_bucket", sa.Date(), nullable=True),
        sa.Column("last_month_bucket", sa.Date(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("device_id"),
    )

    op.create_table(
        "energy_device_day",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("energy_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("energy_cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("idle_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("offhours_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("overconsumption_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loss_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loss_cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quality_flags", sa.Text(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "day", name="uq_energy_device_day"),
    )
    op.create_index("ix_energy_device_day_day", "energy_device_day", ["day"])
    op.create_index("ix_energy_device_day_version", "energy_device_day", ["version"])
    op.create_index("ix_energy_device_day_device_id", "energy_device_day", ["device_id"])

    op.create_table(
        "energy_device_month",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("energy_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("energy_cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("idle_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("offhours_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("overconsumption_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loss_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loss_cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quality_flags", sa.Text(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "month", name="uq_energy_device_month"),
    )
    op.create_index("ix_energy_device_month_month", "energy_device_month", ["month"])
    op.create_index("ix_energy_device_month_version", "energy_device_month", ["version"])
    op.create_index("ix_energy_device_month_device_id", "energy_device_month", ["device_id"])

    op.create_table(
        "energy_fleet_day",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("energy_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("energy_cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("idle_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("offhours_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("overconsumption_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loss_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loss_cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("day"),
    )
    op.create_index("ix_energy_fleet_day_version", "energy_fleet_day", ["version"])

    op.create_table(
        "energy_fleet_month",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("energy_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("energy_cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("idle_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("offhours_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("overconsumption_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loss_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loss_cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("month"),
    )
    op.create_index("ix_energy_fleet_month_version", "energy_fleet_month", ["version"])

    op.create_table(
        "energy_reconcile_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("expected_energy_kwh", sa.Float(), nullable=False),
        sa.Column("projected_energy_kwh", sa.Float(), nullable=False),
        sa.Column("drift_kwh", sa.Float(), nullable=False),
        sa.Column("repaired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_energy_reconcile_audit_device_id", "energy_reconcile_audit", ["device_id"])
    op.create_index("ix_energy_reconcile_audit_day", "energy_reconcile_audit", ["day"])


def downgrade() -> None:
    op.drop_index("ix_energy_reconcile_audit_day", table_name="energy_reconcile_audit")
    op.drop_index("ix_energy_reconcile_audit_device_id", table_name="energy_reconcile_audit")
    op.drop_table("energy_reconcile_audit")

    op.drop_index("ix_energy_fleet_month_version", table_name="energy_fleet_month")
    op.drop_table("energy_fleet_month")

    op.drop_index("ix_energy_fleet_day_version", table_name="energy_fleet_day")
    op.drop_table("energy_fleet_day")

    op.drop_index("ix_energy_device_month_device_id", table_name="energy_device_month")
    op.drop_index("ix_energy_device_month_version", table_name="energy_device_month")
    op.drop_index("ix_energy_device_month_month", table_name="energy_device_month")
    op.drop_table("energy_device_month")

    op.drop_index("ix_energy_device_day_device_id", table_name="energy_device_day")
    op.drop_index("ix_energy_device_day_version", table_name="energy_device_day")
    op.drop_index("ix_energy_device_day_day", table_name="energy_device_day")
    op.drop_table("energy_device_day")

    op.drop_table("energy_device_state")
