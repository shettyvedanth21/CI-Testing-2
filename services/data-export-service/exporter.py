"""Core export logic for telemetry data.

Orchestrates the export pipeline: querying data, writing to S3,
and tracking checkpoints with idempotency guarantees.
"""

import time
from datetime import datetime, timedelta, timezone

from config import Settings
from checkpoint import CheckpointRepository
from data_source import DataSourceClient
from logging_config import get_logger
from models import Checkpoint, ExportBatch, ExportFormat, ExportResult, ExportStatus
from s3_writer import S3Writer

logger = get_logger(__name__)


class TelemetryExporter:
    """Core exporter that coordinates data export pipeline."""

    def __init__(
        self,
        settings: Settings,
        data_source: DataSourceClient,
        s3_writer: S3Writer,
        checkpoint_repo: CheckpointRepository,
    ):
        self.settings = settings
        self.data_source = data_source
        self.s3_writer = s3_writer
        self.checkpoint_repo = checkpoint_repo

    async def export_device_data(
        self,
        device_id: str,
        force_full: bool = False,
        force_start_time: datetime | None = None,
        force_end_time: datetime | None = None,
    ) -> ExportResult:

        wall_start = time.time()

        try:
            # -------------------------------------------------------
            # Decide export window
            # -------------------------------------------------------
            if force_start_time is not None and force_end_time is not None:
                start_time_export = (
                    force_start_time.replace(tzinfo=timezone.utc)
                    if force_start_time.tzinfo is None
                    else force_start_time.astimezone(timezone.utc)
                )
                end_time_export = (
                    force_end_time.replace(tzinfo=timezone.utc)
                    if force_end_time.tzinfo is None
                    else force_end_time.astimezone(timezone.utc)
                )
                if end_time_export <= start_time_export:
                    raise ValueError("end_time must be after start_time")
                window_hours = (end_time_export - start_time_export).total_seconds() / 3600.0
                if window_hours > self.settings.max_force_export_window_hours:
                    raise ValueError(
                        f"Requested export window exceeds max_force_export_window_hours="
                        f"{self.settings.max_force_export_window_hours}"
                    )
            elif force_full:
                start_time_export = datetime.now(timezone.utc) - timedelta(
                    hours=self.settings.max_export_window_hours
                )
                end_time_export = datetime.now(timezone.utc)
            else:
                checkpoint = await self.checkpoint_repo.get_last_checkpoint(device_id)
                if checkpoint and checkpoint.status == ExportStatus.COMPLETED:
                    start_time_export = checkpoint.last_exported_at
                else:
                    start_time_export = datetime.now(timezone.utc) - timedelta(
                        hours=self.settings.lookback_hours
                    )
                end_time_export = datetime.now(timezone.utc)

            # -------------------------------------------------------
            # Check if there is data
            # -------------------------------------------------------
            record_count = await self.data_source.count_records(
                device_id, start_time_export, end_time_export
            )

            export_format = ExportFormat(self.settings.export_format)
            force_range = force_start_time is not None and force_end_time is not None

            if record_count == 0:
                if force_range:
                    checkpoint = Checkpoint(
                        device_id=device_id,
                        last_exported_at=end_time_export,
                        status=ExportStatus.FAILED,
                        record_count=0,
                        error_message="No telemetry found in selected range",
                    )
                    await self.checkpoint_repo.save_checkpoint(checkpoint)
                logger.info(
                    "No new data to export for device",
                    extra={"device_id": device_id}
                )
                return ExportResult(
                    success=True,
                    device_id=device_id,
                    start_time=start_time_export,
                    end_time=end_time_export,
                    record_count=0,
                    format=export_format,
                    duration_seconds=time.time() - wall_start,
                )

            # -------------------------------------------------------
            # Query telemetry
            # -------------------------------------------------------
            records = await self.data_source.query_telemetry(
                device_id=device_id,
                start_time=start_time_export,
                end_time=end_time_export,
                batch_size=self.settings.export_batch_size,
            )

            if not records:
                if force_range:
                    checkpoint = Checkpoint(
                        device_id=device_id,
                        last_exported_at=end_time_export,
                        status=ExportStatus.FAILED,
                        record_count=0,
                        error_message="No telemetry found in selected range",
                    )
                    await self.checkpoint_repo.save_checkpoint(checkpoint)
                logger.info(
                    "No records returned for device",
                    extra={"device_id": device_id}
                )
                return ExportResult(
                    success=True,
                    device_id=device_id,
                    start_time=start_time_export,
                    end_time=end_time_export,
                    record_count=0,
                    format=export_format,
                    duration_seconds=time.time() - wall_start,
                )

            batch_start = records[0].timestamp
            batch_end = records[-1].timestamp

            # -------------------------------------------------------
            # Save IN_PROGRESS checkpoint
            # -------------------------------------------------------
            checkpoint = Checkpoint(
                device_id=device_id,
                last_exported_at=batch_end,
                status=ExportStatus.IN_PROGRESS,
                record_count=len(records),
            )
            await self.checkpoint_repo.save_checkpoint(checkpoint)

            # -------------------------------------------------------
            # Build batch
            # -------------------------------------------------------
            batch = ExportBatch(
                device_id=device_id,
                start_time=batch_start,
                end_time=batch_end,
                records=records,
                record_count=len(records),
            )

            # -------------------------------------------------------
            # Write to S3
            # -------------------------------------------------------
            metadata = await self.s3_writer.write_batch(
                batch=batch,
                format=export_format,
            )

            # -------------------------------------------------------
            # Build S3 key again (same logic as writer)
            # IMPORTANT: must pass start_time AND end_time
            # -------------------------------------------------------
            s3_key = self.s3_writer._build_s3_key(
                device_id,
                batch_start,
                batch_end,
                export_format,
            )

            # -------------------------------------------------------
            # Update checkpoint to COMPLETED
            # -------------------------------------------------------
            checkpoint.status = ExportStatus.COMPLETED
            checkpoint.s3_key = s3_key
            checkpoint.record_count = len(records)
            checkpoint.last_exported_at = batch_end

            await self.checkpoint_repo.save_checkpoint(checkpoint)

            duration = time.time() - wall_start

            logger.info(
                "Successfully exported records for device",
                extra={
                    "device_id": device_id,
                    "record_count": len(records),
                    "s3_key": s3_key,
                    "duration_seconds": duration,
                }
            )

            return ExportResult(
                success=True,
                device_id=device_id,
                start_time=batch_start,
                end_time=batch_end,
                record_count=len(records),
                s3_key=s3_key,
                format=export_format,
                file_size_bytes=metadata.file_size_bytes,
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - wall_start

            logger.error(
                f"Export failed for device {device_id}: {e}",
                extra={
                    "device_id": device_id,
                    "error": str(e),
                    "duration_seconds": duration,
                }
            )

            failed_checkpoint = Checkpoint(
                device_id=device_id,
                last_exported_at=datetime.now(timezone.utc),
                status=ExportStatus.FAILED,
                error_message=str(e),
            )

            await self.checkpoint_repo.save_checkpoint(failed_checkpoint)

            return ExportResult(
                success=False,
                device_id=device_id,
                start_time=start_time_export
                if "start_time_export" in locals()
                else datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                record_count=0,
                format=ExportFormat(self.settings.export_format),
                error_message=str(e),
                duration_seconds=duration,
            )

    async def get_export_status(self, device_id: str) -> dict:
        """Get export status for a device."""

        checkpoint = await self.checkpoint_repo.get_last_checkpoint(device_id)

        if not checkpoint:
            return {
                "device_id": device_id,
                "status": "never_exported",
                "last_exported_at": None,
            }

        return {
            "device_id": device_id,
            "status": checkpoint.status.value,
            "last_exported_at": checkpoint.last_exported_at.isoformat(),
            "record_count": checkpoint.record_count,
            "s3_key": checkpoint.s3_key,
            "error_message": checkpoint.error_message,
            "updated_at": checkpoint.updated_at.isoformat()
            if checkpoint.updated_at else None,
        }
