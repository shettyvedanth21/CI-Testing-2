"""Add first-class tenant columns for analytics jobs/artifacts.

Revision ID: 0007_first_class_tenant_columns
Revises: 0006_fleet_dispatch_orchestration
Create Date: 2026-04-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "0007_first_class_tenant_columns"
down_revision = "0006_fleet_dispatch_orchestration"
branch_labels = None
depends_on = None


def _has_index(indexes: list[dict[str, object]], name: str) -> bool:
    return any(index.get("name") == name for index in indexes)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    job_columns = {column["name"] for column in inspector.get_columns("analytics_jobs")}
    artifact_columns = {column["name"] for column in inspector.get_columns("ml_model_artifacts")}

    if "tenant_id" not in job_columns:
        op.add_column("analytics_jobs", sa.Column("tenant_id", sa.String(length=10), nullable=True))
    if "tenant_id" not in artifact_columns:
        op.add_column("ml_model_artifacts", sa.Column("tenant_id", sa.String(length=10), nullable=True))

    bind.execute(
        text(
            """
            UPDATE analytics_jobs
            SET tenant_id = JSON_UNQUOTE(JSON_EXTRACT(parameters, '$.tenant_id'))
            WHERE tenant_id IS NULL
              AND JSON_EXTRACT(parameters, '$.tenant_id') IS NOT NULL
            """
        )
    )

    bind.execute(
        text(
            """
            UPDATE ml_model_artifacts artifacts
            JOIN (
                SELECT
                    device_id,
                    analysis_type,
                    MIN(tenant_id) AS tenant_id
                FROM analytics_jobs
                WHERE tenant_id IS NOT NULL
                GROUP BY device_id, analysis_type
                HAVING COUNT(DISTINCT tenant_id) = 1
            ) jobs
              ON jobs.device_id = artifacts.device_id
             AND jobs.analysis_type = artifacts.analysis_type
            SET artifacts.tenant_id = jobs.tenant_id
            WHERE artifacts.tenant_id IS NULL
            """
        )
    )

    bind.execute(
        text(
            """
            UPDATE ml_model_artifacts artifacts
            JOIN (
                SELECT
                    device_id,
                    analysis_type,
                    model_name AS model_key,
                    MIN(tenant_id) AS tenant_id
                FROM analytics_jobs
                WHERE tenant_id IS NOT NULL
                GROUP BY device_id, analysis_type, model_name
                HAVING COUNT(DISTINCT tenant_id) = 1
            ) jobs
              ON jobs.device_id = artifacts.device_id
             AND jobs.analysis_type = artifacts.analysis_type
             AND jobs.model_key = artifacts.model_key
            SET artifacts.tenant_id = jobs.tenant_id
            WHERE artifacts.tenant_id IS NULL
            """
        )
    )

    job_indexes = inspector.get_indexes("analytics_jobs")
    if not _has_index(job_indexes, "ix_analytics_jobs_tenant_id"):
        op.create_index("ix_analytics_jobs_tenant_id", "analytics_jobs", ["tenant_id"], unique=False)
    if not _has_index(job_indexes, "idx_analytics_jobs_tenant_status_created"):
        op.create_index(
            "idx_analytics_jobs_tenant_status_created",
            "analytics_jobs",
            ["tenant_id", "status", "created_at"],
            unique=False,
        )

    artifact_indexes = inspector.get_indexes("ml_model_artifacts")
    if not _has_index(artifact_indexes, "ix_ml_model_artifacts_tenant_id"):
        op.create_index("ix_ml_model_artifacts_tenant_id", "ml_model_artifacts", ["tenant_id"], unique=False)
    if _has_index(artifact_indexes, "idx_ml_artifacts_lookup"):
        op.drop_index("idx_ml_artifacts_lookup", table_name="ml_model_artifacts")
    op.create_index(
        "idx_ml_artifacts_lookup",
        "ml_model_artifacts",
        ["tenant_id", "device_id", "analysis_type", "model_key"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    job_columns = {column["name"] for column in inspector.get_columns("analytics_jobs")}
    artifact_columns = {column["name"] for column in inspector.get_columns("ml_model_artifacts")}

    artifact_indexes = inspector.get_indexes("ml_model_artifacts")
    if _has_index(artifact_indexes, "idx_ml_artifacts_lookup"):
        op.drop_index("idx_ml_artifacts_lookup", table_name="ml_model_artifacts")
    op.create_index(
        "idx_ml_artifacts_lookup",
        "ml_model_artifacts",
        ["device_id", "analysis_type", "model_key"],
        unique=False,
    )
    if _has_index(artifact_indexes, "ix_ml_model_artifacts_tenant_id"):
        op.drop_index("ix_ml_model_artifacts_tenant_id", table_name="ml_model_artifacts")
    if "tenant_id" in artifact_columns:
        op.drop_column("ml_model_artifacts", "tenant_id")

    job_indexes = inspector.get_indexes("analytics_jobs")
    if _has_index(job_indexes, "idx_analytics_jobs_tenant_status_created"):
        op.drop_index("idx_analytics_jobs_tenant_status_created", table_name="analytics_jobs")
    if _has_index(job_indexes, "ix_analytics_jobs_tenant_id"):
        op.drop_index("ix_analytics_jobs_tenant_id", table_name="analytics_jobs")
    if "tenant_id" in job_columns:
        op.drop_column("analytics_jobs", "tenant_id")
