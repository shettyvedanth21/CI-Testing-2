"""Machine degradation score foundation tables.

Revision ID: 20260521_0001_machine_degradation_score
Revises: 20260506_0003_split_live_uptime_semantics
Create Date: 2026-05-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260521_0001_machine_degradation_score"
down_revision = "20260506_0003_split_live_uptime_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_health_feature_windows" not in existing_tables:
        op.create_table(
            "machine_health_feature_windows",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=10), nullable=False),
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("running_state", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
            sa.Column("current_avg_mean", sa.Float(), nullable=True),
            sa.Column("current_avg_std", sa.Float(), nullable=True),
            sa.Column("current_avg_p95", sa.Float(), nullable=True),
            sa.Column("current_l1_mean", sa.Float(), nullable=True),
            sa.Column("current_l2_mean", sa.Float(), nullable=True),
            sa.Column("current_l3_mean", sa.Float(), nullable=True),
            sa.Column("power_mean", sa.Float(), nullable=True),
            sa.Column("power_p95", sa.Float(), nullable=True),
            sa.Column("power_factor_mean", sa.Float(), nullable=True),
            sa.Column("voltage_avg_mean", sa.Float(), nullable=True),
            sa.Column("voltage_imbalance", sa.Float(), nullable=True),
            sa.Column("phase_imbalance", sa.Float(), nullable=True),
            sa.Column("frequency_mean", sa.Float(), nullable=True),
            sa.Column("energy_kwh", sa.Float(), nullable=True),
            sa.Column("telemetry_coverage", sa.Float(), nullable=True),
            sa.Column("sample_count", sa.Integer(), nullable=True),
            sa.Column("excluded_reason", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("tenant_id", "device_id", "window_start", name="uq_mhfw_tenant_device_window"),
            sa.CheckConstraint(
                "telemetry_coverage >= 0 AND telemetry_coverage <= 1",
                name="ck_mhfw_coverage_range",
            ),
            sa.CheckConstraint(
                "running_state IN ('OFF','STARTUP','STEADY_RUNNING','LOAD_CHANGE','SHUTDOWN','UNKNOWN')",
                name="ck_mhfw_running_state",
            ),
        )
        op.create_index("ix_mhfw_tenant_device_start", "machine_health_feature_windows", ["tenant_id", "device_id", "window_start"])
        op.create_index("ix_mhfw_tenant_start", "machine_health_feature_windows", ["tenant_id", "window_start"])
        op.create_index("ix_mhfw_device_start", "machine_health_feature_windows", ["device_id", "window_start"])

    if "machine_health_baselines" not in existing_tables:
        op.create_table(
            "machine_health_baselines",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=10), nullable=False),
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("baseline_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="candidate"),
            sa.Column("current_avg_mean", sa.Float(), nullable=True),
            sa.Column("current_avg_std", sa.Float(), nullable=True),
            sa.Column("power_mean", sa.Float(), nullable=True),
            sa.Column("power_p95", sa.Float(), nullable=True),
            sa.Column("power_factor_mean", sa.Float(), nullable=True),
            sa.Column("voltage_avg_mean", sa.Float(), nullable=True),
            sa.Column("phase_imbalance_mean", sa.Float(), nullable=True),
            sa.Column("frequency_mean", sa.Float(), nullable=True),
            sa.Column("quality_score", sa.Float(), nullable=True),
            sa.Column("quality_band", sa.String(length=16), nullable=True),
            sa.Column("signal_completeness", sa.Float(), nullable=True),
            sa.Column("steady_running_coverage", sa.Float(), nullable=True),
            sa.Column("learning_window_count", sa.Integer(), nullable=True),
            sa.Column("learned_from_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("learned_from_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.CheckConstraint(
                "status IN ('active','candidate','retired')",
                name="ck_mhb_status",
            ),
            sa.CheckConstraint(
                "quality_score >= 0 AND quality_score <= 1",
                name="ck_mhb_quality_score_range",
            ),
        )
        op.create_index("ix_mhb_tenant_device_status", "machine_health_baselines", ["tenant_id", "device_id", "status"])
        op.create_index("ix_mhb_tenant_status", "machine_health_baselines", ["tenant_id", "status"])

    if "machine_health_latest" not in existing_tables:
        op.create_table(
            "machine_health_latest",
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("tenant_id", sa.String(length=10), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("baseline_version", sa.Integer(), nullable=True),
            sa.Column("baseline_quality", sa.String(length=16), nullable=True),
            sa.Column("top_reasons_json", sa.Text(), nullable=True),
            sa.Column("contributions_json", sa.Text(), nullable=True),
            sa.Column("phase_status", sa.String(length=32), nullable=True),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_window_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_window_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("worker_version", sa.String(length=32), nullable=True, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("device_id", "tenant_id"),
            sa.CheckConstraint(
                "score >= 1 AND score <= 10",
                name="ck_mhl_score_range",
            ),
            sa.CheckConstraint(
                "confidence >= 0 AND confidence <= 1",
                name="ck_mhl_confidence_range",
            ),
            sa.CheckConstraint(
                "status IN ('healthy','watch','warning','critical','learning','unavailable')",
                name="ck_mhl_status_values",
            ),
        )
        op.create_index("ix_mhl_tenant_status", "machine_health_latest", ["tenant_id", "status"])

    if "machine_health_history" not in existing_tables:
        op.create_table(
            "machine_health_history",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=10), nullable=False),
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("baseline_version", sa.Integer(), nullable=True),
            sa.Column("contributions_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("tenant_id", "device_id", "computed_at", name="uq_mhh_tenant_device_time"),
            sa.CheckConstraint(
                "score >= 1 AND score <= 10",
                name="ck_mhh_score_range",
            ),
            sa.CheckConstraint(
                "confidence >= 0 AND confidence <= 1",
                name="ck_mhh_confidence_range",
            ),
        )
        op.create_index("ix_mhh_tenant_device_time", "machine_health_history", ["tenant_id", "device_id", "computed_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_health_history" in existing_tables:
        op.drop_index("ix_mhh_tenant_device_time", table_name="machine_health_history")
        op.drop_table("machine_health_history")

    if "machine_health_latest" in existing_tables:
        op.drop_index("ix_mhl_tenant_status", table_name="machine_health_latest")
        op.drop_table("machine_health_latest")

    if "machine_health_baselines" in existing_tables:
        op.drop_index("ix_mhb_tenant_status", table_name="machine_health_baselines")
        op.drop_index("ix_mhb_tenant_device_status", table_name="machine_health_baselines")
        op.drop_table("machine_health_baselines")

    if "machine_health_feature_windows" in existing_tables:
        op.drop_index("ix_mhfw_device_start", table_name="machine_health_feature_windows")
        op.drop_index("ix_mhfw_tenant_start", table_name="machine_health_feature_windows")
        op.drop_index("ix_mhfw_tenant_device_start", table_name="machine_health_feature_windows")
        op.drop_table("machine_health_feature_windows")
