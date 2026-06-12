"""Add first-class tenant scope to waste analysis jobs.

Revision ID: 005_add_tenant_scope_to_waste_jobs
Revises: 004_add_wastage_categories
Create Date: 2026-04-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = "005_add_tenant_scope_to_waste_jobs"
down_revision: Union[str, None] = "004_add_wastage_categories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    columns = {col["name"] for col in inspector.get_columns("waste_analysis_jobs")}
    if "tenant_id" not in columns:
        op.add_column("waste_analysis_jobs", sa.Column("tenant_id", sa.String(length=50), nullable=True))

    # Backfill from legacy metadata where present.
    bind.execute(
        text(
            """
            UPDATE waste_analysis_jobs
            SET tenant_id = JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.tenant_id'))
            WHERE tenant_id IS NULL
              AND JSON_EXTRACT(result_json, '$.tenant_id') IS NOT NULL
            """
        )
    )

    # Backfill using the first selected device, which is sufficient for historical jobs in this service.
    if "devices" in table_names:
        bind.execute(
            text(
                """
                UPDATE waste_analysis_jobs AS j
                JOIN devices AS d
                  ON d.device_id = JSON_UNQUOTE(JSON_EXTRACT(j.device_ids, '$[0]'))
                SET j.tenant_id = d.tenant_id
                WHERE j.tenant_id IS NULL
                  AND JSON_LENGTH(j.device_ids) > 0
                """
            )
        )

    # Backfill any remaining completed jobs from persisted device summaries.
    if "devices" in table_names and "waste_device_summary" in table_names:
        bind.execute(
            text(
                """
                UPDATE waste_analysis_jobs AS j
                JOIN (
                    SELECT
                        s.job_id,
                        MIN(d.tenant_id) AS tenant_id,
                        COUNT(DISTINCT d.tenant_id) AS tenant_count
                    FROM waste_device_summary AS s
                    JOIN devices AS d ON d.device_id = s.device_id
                    GROUP BY s.job_id
                ) AS inferred
                  ON inferred.job_id = j.id
                SET j.tenant_id = inferred.tenant_id
                WHERE j.tenant_id IS NULL
                  AND inferred.tenant_count = 1
                """
            )
        )

    unresolved = bind.execute(
        text("SELECT COUNT(*) FROM waste_analysis_jobs WHERE tenant_id IS NULL")
    ).scalar_one()
    if int(unresolved or 0) != 0:
        raise RuntimeError(
            f"Unable to backfill tenant scope for {int(unresolved)} waste analysis job(s)."
        )

    op.alter_column(
        "waste_analysis_jobs",
        "tenant_id",
        existing_type=sa.String(length=50),
        nullable=False,
    )

    indexes = {idx["name"] for idx in inspector.get_indexes("waste_analysis_jobs")}
    if "idx_waste_jobs_duplicate_lookup" in indexes:
        op.drop_index("idx_waste_jobs_duplicate_lookup", table_name="waste_analysis_jobs")
    if "idx_waste_jobs_status_created" in indexes:
        op.drop_index("idx_waste_jobs_status_created", table_name="waste_analysis_jobs")

    refreshed = inspect(bind)
    refreshed_indexes = {idx["name"] for idx in refreshed.get_indexes("waste_analysis_jobs")}
    if "idx_waste_jobs_history_tenant_created" not in refreshed_indexes:
        op.create_index(
            "idx_waste_jobs_history_tenant_created",
            "waste_analysis_jobs",
            ["tenant_id", "created_at"],
        )
    if "idx_waste_jobs_tenant_duplicate_lookup" not in refreshed_indexes:
        op.create_index(
            "idx_waste_jobs_tenant_duplicate_lookup",
            "waste_analysis_jobs",
            ["tenant_id", "status", "scope", "start_date", "end_date", "granularity"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("waste_analysis_jobs")}

    if "idx_waste_jobs_tenant_duplicate_lookup" in indexes:
        op.drop_index("idx_waste_jobs_tenant_duplicate_lookup", table_name="waste_analysis_jobs")
    if "idx_waste_jobs_history_tenant_created" in indexes:
        op.drop_index("idx_waste_jobs_history_tenant_created", table_name="waste_analysis_jobs")
    if "idx_waste_jobs_status_created" not in indexes:
        op.create_index("idx_waste_jobs_status_created", "waste_analysis_jobs", ["status", "created_at"])

    columns = {col["name"] for col in inspector.get_columns("waste_analysis_jobs")}
    if "tenant_id" in columns:
        op.drop_column("waste_analysis_jobs", "tenant_id")
