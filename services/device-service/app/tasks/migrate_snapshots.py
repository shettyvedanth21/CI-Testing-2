"""One-time migration task for dashboard snapshots."""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from datetime import timezone
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.device import DashboardSnapshot
from app.services.dashboard import DashboardService

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
BATCH_SLEEP_SECONDS = 0.1


def _snapshot_payload_bytes(payload_json: str) -> BytesIO:
    return BytesIO(payload_json.encode("utf-8"))


async def migrate_snapshots_to_minio(session_factory: Any = AsyncSessionLocal) -> dict[str, int]:
    """Move existing MySQL-backed dashboard snapshot payloads to MinIO."""

    if not settings.MIGRATE_SNAPSHOTS_TO_MINIO:
        return {"processed": 0, "migrated": 0, "failed": 0}

    processed = 0
    migrated = 0
    failed = 0

    try:
        DashboardService._ensure_snapshot_bucket()
    except Exception as exc:
        logger.error("snapshot_migration_bucket_init_failed", extra={"error": str(exc)})
        return {"processed": 0, "migrated": 0, "failed": 0}

    while True:
        async with session_factory() as session:
            result = await session.execute(
                select(DashboardSnapshot)
                .where(
                    DashboardSnapshot.storage_backend == "mysql",
                    DashboardSnapshot.payload_json.is_not(None),
                )
                .order_by(DashboardSnapshot.tenant_id.asc(), DashboardSnapshot.snapshot_key.asc())
                .limit(BATCH_SIZE)
            )
            rows = list(result.scalars().all())
            if not rows:
                break

            for row in rows:
                processed += 1
                try:
                    generated_at = row.generated_at
                    if generated_at.tzinfo is None:
                        generated_at = generated_at.replace(tzinfo=timezone.utc)
                    object_key = DashboardService._snapshot_object_key(
                        row.tenant_id,
                        row.snapshot_key,
                        generated_at,
                    )
                    payload_bytes = _snapshot_payload_bytes(row.payload_json or "")
                    client = DashboardService._get_snapshot_minio_client()
                    client.put_object(
                        settings.SNAPSHOT_MINIO_BUCKET,
                        object_key,
                        payload_bytes,
                        len(payload_bytes.getbuffer()),
                        content_type="application/json",
                    )
                    row.s3_key = object_key
                    row.storage_backend = "minio"
                    row.payload_json = None
                    migrated += 1
                except Exception as exc:
                        failed += 1
                        logger.error(
                            "snapshot_migration_row_failed",
                            extra={
                                "tenant_id": row.tenant_id,
                                "snapshot_key": row.snapshot_key,
                                "error": str(exc),
                            },
                        )

            await session.commit()

        if processed and processed % 100 == 0:
            logger.info(
                "snapshot_migration_progress",
                extra={"processed": processed, "migrated": migrated, "failed": failed},
            )

        if len(rows) < BATCH_SIZE:
            break

        await asyncio.sleep(BATCH_SLEEP_SECONDS)

    logger.info(
        "snapshot_migration_complete",
        extra={"processed": processed, "migrated": migrated, "failed": failed},
    )
    return {"processed": processed, "migrated": migrated, "failed": failed}
