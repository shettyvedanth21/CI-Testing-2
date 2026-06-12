"""Move dashboard snapshot payloads to MinIO-backed storage.

Revision ID: add_dashboard_snapshot_minio_storage
Revises: add_device_live_state
Create Date: 2026-03-24
"""

from alembic import op
import sqlalchemy as sa


revision = "add_dashboard_snapshot_minio_storage"
down_revision = "add_device_live_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "dashboard_snapshots" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("dashboard_snapshots")}
    if "s3_key" not in columns:
        op.add_column("dashboard_snapshots", sa.Column("s3_key", sa.String(length=512), nullable=True))
    if "storage_backend" not in columns:
        op.add_column(
            "dashboard_snapshots",
            sa.Column(
                "storage_backend",
                sa.Enum("mysql", "minio", name="dashboard_snapshot_storage_backend"),
                nullable=False,
                server_default=sa.text("'mysql'"),
            ),
        )
    payload_col = next((col for col in inspector.get_columns("dashboard_snapshots") if col["name"] == "payload_json"), None)
    if payload_col is not None and not payload_col.get("nullable", True):
        op.alter_column(
            "dashboard_snapshots",
            "payload_json",
            existing_type=sa.Text(),
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "dashboard_snapshots" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("dashboard_snapshots")}
    if "payload_json" in columns:
        op.execute("UPDATE dashboard_snapshots SET payload_json = '{}' WHERE payload_json IS NULL")
        op.alter_column(
            "dashboard_snapshots",
            "payload_json",
            existing_type=sa.Text(),
            nullable=False,
        )
    if "storage_backend" in columns:
        op.drop_column("dashboard_snapshots", "storage_backend")
    if "s3_key" in columns:
        op.drop_column("dashboard_snapshots", "s3_key")
