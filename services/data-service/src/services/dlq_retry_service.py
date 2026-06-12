"""Background DLQ retry scheduler for telemetry reprocessing."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.repositories.dlq_repository import DLQRepository
from src.services.telemetry_service import TelemetryService
from src.utils import get_logger

logger = get_logger(__name__)


class DLQRetryService:
    """Async DLQ retry loop that replays telemetry through the existing service."""

    def __init__(
        self,
        *,
        telemetry_service: TelemetryService,
        dlq_repository: Optional[DLQRepository] = None,
        max_retry_count: int = 5,
        retry_grace_period: timedelta = timedelta(minutes=2),
        batch_limit: int = 100,
        base_backoff_seconds: int = 60,
        max_backoff_seconds: int = 300,
    ) -> None:
        self._telemetry_service = telemetry_service
        self._dlq_repository = dlq_repository or telemetry_service.dlq_repository
        self._max_retry_count = max(1, max_retry_count)
        self._retry_grace_period = retry_grace_period
        self._batch_limit = max(1, batch_limit)
        self._base_backoff_seconds = max(1, base_backoff_seconds)
        self._max_backoff_seconds = max(self._base_backoff_seconds, max_backoff_seconds)
        self._retryable_error_types = self._dlq_repository.retryable_error_types()
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._attempts_by_error_type: dict[str, int] = {}
        self._reprocessed_by_error_type: dict[str, int] = {}
        self._failed_by_error_type: dict[str, int] = {}

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="dlq-retry-service")
        logger.info(
            "dlq_retry_service_started",
            batch_limit=self._batch_limit,
            max_retry_count=self._max_retry_count,
            retry_grace_period_seconds=int(self._retry_grace_period.total_seconds()),
            retryable_error_types=list(self._retryable_error_types),
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("dlq_retry_service_stopped")

    async def _run_loop(self) -> None:
        sleep_seconds = self._base_backoff_seconds
        consecutive_empty_batches = 0

        while not self._stop_event.is_set():
            try:
                processed = await self._process_batch()
                if processed == 0:
                    consecutive_empty_batches += 1
                    if consecutive_empty_batches > 1:
                        sleep_seconds = min(sleep_seconds * 2, self._max_backoff_seconds)
                else:
                    consecutive_empty_batches = 0
                    sleep_seconds = self._base_backoff_seconds

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_seconds)
                except asyncio.TimeoutError:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("dlq_retry_loop_error", error=str(exc))
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._base_backoff_seconds)
                except asyncio.TimeoutError:
                    continue

    async def _process_batch(self) -> int:
        rows = await asyncio.to_thread(
            self._dlq_repository.fetch_pending_retries,
            max_retry_count=self._max_retry_count,
            grace_period=self._retry_grace_period,
            limit=self._batch_limit,
            error_types=self._retryable_error_types,
        )
        if not rows:
            logger.debug("dlq_retry_batch_empty")
            return 0

        for row in rows:
            await self._retry_row(row)
        return len(rows)

    async def _retry_row(self, row: dict[str, Any]) -> None:
        message_id = int(row["id"])
        current_retry_count = int(row.get("retry_count") or 0)
        next_retry_count = current_retry_count + 1
        error_type = str(row.get("error_type") or "unknown")
        payload = self._decode_payload(row.get("original_payload"))
        if error_type == "outbox_delivery_dead" and isinstance(payload, dict):
            telemetry = payload.get("telemetry")
            if isinstance(telemetry, dict):
                payload = telemetry
        device_id = str(payload.get("device_id") or "unknown")
        attempted_at = datetime.now(timezone.utc).replace(tzinfo=None)

        logger.info(
            "dlq_retry_attempt",
            message_id=message_id,
            device_id=device_id,
            retry_count=next_retry_count,
            error_type=error_type,
        )
        self._attempts_by_error_type[error_type] = int(self._attempts_by_error_type.get(error_type, 0) or 0) + 1

        try:
            accepted = await self._telemetry_service.process_telemetry_message(
                payload,
                correlation_id=f"dlq-retry-{message_id}-{next_retry_count}",
            )
            if not accepted:
                raise ValueError("Telemetry payload rejected during DLQ retry")
            await asyncio.to_thread(
                self._dlq_repository.mark_retry_reprocessed,
                message_id=message_id,
                retry_count=next_retry_count,
                last_retry_at=attempted_at,
            )
            logger.info(
                "dlq_retry_reprocessed",
                message_id=message_id,
                device_id=device_id,
                retry_count=next_retry_count,
                error_type=error_type,
            )
            self._reprocessed_by_error_type[error_type] = int(self._reprocessed_by_error_type.get(error_type, 0) or 0) + 1
        except Exception as exc:
            failure_reason = self._truncate_reason(exc)
            status = await asyncio.to_thread(
                self._dlq_repository.mark_retry_failed,
                message_id=message_id,
                retry_count=next_retry_count,
                last_retry_at=attempted_at,
                dead_reason=failure_reason,
                max_retry_count=self._max_retry_count,
            )
            logger.warning(
                "dlq_retry_failed",
                message_id=message_id,
                device_id=device_id,
                retry_count=next_retry_count,
                error_type=error_type,
                status=status,
                error=str(exc),
            )
            self._failed_by_error_type[error_type] = int(self._failed_by_error_type.get(error_type, 0) or 0) + 1

    @staticmethod
    def _decode_payload(original_payload: Any) -> dict[str, Any]:
        if isinstance(original_payload, dict):
            return original_payload
        if isinstance(original_payload, str):
            decoded = json.loads(original_payload)
            if isinstance(decoded, dict):
                return decoded
        raise ValueError("DLQ original_payload is not a valid JSON object")

    @staticmethod
    def _truncate_reason(exc: Exception, max_length: int = 4096) -> str:
        message = f"{type(exc).__name__}: {exc}"
        if len(message) <= max_length:
            return message
        return message[: max_length - 3] + "..."

    def get_stats(self) -> dict[str, Any]:
        return {
            "retryable_error_types": list(self._retryable_error_types),
            "attempts_by_error_type": dict(self._attempts_by_error_type),
            "reprocessed_by_error_type": dict(self._reprocessed_by_error_type),
            "failed_by_error_type": dict(self._failed_by_error_type),
            "batch_limit": self._batch_limit,
            "max_retry_count": self._max_retry_count,
            "base_backoff_seconds": self._base_backoff_seconds,
            "max_backoff_seconds": self._max_backoff_seconds,
        }
