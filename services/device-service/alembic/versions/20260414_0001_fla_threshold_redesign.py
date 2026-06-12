"""Add FLA-based threshold fields and backfill from legacy over-threshold.

Revision ID: 20260414_0001_fla_threshold_redesign
Revises: 20260413_0002_drop_legacy_health_bounds
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260414_0001_fla_threshold_redesign"
down_revision = "20260413_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    device_cols = {c["name"] for c in inspector.get_columns("devices")}

    if "full_load_current_a" not in device_cols:
        op.add_column("devices", sa.Column("full_load_current_a", sa.Numeric(10, 4), nullable=True))

    if "idle_threshold_pct_of_fla" not in device_cols:
        op.add_column(
            "devices",
            sa.Column(
                "idle_threshold_pct_of_fla",
                sa.Numeric(6, 4),
                nullable=False,
                server_default="0.2500",
            ),
        )

    op.execute(
        sa.text(
            """
            UPDATE devices
            SET full_load_current_a = overconsumption_current_threshold_a
            WHERE full_load_current_a IS NULL
              AND overconsumption_current_threshold_a IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE devices
            SET idle_threshold_pct_of_fla = 0.2500
            WHERE idle_threshold_pct_of_fla IS NULL
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    device_cols = {c["name"] for c in inspector.get_columns("devices")}

    if "idle_threshold_pct_of_fla" in device_cols:
        op.drop_column("devices", "idle_threshold_pct_of_fla")
    if "full_load_current_a" in device_cols:
        op.drop_column("devices", "full_load_current_a")
