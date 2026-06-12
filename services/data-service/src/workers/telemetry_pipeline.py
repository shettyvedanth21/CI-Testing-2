"""Worker-owned telemetry pipeline stages."""

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from src.config import settings
from src.models import OutboxTarget, TelemetryPayload
from src.queue import (
    DerivedEnvelope,
    PersistedEnvelope,
    QueueMessage,
    RedisTelemetryStreamQueue,
    TelemetryIngressEnvelope,
    TelemetryStage,
)
from src.services.device_projection_client import DeviceProjectionSyncError
from src.services.tenant_lock import TenantLockProvider, TenantLockTimeoutError, create_tenant_lock
from src.services.telemetry_service import TelemetryService
from src.utils import get_logger

logger = get_logger(__name__)

_PROJECTION_DEFER_RETRYABLE_CODES = {
    "DEVICE_PROJECTION_CIRCUIT_OPEN",
    "DEVICE_PROJECTION_TRANSPORT_ERROR",
    "DEVICE_PROJECTION_OVERLOADED",
    "DEVICE_PROJECTION_SERVER_ERROR",
    "DEVICE_PROJECTION_INVALID_BATCH_RESPONSE",
    "PROJECTION_CONCURRENT_WRITE_CONFLICT",
}


class TelemetryPipelineWorker:
    """Runs durable persistence, projection, and downstream fan-out stages."""

    def __init__(
        self,
        telemetry_service: TelemetryService,
        *,
        stage_queue: RedisTelemetryStreamQueue | None = None,
        consumer_name: str | None = None,
        tenant_lock: TenantLockProvider | None = None,
    ) -> None:
        self.telemetry_service = telemetry_service
        self.stage_queue = stage_queue or telemetry_service.stage_queue
        self.consumer_name = consumer_name or settings.telemetry_worker_consumer_name
        self._running = False
        self._ready = False
        self._tasks: list[asyncio.Task] = []
        self._inflight: dict[str, int] = defaultdict(int)
        self._tenant_lock = tenant_lock or create_tenant_lock(
            settings.tenant_lock_provider,
            redis_url=settings.redis_url,
            lock_ttl_seconds=max(1, settings.tenant_lock_redis_ttl_seconds),
            acquire_timeout_seconds=max(1.0, settings.tenant_lock_redis_acquire_timeout_seconds),
        )

    async def start(self) -> None:
        self._running = True
        await self.stage_queue.ensure_groups()
        self._tasks = [
            *[
                asyncio.create_task(self._stage_loop(stage=TelemetryStage.INGEST, worker_index=index), name=f"telemetry-ingest-{index}")
                for index in range(max(1, settings.telemetry_persistence_workers))
            ],
            *[
                asyncio.create_task(self._stage_loop(stage=TelemetryStage.PROJECTION, worker_index=index), name=f"telemetry-projection-{index}")
                for index in range(max(1, settings.telemetry_projection_workers))
            ],
            *[
                asyncio.create_task(self._stage_loop(stage=TelemetryStage.BROADCAST, worker_index=index), name=f"telemetry-broadcast-{index}")
                for index in range(max(1, settings.telemetry_broadcast_workers))
            ],
            *[
                asyncio.create_task(self._stage_loop(stage=TelemetryStage.ENERGY, worker_index=index), name=f"telemetry-energy-{index}")
                for index in range(max(1, settings.telemetry_energy_workers))
            ],
            *[
                asyncio.create_task(self._stage_loop(stage=TelemetryStage.RULES, worker_index=index), name=f"telemetry-rules-{index}")
                for index in range(max(1, settings.telemetry_rules_workers))
            ],
            asyncio.create_task(self._heartbeat_loop(), name="telemetry-worker-heartbeat"),
            asyncio.create_task(self._tenant_lock_cleanup_loop(), name="telemetry-tenant-lock-cleanup"),
        ]
        self._ready = True
        logger.info(
            "Telemetry pipeline worker started",
            consumer_name=self.consumer_name,
            persistence_workers=settings.telemetry_persistence_workers,
            projection_workers=settings.telemetry_projection_workers,
            broadcast_workers=settings.telemetry_broadcast_workers,
            energy_workers=settings.telemetry_energy_workers,
            rules_workers=settings.telemetry_rules_workers,
        )

    async def stop(self) -> None:
        self._running = False
        self._ready = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        logger.info("Telemetry pipeline worker stopped", consumer_name=self.consumer_name)

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                await self.stage_queue.record_worker_health(
                    worker_name=self.consumer_name,
                    role=settings.app_role,
                    ready=self._ready,
                    maintenance_enabled=settings.telemetry_worker_maintenance_enabled,
                    stages=[
                        TelemetryStage.INGEST.value,
                        TelemetryStage.PROJECTION.value,
                        TelemetryStage.BROADCAST.value,
                        TelemetryStage.ENERGY.value,
                        TelemetryStage.RULES.value,
                    ],
                    inflight=dict(self._inflight),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Failed to record telemetry worker heartbeat", worker=self.consumer_name, error=str(exc))
            await asyncio.sleep(5.0)

    async def _tenant_lock_cleanup_loop(self) -> None:
        interval = max(60, settings.tenant_lock_cleanup_interval_sec)
        while self._running:
            await asyncio.sleep(interval)
            try:
                removed = self._tenant_lock.cleanup_inactive()
                if removed:
                    logger.debug("Tenant lock cleanup", removed=removed, remaining=self._tenant_lock.active_lock_count)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Tenant lock cleanup failed", error=str(exc))

    async def _stage_loop(self, *, stage: TelemetryStage, worker_index: int) -> None:
        batch_size = self._batch_size_for(stage)
        consumer_name = self._consumer_name(stage, worker_index)
        while self._running:
            try:
                messages = await self.stage_queue.read_batch(
                    stage=stage,
                    consumer_name=consumer_name,
                    batch_size=batch_size,
                    block_ms=max(100, settings.telemetry_stream_block_ms),
                )
                if not messages:
                    reclaimed = await self.stage_queue.reclaim_stale(
                        stage=stage,
                        consumer_name=consumer_name,
                        min_idle_ms=max(1000, settings.telemetry_stage_reclaim_idle_ms),
                        batch_size=batch_size,
                    )
                    if not reclaimed:
                        await asyncio.sleep(0.1)
                        continue
                    messages = reclaimed
                self._inflight[stage.value] = len(messages)
                await self._handle_batch(stage=stage, messages=messages)
                self._inflight[stage.value] = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._inflight[stage.value] = 0
                logger.error(
                    "Telemetry stage loop failed",
                    stage=stage.value,
                    worker_index=worker_index,
                    error=str(exc),
                )
                await asyncio.sleep(1.0)

    def _consumer_name(self, stage: TelemetryStage, worker_index: int) -> str:
        return f"{self.consumer_name}:{stage.value}:{worker_index}"

    @staticmethod
    def _batch_size_for(stage: TelemetryStage) -> int:
        if stage == TelemetryStage.INGEST:
            return max(1, settings.telemetry_ingest_batch_size)
        if stage == TelemetryStage.PROJECTION:
            return max(1, settings.telemetry_projection_batch_size)
        if stage == TelemetryStage.BROADCAST:
            return max(1, settings.telemetry_broadcast_batch_size)
        if stage == TelemetryStage.ENERGY:
            return max(1, settings.telemetry_energy_batch_size)
        return max(1, settings.telemetry_rules_batch_size)

    async def _handle_batch(self, *, stage: TelemetryStage, messages: list[QueueMessage]) -> None:
        if stage == TelemetryStage.INGEST:
            for message in messages:
                await self._handle_message(stage=stage, message=message)
            return
        if stage == TelemetryStage.PROJECTION:
            await self._handle_projection_batch(messages)
            return
        if stage == TelemetryStage.BROADCAST:
            await self._handle_broadcast_batch(messages)
            return
        if stage == TelemetryStage.ENERGY:
            await self._handle_energy_batch(messages)
            return
        await self._handle_rules_batch(messages)

    async def _handle_message(self, *, stage: TelemetryStage, message: QueueMessage) -> None:
        try:
            if stage == TelemetryStage.INGEST:
                envelope = TelemetryIngressEnvelope(**message.payload)
                await self.telemetry_service._process_telemetry_async(  # noqa: SLF001
                    payload=TelemetryPayload(**envelope.raw_payload),
                    correlation_id=envelope.correlation_id,
                    raw_payload=envelope.raw_payload,
                )
            else:
                raise RuntimeError(f"Unsupported single-message stage {stage.value}")
            await self.stage_queue.ack(stage=stage, message_ids=[message.message_id])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_stage_failure(stage=stage, message=message, error=str(exc))

    async def _handle_projection_batch(self, messages: list[QueueMessage]) -> None:
        groups: dict[str, list[tuple[QueueMessage, PersistedEnvelope, TelemetryPayload]]] = defaultdict(list)
        for message in messages:
            envelope = PersistedEnvelope(**message.payload)
            payload = TelemetryPayload(**envelope.payload)
            try:
                tenant_id = self.telemetry_service.device_projection_client._resolve_tenant_id(payload)  # noqa: SLF001
            except DeviceProjectionSyncError as exc:
                await self._handle_projection_result(
                    message=message,
                    envelope=envelope,
                    payload=payload,
                    result={
                        "success": False,
                        "error": str(exc),
                        "error_code": exc.code,
                        "error_category": exc.category,
                        "retryable": exc.retryable,
                    },
                )
                continue
            groups[tenant_id].append((message, envelope, payload))

        for tenant_id, group_messages in groups.items():
            try:
                async with self._tenant_lock.acquire(tenant_id):
                    payloads = [item[2] for item in group_messages]
                    try:
                        results = await self.telemetry_service.device_projection_client.sync_projection_batch(payloads)
                    except DeviceProjectionSyncError as exc:
                        if exc.retryable:
                            for message, _envelope, _payload in group_messages:
                                await self._handle_projection_retryable_failure(
                                    message=message,
                                    error=str(exc),
                                    error_code=exc.code,
                                    error_category=exc.category,
                                )
                            continue
                        for message, envelope, payload in group_messages:
                            await self.telemetry_service._publish_downstream_stages(  # noqa: SLF001
                                payload=payload,
                                correlation_id=envelope.correlation_id,
                                outbox_payload=envelope.outbox_payload,
                                projection_synced=False,
                                projection_state=None,
                                projection_error=str(exc),
                            )
                            await self.stage_queue.ack(stage=TelemetryStage.PROJECTION, message_ids=[message.message_id])
                        continue
                    except Exception as exc:
                        for message, _envelope, _payload in group_messages:
                            await self._handle_stage_failure(stage=TelemetryStage.PROJECTION, message=message, error=str(exc))
                        continue

                    for (message, envelope, payload), result in zip(group_messages, results, strict=False):
                        await self._handle_projection_result(
                            message=message,
                            envelope=envelope,
                            payload=payload,
                            result=result,
                        )
            except TenantLockTimeoutError:
                for message, _envelope, _payload in group_messages:
                    await self._handle_projection_retryable_failure(
                        message=message,
                        error=f"TENANT_LOCK_TIMEOUT:{tenant_id}",
                        error_code="TENANT_LOCK_TIMEOUT",
                        error_category="downstream_overload",
                    )

    async def _handle_projection_result(
        self,
        *,
        message: QueueMessage,
        envelope: PersistedEnvelope,
        payload: TelemetryPayload,
        result: dict[str, Any],
    ) -> None:
        try:
            projection_synced = bool(result.get("success"))
            retryable = bool(result.get("retryable", True))
            error_code = str(result.get("error_code") or "DEVICE_PROJECTION_FAILED")
            error_category = str(result.get("error_category") or "unexpected_internal_error")
            projection_error = None if projection_synced else str(result.get("error") or error_code)
            projection_state = result.get("device") if projection_synced and isinstance(result.get("device"), dict) else None

            if not projection_synced and retryable:
                await self._handle_projection_retryable_failure(
                    message=message,
                    error=f"{error_code}:{projection_error}",
                    error_code=error_code,
                    error_category=error_category,
                )
                return

            if projection_synced:
                await self.telemetry_service._record_projection_success()  # noqa: SLF001
            await self.telemetry_service._publish_downstream_stages(  # noqa: SLF001
                payload=payload,
                correlation_id=envelope.correlation_id,
                outbox_payload=envelope.outbox_payload,
                projection_synced=projection_synced,
                projection_state=projection_state,
                projection_error=projection_error if projection_synced else f"{error_category}:{projection_error}",
            )
            await self.stage_queue.ack(stage=TelemetryStage.PROJECTION, message_ids=[message.message_id])
        except Exception as exc:
            await self._handle_stage_failure(stage=TelemetryStage.PROJECTION, message=message, error=str(exc))

    async def _handle_projection_retryable_failure(
        self,
        *,
        message: QueueMessage,
        error: str,
        error_code: str,
        error_category: str,
    ) -> None:
        normalized_code = str(error_code or "").strip().upper()
        normalized_category = str(error_category or "").strip().lower()
        is_defer_class = (
            normalized_category in {"downstream_overload", "transient_dependency_failure"}
            or normalized_code in _PROJECTION_DEFER_RETRYABLE_CODES
        )
        if is_defer_class:
            await self._defer_projection_message(
                message=message,
                error=error,
                error_code=normalized_code or "DEVICE_PROJECTION_RETRYABLE",
            )
            return
        await self._handle_stage_failure(
            stage=TelemetryStage.PROJECTION,
            message=message,
            error=f"{normalized_code or 'DEVICE_PROJECTION_RETRYABLE'}:{error}",
        )

    async def _defer_projection_message(
        self,
        *,
        message: QueueMessage,
        error: str,
        error_code: str,
    ) -> None:
        payload = dict(message.payload)
        defer_count = int(payload.get("_projection_defer_count") or 0) + 1
        max_defers = max(1, int(settings.telemetry_projection_overload_max_defers))
        if defer_count > max_defers:
            await self.stage_queue.dead_letter(
                stage=TelemetryStage.PROJECTION,
                message=message,
                reason=f"{error_code}:projection_defer_limit_exceeded:{error}"[:2048],
                terminal_payload=payload,
            )
            logger.error(
                "Projection message dead-lettered after deferred retries exhausted",
                stage=TelemetryStage.PROJECTION.value,
                message_id=message.message_id,
                defer_count=defer_count,
                error=error,
                error_code=error_code,
            )
            return

        # Deterministic jitter spreads retries without introducing randomness in tests.
        digest = hashlib.sha1(message.message_id.encode("utf-8")).hexdigest()[:8]
        jitter_basis = int(digest, 16) % 100
        jitter_seconds = jitter_basis / 1000.0
        base_backoff = max(0.05, float(settings.telemetry_projection_defer_base_seconds))
        max_backoff = max(base_backoff, float(settings.telemetry_projection_defer_max_seconds))
        backoff_seconds = min(max_backoff, base_backoff * (2 ** (defer_count - 1))) + jitter_seconds

        payload["_projection_defer_count"] = defer_count
        payload["_projection_last_defer_error"] = f"{error_code}:{error}"[:1024]
        payload["_projection_last_defer_at"] = datetime.now(timezone.utc).isoformat()

        await asyncio.sleep(backoff_seconds)
        await self.stage_queue.publish(
            stage=TelemetryStage.PROJECTION,
            payload=payload,
            correlation_id=message.correlation_id,
            attempt=max(1, message.attempt),
        )
        await self.stage_queue.ack(stage=TelemetryStage.PROJECTION, message_ids=[message.message_id])
        logger.warning(
            "Projection message deferred due to overload/transport pressure",
            stage=TelemetryStage.PROJECTION.value,
            message_id=message.message_id,
            defer_count=defer_count,
            defer_seconds=round(backoff_seconds, 3),
            error=error,
            error_code=error_code,
        )

    async def _handle_broadcast_batch(self, messages: list[QueueMessage]) -> None:
        latest_by_device: dict[str, tuple[datetime, QueueMessage, DerivedEnvelope]] = {}
        for message in messages:
            envelope = DerivedEnvelope(**message.payload)
            payload = TelemetryPayload(**envelope.payload)
            existing = latest_by_device.get(payload.device_id)
            if existing is None or payload.timestamp >= existing[0]:
                latest_by_device[payload.device_id] = (payload.timestamp, message, envelope)

        await self.telemetry_service._broadcast_telemetry_batch(  # noqa: SLF001
            items=[
                (TelemetryPayload(**envelope.payload).device_id, TelemetryPayload(**envelope.payload).get_dynamic_fields())
                for _, message, envelope in latest_by_device.values()
            ],
        )
        await self.stage_queue.ack(stage=TelemetryStage.BROADCAST, message_ids=[message.message_id for message in messages])

    async def _handle_energy_batch(self, messages: list[QueueMessage]) -> None:
        entries: list[tuple[str, dict[str, Any], list[OutboxTarget]]] = []
        for message in messages:
            envelope = DerivedEnvelope(**message.payload)
            payload = TelemetryPayload(**envelope.payload)
            targets: list[OutboxTarget] = []
            if settings.energy_sync_enabled:
                targets.append(OutboxTarget.ENERGY_SERVICE)
            if not envelope.projection_synced and settings.device_sync_enabled:
                targets.append(OutboxTarget.DEVICE_SERVICE)
            if targets:
                entries.append((payload.device_id, envelope.outbox_payload, targets))
        if entries:
            await self.telemetry_service._enqueue_outbox_batch(entries=entries)  # noqa: SLF001
        await self.stage_queue.ack(stage=TelemetryStage.ENERGY, message_ids=[message.message_id for message in messages])

    async def _handle_rules_batch(self, messages: list[QueueMessage]) -> None:
        async def _run_rule(message: QueueMessage) -> None:
            envelope = DerivedEnvelope(**message.payload)
            payload = TelemetryPayload(**envelope.payload)
            if not envelope.projection_synced:
                await self.telemetry_service._handle_projection_skip_for_rules(  # noqa: SLF001
                    payload=payload,
                    correlation_id=envelope.correlation_id,
                    outbox_payload=envelope.outbox_payload,
                    projection_error=envelope.projection_error,
                )
                return
            await self.telemetry_service.rule_engine_client.evaluate_rules(
                payload,
                projection_state=envelope.projection_state,
            )
            self.telemetry_service._derived_total += 1  # noqa: SLF001
            await self.stage_queue.incr_counter("derived_total")

        results = await asyncio.gather(*[_run_rule(message) for message in messages], return_exceptions=True)
        ack_ids: list[str] = []
        for message, result in zip(messages, results, strict=False):
            if isinstance(result, Exception):
                await self._handle_stage_failure(stage=TelemetryStage.RULES, message=message, error=str(result))
            else:
                ack_ids.append(message.message_id)
        if ack_ids:
            await self.stage_queue.ack(stage=TelemetryStage.RULES, message_ids=ack_ids)

    async def _handle_stage_failure(
        self,
        *,
        stage: TelemetryStage,
        message: QueueMessage,
        error: str,
    ) -> None:
        if message.attempt >= max(1, settings.telemetry_stage_max_attempts):
            await self.stage_queue.dead_letter(
                stage=stage,
                message=message,
                reason=error,
            )
            logger.error(
                "Telemetry stage dead-lettered after retries",
                stage=stage.value,
                message_id=message.message_id,
                attempt=message.attempt,
                error=error,
            )
            return

        await self.stage_queue.publish(
            stage=stage,
            payload=message.payload,
            correlation_id=message.correlation_id,
            attempt=message.attempt + 1,
        )
        await self.stage_queue.ack(stage=stage, message_ids=[message.message_id])
        logger.warning(
            "Telemetry stage requeued",
            stage=stage.value,
            message_id=message.message_id,
            attempt=message.attempt + 1,
            error=error,
        )
