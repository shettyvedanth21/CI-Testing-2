from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from services.shared.tenant_context import TenantContext
from src.config import settings
from src.database import AsyncSessionLocal
from src.repositories import WasteRepository
from src.storage.minio_client import minio_client


logger = logging.getLogger(__name__)


async def apply_waste_retention(now: datetime | None = None) -> dict[str, int | str]:
    reference = now or datetime.now(timezone.utc)
    cutoff = (reference - timedelta(days=max(1, int(settings.WASTE_RETENTION_DAYS)))).replace(tzinfo=None)

    async with AsyncSessionLocal() as session:
        repo = WasteRepository(session, TenantContext.system("svc:waste-retention"))
        rows = await repo.list_jobs_for_retention(
            cutoff=cutoff,
            limit=settings.WASTE_RETENTION_BATCH_SIZE,
        )

        job_ids = [job_id for job_id, _ in rows]
        deleted_artifacts = 0
        for _, s3_key in rows:
            if not s3_key:
                continue
            await minio_client.async_delete_file_if_exists(s3_key)
            deleted_artifacts += 1

        deleted_rows = await repo.delete_jobs_by_ids(job_ids)

    summary = {
        "deleted_rows": int(deleted_rows),
        "deleted_artifacts": int(deleted_artifacts),
        "cutoff": cutoff.isoformat(),
    }
    logger.info("waste_retention_applied", extra=summary)
    return summary
