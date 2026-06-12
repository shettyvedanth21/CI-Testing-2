"""Bounded retention cleanup for analytics jobs and artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from src.config.settings import get_settings
from src.infrastructure.database import async_session_maker
from src.infrastructure.mysql_repository import MySQLResultRepository


logger = structlog.get_logger()


async def apply_retention(now: datetime | None = None) -> dict[str, int | str]:
    settings = get_settings()
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=max(1, int(settings.job_retention_days)))

    async with async_session_maker() as session:
        repo = MySQLResultRepository(session)
        deleted_jobs = await repo.purge_terminal_jobs_older_than(
            cutoff=cutoff,
            batch_size=settings.retention_batch_size,
        )
        deleted_artifacts = await repo.purge_expired_model_artifacts(
            now=reference,
            grace_period_hours=settings.artifact_retention_grace_hours,
            batch_size=settings.retention_batch_size,
        )

    summary = {
        "deleted_jobs": int(deleted_jobs),
        "deleted_artifacts": int(deleted_artifacts),
        "job_cutoff": cutoff.isoformat(),
    }
    logger.info("analytics_retention_applied", **summary)
    return summary
