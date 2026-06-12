"""Bounded retention for export checkpoints and artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from logging_config import get_logger


logger = get_logger(__name__)


async def apply_checkpoint_retention(worker, *, now: datetime | None = None) -> dict[str, int | str]:
    settings = worker.settings
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=max(1, int(settings.checkpoint_retention_days)))
    rows = await worker.checkpoint_store.list_checkpoints_for_retention(
        updated_before=cutoff,
        limit=settings.checkpoint_retention_batch_size,
    )

    deleted_artifacts = 0
    checkpoint_ids: list[int] = []
    for row in rows:
        checkpoint_id = int(row["id"])
        s3_key = row.get("s3_key")
        checkpoint_ids.append(checkpoint_id)
        if s3_key and await worker.checkpoint_store.is_latest_reference_for_key(checkpoint_id=checkpoint_id, s3_key=s3_key):
            await worker.s3_writer.delete_object_if_exists(s3_key)
            deleted_artifacts += 1

    deleted_rows = await worker.checkpoint_store.delete_checkpoints_by_ids(checkpoint_ids)
    summary = {
        "deleted_rows": int(deleted_rows),
        "deleted_artifacts": int(deleted_artifacts),
        "cutoff": cutoff.isoformat(),
    }
    logger.info("export_checkpoint_retention_applied", extra=summary)
    return summary
