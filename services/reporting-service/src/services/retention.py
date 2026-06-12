from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.config import settings
from src.database import AsyncSessionLocal
from src.repositories.report_repository import ReportRepository
from src.storage.minio_client import minio_client


logger = logging.getLogger(__name__)


async def apply_report_retention(now: datetime | None = None) -> dict[str, int | str]:
    reference = now or datetime.now(timezone.utc)
    cutoff = (reference - timedelta(days=max(1, int(settings.REPORT_RETENTION_DAYS)))).replace(tzinfo=None)

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        rows = await repo.list_reports_for_retention(
            cutoff=cutoff,
            limit=settings.REPORT_RETENTION_BATCH_SIZE,
        )

        report_ids = [report_id for report_id, _ in rows]
        deleted_artifacts = 0
        for _, s3_key in rows:
            if not s3_key:
                continue
            await minio_client.async_delete_file_if_exists(s3_key)
            deleted_artifacts += 1

        deleted_rows = await repo.delete_reports_by_ids(report_ids)

    summary = {
        "deleted_rows": int(deleted_rows),
        "deleted_artifacts": int(deleted_artifacts),
        "cutoff": cutoff.isoformat(),
    }
    logger.info("report_retention_applied", extra=summary)
    return summary
