"""Telemetry ingest/query service with durable stream-backed execution."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import bindparam, text

try:
    from prometheus_client import Counter, Gauge
except ImportError:  # pragma: no cover
    class _MetricValue:
        def __init__(self) -> None:
            self.value = 0

        def get(self) -> int:
            return self.value

    class _MetricChild:
        def __init__(self) -> None:
            self._value = _MetricValue()

        def inc(self, amount: int = 1) -> None:
            self._value.value += amount

        def set(self, value: int | float) -> None:
            self._value.value = value

    class _Metric:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._children: dict[tuple[tuple[str, Any], ...], _MetricChild] = {}

        def labels(self, **labels: Any) -> _MetricChild:
            key = tuple(sorted(labels.items()))
            if key not in self._children:
                self._children[key] = _MetricChild()
            return self._children[key]

    Counter = Gauge = _Metric  # type: ignore[assignment]

from src.config import settings
from src.models import OutboxTarget, TelemetryPayload
from src.queue import (
    DerivedEnvelope,
    PersistedEnvelope,
    RedisTelemetryStreamQueue,
    TelemetryIngressEnvelope,
    TelemetryStage,
)
from src.repositories import DLQRepository, InfluxDBRepository, OutboxRepository
from src.services.device_projection_client import DeviceProjectionClient, DeviceProjectionSyncError
from src.services.enrichment_service import EnrichmentService, _get_mysql_session_factory
from src.services.rule_engine_client import RuleEngineClient
from src.utils import (
    TelemetryValidator,
    get_logger,
    log_telemetry_error,
    log_telemetry_processed,
)

logger = get_logger(__name__)
TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
QUEUE_OVERFLOW_COUNTER: Dict[str, int] = defaultdict(int)
QUEUE_OVERFLOW_TOTAL = Counter(
    "telemetry_rejected_total",
    "Rejected telemetry ingress publishes",
    ["queue_name", "device_id"],
)
QUEUE_DEPTH = Gauge(
    "telemetry_stage_backlog_depth",
    "Current durable telemetry stage backlog depth",
    ["queue_name"],
)
TELEMETRY_STAGE_TOTAL = Counter(
    "telemetry_stage_total",
    "Telemetry pipeline events by stage",
    ["stage"],
)
QUEUE_OLDEST_AGE_SECONDS = Gauge(
    "telemetry_stage_oldest_age_seconds",
    "Age in seconds of the oldest queued telemetry stage item",
    ["queue_name"],
)


class TelemetryServiceError(Exception):
    """Raised when telemetry processing fails."""


class TelemetryService:
    """Public telemetry service surface plus worker stage helpers."""

    def __init__(
        self,
        influx_repository: Optional[InfluxDBRepository] = None,
        dlq_repository: Optional[DLQRepository] = None,
        outbox_repository: Optional[OutboxRepository] = None,
        enrichment_service: Optional[EnrichmentService] = None,
        rule_engine_client: Optional[RuleEngineClient] = None,
        device_projection_client: Optional[DeviceProjectionClient] = None,
        stage_queue: Optional[RedisTelemetryStreamQueue] = None,
    ):
        self.influx_repository = influx_repository or InfluxDBRepository()
        self.dlq_repository = dlq_repository or DLQRepository()
        self.outbox_repository = outbox_repository or OutboxRepository()
        self.enrichment_service = enrichment_service or EnrichmentService()
        self.rule_engine_client = rule_engine_client or RuleEngineClient(
            dlq_repository=self.dlq_repository,
        )
        self.device_projection_client = device_projection_client or DeviceProjectionClient()
        self.stage_queue = stage_queue or RedisTelemetryStreamQueue()

        self._ingest_stage_name = TelemetryStage.INGEST.value
        self._projection_stage_name = TelemetryStage.PROJECTION.value
        self._broadcast_stage_name = TelemetryStage.BROADCAST.value
        self._energy_stage_name = TelemetryStage.ENERGY.value
        self._rules_stage_name = TelemetryStage.RULES.value
        self._accepted_total = 0
        self._persisted_total = 0
        self._projection_synced_total = 0
        self._derived_total = 0
        self._rule_eval_skipped_total = 0
        self._rejected_total = 0
        self._last_stage_metrics: dict[str, Any] = {}
        self._ingest_publish_semaphore = asyncio.Semaphore(max(1, settings.telemetry_ingest_publish_concurrency))

    async def start(self) -> None:
        await self.outbox_repository.ensure_schema()
        await self.stage_queue.ensure_groups()
        await self._refresh_stage_metrics()
        logger.info(
            "TelemetryService ready",
            app_role=settings.app_role,
            ingest_stream=settings.telemetry_ingest_stream_name,
        )

    async def stop(self) -> None:
        await self._refresh_stage_metrics()

    async def process_telemetry_message(
        self,
        raw_payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> bool:
        correlation_id = correlation_id or str(uuid.uuid4())
        device_id = str(raw_payload.get("device_id") or "unknown")
        is_valid, error_type, error_message = TelemetryValidator.validate_payload(raw_payload)
        if not is_valid:
            await asyncio.to_thread(
                self.dlq_repository.send,
                original_payload=raw_payload,
                error_type=error_type or "validation_error",
                error_message=error_message or "Validation failed",
            )
            log_telemetry_error(
                logger=logger,
                device_id=device_id,
                correlation_id=correlation_id,
                error_type=error_type or "validation_error",
                error_message=error_message or "Validation failed",
                payload=raw_payload,
            )
            return False

        try:
            envelope = TelemetryIngressEnvelope(
                raw_payload=raw_payload,
                correlation_id=correlation_id,
            )
            async with self._ingest_publish_semaphore:
                await self.stage_queue.publish(
                    stage=TelemetryStage.INGEST,
                    payload=envelope,
                    correlation_id=correlation_id,
                )
            self._accepted_total += 1
            await self.stage_queue.incr_counter("accepted_total")
            TELEMETRY_STAGE_TOTAL.labels(stage="accepted").inc()
            return True
        except RuntimeError as exc:
            self._rejected_total += 1
            await self.stage_queue.incr_counter("rejected_total")
            TELEMETRY_STAGE_TOTAL.labels(stage="rejected_backlog").inc()
            await self._handle_stream_overflow(
                stage_name=self._ingest_stage_name,
                device_id=device_id,
                correlation_id=correlation_id,
                raw_payload=raw_payload,
                error=str(exc),
            )
            return False
        except Exception as exc:
            self._rejected_total += 1
            TELEMETRY_STAGE_TOTAL.labels(stage="rejected_unexpected").inc()
            await asyncio.to_thread(
                self.dlq_repository.send,
                original_payload=raw_payload,
                error_type="unexpected_error",
                error_message=str(exc),
            )
            logger.error(
                "Unexpected error enqueuing telemetry",
                device_id=device_id,
                correlation_id=correlation_id,
                error=str(exc),
            )
            return False

    async def _process_telemetry_async(
        self,
        *,
        payload: TelemetryPayload,
        correlation_id: str,
        raw_payload: Dict[str, Any],
    ) -> None:
        payload = await self.enrichment_service.enrich_telemetry(payload)
        if not await self._validate_ingest_ownership(
            payload=payload,
            correlation_id=correlation_id,
            raw_payload=raw_payload,
        ):
            return

        write_success = await asyncio.to_thread(self.influx_repository.write_telemetry, payload)
        if not write_success:
            await asyncio.to_thread(
                self.dlq_repository.send,
                original_payload=raw_payload,
                error_type="influxdb_write_error",
                error_message="Failed to write to InfluxDB",
            )
            log_telemetry_error(
                logger=logger,
                device_id=payload.device_id,
                correlation_id=correlation_id,
                error_type="influxdb_write_error",
                error_message="Failed to write to InfluxDB",
                payload=raw_payload,
            )
            return

        self._persisted_total += 1
        await self.stage_queue.incr_counter("persisted_total")
        TELEMETRY_STAGE_TOTAL.labels(stage="persisted").inc()
        outbox_payload = jsonable_encoder(payload.model_dump(mode="json"))
        await self._publish_projection_stage(
            payload=payload,
            raw_payload=raw_payload,
            correlation_id=correlation_id,
            outbox_payload=outbox_payload,
        )
        log_telemetry_processed(
            logger=logger,
            device_id=payload.device_id,
            correlation_id=correlation_id,
            enrichment_status=payload.enrichment_status.value,
        )

    async def _process_projection_async(
        self,
        *,
        payload: TelemetryPayload,
        correlation_id: str,
        outbox_payload: Dict[str, Any],
    ) -> None:
        projection_state = await self.device_projection_client.sync_projection(payload)
        await self._record_projection_success()
        await self._publish_downstream_stages(
            payload=payload,
            correlation_id=correlation_id,
            outbox_payload=outbox_payload,
            projection_synced=True,
            projection_state=projection_state,
            projection_error=None,
        )

    async def _process_derived_async(
        self,
        *,
        payload: TelemetryPayload,
        correlation_id: str,
        outbox_payload: Dict[str, Any],
        projection_synced: bool,
        projection_state: Dict[str, Any] | None,
        projection_error: str | None,
    ) -> None:
        if settings.energy_sync_enabled:
            await self._enqueue_outbox(
                device_id=payload.device_id,
                telemetry_payload=outbox_payload,
                targets=[OutboxTarget.ENERGY_SERVICE],
            )
        if not projection_synced:
            await self._handle_projection_skip_for_rules(
                payload=payload,
                correlation_id=correlation_id,
                outbox_payload=outbox_payload,
                projection_error=projection_error,
            )
            return

        await self.rule_engine_client.evaluate_rules(payload, projection_state=projection_state)
        self._derived_total += 1
        await self.stage_queue.incr_counter("derived_total")
        TELEMETRY_STAGE_TOTAL.labels(stage="rule_eval_completed").inc()

    async def _publish_projection_stage(
        self,
        *,
        payload: TelemetryPayload,
        raw_payload: Dict[str, Any],
        correlation_id: str,
        outbox_payload: Dict[str, Any],
    ) -> None:
        envelope = PersistedEnvelope(
            payload=jsonable_encoder(payload.model_dump(mode="json")),
            raw_payload=raw_payload,
            outbox_payload=outbox_payload,
            correlation_id=correlation_id,
        )
        try:
            await self.stage_queue.publish(
                stage=TelemetryStage.PROJECTION,
                payload=envelope,
                correlation_id=correlation_id,
            )
            TELEMETRY_STAGE_TOTAL.labels(stage="projection_queued").inc()
        except RuntimeError as exc:
            await self._handle_stream_overflow(
                stage_name=self._projection_stage_name,
                device_id=payload.device_id,
                correlation_id=correlation_id,
                raw_payload=outbox_payload,
                error=str(exc),
            )
            if settings.device_sync_enabled:
                await self._enqueue_outbox(
                    device_id=payload.device_id,
                    telemetry_payload=outbox_payload,
                    targets=[OutboxTarget.DEVICE_SERVICE],
                )

    async def _publish_downstream_stages(
        self,
        *,
        payload: TelemetryPayload,
        correlation_id: str,
        outbox_payload: Dict[str, Any],
        projection_synced: bool,
        projection_state: Dict[str, Any] | None,
        projection_error: str | None,
    ) -> None:
        envelope = DerivedEnvelope(
            payload=jsonable_encoder(payload.model_dump(mode="json")),
            outbox_payload=outbox_payload,
            correlation_id=correlation_id,
            projection_synced=projection_synced,
            projection_state=projection_state,
            projection_error=projection_error,
        )
        stage_plan = [
            (TelemetryStage.BROADCAST, self._broadcast_stage_name),
            (TelemetryStage.ENERGY, self._energy_stage_name),
            (TelemetryStage.RULES, self._rules_stage_name),
        ]
        for stage, stage_name in stage_plan:
            try:
                await self.stage_queue.publish(
                    stage=stage,
                    payload=envelope,
                    correlation_id=correlation_id,
                )
                TELEMETRY_STAGE_TOTAL.labels(stage=f"{stage.value}_queued").inc()
            except RuntimeError as exc:
                await self._handle_stream_overflow(
                    stage_name=stage_name,
                    device_id=payload.device_id,
                    correlation_id=correlation_id,
                    raw_payload=outbox_payload,
                    error=str(exc),
                )
                if stage == TelemetryStage.RULES and settings.device_sync_enabled and not projection_synced:
                    await self._enqueue_outbox(
                        device_id=payload.device_id,
                        telemetry_payload=outbox_payload,
                        targets=[OutboxTarget.DEVICE_SERVICE],
                    )

    async def _enqueue_outbox(
        self,
        *,
        device_id: str,
        telemetry_payload: Dict[str, Any],
        targets: list[OutboxTarget],
    ) -> None:
        if not targets:
            return
        await self.outbox_repository.enqueue_telemetry(
            device_id=device_id,
            telemetry_payload=telemetry_payload,
            targets=targets,
            max_retries=settings.outbox_max_retries,
        )

    async def _enqueue_outbox_batch(
        self,
        *,
        entries: list[tuple[str, Dict[str, Any], list[OutboxTarget]]],
    ) -> None:
        if not entries:
            return
        await self.outbox_repository.enqueue_telemetry_batch(
            entries=entries,
            max_retries=settings.outbox_max_retries,
        )

    async def _broadcast_telemetry(self, *, device_id: str, dynamic_fields: Dict[str, Any]) -> None:
        try:
            from src.api.websocket import broadcast_telemetry

            await broadcast_telemetry(
                device_id=device_id,
                telemetry_data=dynamic_fields,
            )
        except Exception as exc:
            logger.warning(
                "Failed to broadcast telemetry via WebSocket",
                device_id=device_id,
                error=str(exc),
            )

    async def _broadcast_telemetry_batch(self, *, items: list[tuple[str, Dict[str, Any]]]) -> None:
        for device_id, dynamic_fields in items:
            await self._broadcast_telemetry(device_id=device_id, dynamic_fields=dynamic_fields)

    async def _handle_projection_skip_for_rules(
        self,
        *,
        payload: TelemetryPayload,
        correlation_id: str,
        outbox_payload: Dict[str, Any],
        projection_error: str | None,
    ) -> None:
        self._rule_eval_skipped_total += 1
        await self.stage_queue.incr_counter("rule_eval_skipped_total")
        TELEMETRY_STAGE_TOTAL.labels(stage="projection_sync_failed").inc()
        logger.warning(
            "Skipping rule evaluation because device projection is stale for current sample",
            device_id=payload.device_id,
            correlation_id=correlation_id,
            projection_error=projection_error,
        )

    async def _record_projection_success(self) -> None:
        self._projection_synced_total += 1
        await self.stage_queue.incr_counter("projection_synced_total")
        TELEMETRY_STAGE_TOTAL.labels(stage="projection_synced").inc()

    async def _validate_ingest_ownership(
        self,
        *,
        payload: TelemetryPayload,
        correlation_id: str,
        raw_payload: Dict[str, Any],
    ) -> bool:
        payload_tenant_id = self._normalize_optional_string(payload.tenant_id)
        metadata_tenant_id = self._normalize_optional_string(
            None if payload.device_metadata is None else payload.device_metadata.tenant_id
        )
        if payload_tenant_id and metadata_tenant_id and payload_tenant_id != metadata_tenant_id:
            await self._reject_ingest_ownership(
                payload=payload,
                correlation_id=correlation_id,
                raw_payload=raw_payload,
                error_type="device_ownership_error",
                error_message="Telemetry tenant scope does not match device metadata tenant.",
            )
            return False
        tenant_id = payload_tenant_id or metadata_tenant_id
        if not tenant_id:
            await self._reject_ingest_ownership(
                payload=payload,
                correlation_id=correlation_id,
                raw_payload=raw_payload,
                error_type="tenant_scope_required",
                error_message="Telemetry tenant scope is required for ingestion.",
            )
            return False
        try:
            owned_devices = await self._fetch_tenant_owned_device_ids(
                tenant_id=tenant_id,
                device_ids=[payload.device_id],
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            await self._reject_ingest_ownership(
                payload=payload,
                correlation_id=correlation_id,
                raw_payload=raw_payload,
                error_type=str(detail.get("code") or "tenant_scope_invalid").lower(),
                error_message=str(detail.get("message") or "Telemetry tenant scope is invalid."),
            )
            return False
        if payload.device_id not in owned_devices:
            await self._reject_ingest_ownership(
                payload=payload,
                correlation_id=correlation_id,
                raw_payload=raw_payload,
                error_type="device_ownership_error",
                error_message="Telemetry device was not found in tenant scope.",
            )
            return False
        payload.tenant_id = tenant_id
        if payload.device_metadata is not None and not metadata_tenant_id:
            payload.device_metadata.tenant_id = tenant_id
        return True

    async def _reject_ingest_ownership(
        self,
        *,
        payload: TelemetryPayload,
        correlation_id: str,
        raw_payload: Dict[str, Any],
        error_type: str,
        error_message: str,
    ) -> None:
        await asyncio.to_thread(
            self.dlq_repository.send,
            original_payload=raw_payload,
            error_type=error_type,
            error_message=error_message,
        )
        log_telemetry_error(
            logger=logger,
            device_id=payload.device_id,
            correlation_id=correlation_id,
            error_type=error_type,
            error_message=error_message,
            payload=raw_payload,
        )

    async def _handle_stream_overflow(
        self,
        *,
        stage_name: str,
        device_id: str,
        correlation_id: str,
        raw_payload: Dict[str, Any],
        error: str,
    ) -> None:
        overflow_count = QUEUE_OVERFLOW_COUNTER[stage_name] + 1
        QUEUE_OVERFLOW_COUNTER[stage_name] = overflow_count
        QUEUE_OVERFLOW_TOTAL.labels(queue_name=stage_name, device_id=device_id).inc()
        _overflow_log = getattr(logger, settings.queue_overflow_log_level.lower(), logger.warning)
        _overflow_log(
            "telemetry_stream_backlog_rejected",
            stage=stage_name,
            device_id=device_id,
            correlation_id=correlation_id,
            overflow_count=overflow_count,
            error=error,
        )
        await asyncio.to_thread(
            self.dlq_repository.send,
            original_payload=raw_payload,
            error_type="QUEUE_OVERFLOW",
            error_message=f"{stage_name} backlog rejected: {error}",
            initial_status="dead",
            dead_reason=f"{stage_name} backlog rejected: {error}",
        )

    async def _refresh_stage_metrics(self) -> None:
        stats = await self.stage_queue.metrics()
        self._last_stage_metrics = stats
        for stage_name, stage_stats in stats.get("stages", {}).items():
            QUEUE_DEPTH.labels(queue_name=stage_name).set(stage_stats.get("backlog_depth", 0))
            QUEUE_OLDEST_AGE_SECONDS.labels(queue_name=stage_name).set(stage_stats.get("oldest_age_seconds", 0.0))

    @staticmethod
    def _normalize_optional_string(value: object | None) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    async def query_telemetry(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ):
        await self._assert_device_owned_by_tenant(tenant_id=tenant_id, device_id=device_id)
        return await self.influx_repository.async_query_telemetry(
            tenant_id=tenant_id,
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    async def get_telemetry(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        fields: Optional[list[str]] = None,
        aggregate: Optional[str] = None,
        interval: Optional[str] = None,
        limit: int = 1000,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> list:
        await self._assert_device_owned_by_tenant(
            tenant_id=tenant_id,
            device_id=device_id,
            accessible_plant_ids=accessible_plant_ids,
        )
        return await self.influx_repository.async_query_telemetry(
            tenant_id=tenant_id,
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
            fields=fields,
            aggregate=aggregate,
            interval=interval,
            limit=limit,
        )

    async def get_latest(
        self,
        tenant_id: str,
        device_id: str,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> Optional[Any]:
        await self._assert_device_owned_by_tenant(
            tenant_id=tenant_id,
            device_id=device_id,
            accessible_plant_ids=accessible_plant_ids,
        )
        return await self.influx_repository.async_get_latest_telemetry(
            tenant_id=tenant_id,
            device_id=device_id,
        )

    async def get_earliest(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> Optional[Any]:
        await self._assert_device_owned_by_tenant(
            tenant_id=tenant_id,
            device_id=device_id,
            accessible_plant_ids=accessible_plant_ids,
        )
        return await self.influx_repository.async_get_earliest_telemetry(
            tenant_id=tenant_id,
            device_id=device_id,
            start_time=start_time,
        )

    async def get_latest_batch(
        self,
        tenant_id: str,
        device_ids: list[str],
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> Dict[str, Optional[Any]]:
        await self._assert_devices_owned_by_tenant(
            tenant_id=tenant_id,
            device_ids=device_ids,
            accessible_plant_ids=accessible_plant_ids,
        )
        return await self.influx_repository.async_get_latest_telemetry_batch(
            tenant_id=tenant_id,
            device_ids=device_ids,
        )

    async def get_stats(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> Optional[Any]:
        await self._assert_device_owned_by_tenant(
            tenant_id=tenant_id,
            device_id=device_id,
            accessible_plant_ids=accessible_plant_ids,
        )
        return await self.influx_repository.async_get_stats(
            tenant_id=tenant_id,
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
        )

    async def assert_device_access(
        self,
        *,
        tenant_id: str,
        device_id: str,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> None:
        await self._assert_device_owned_by_tenant(
            tenant_id=tenant_id,
            device_id=device_id,
            accessible_plant_ids=accessible_plant_ids,
        )

    async def _assert_device_owned_by_tenant(
        self,
        *,
        tenant_id: str,
        device_id: str,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> None:
        owned_devices = await self._fetch_tenant_owned_device_ids(
            tenant_id=tenant_id,
            device_ids=[device_id],
            accessible_plant_ids=accessible_plant_ids,
        )
        if device_id not in owned_devices:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "DEVICE_NOT_FOUND",
                    "message": f"Device {device_id} was not found in tenant scope.",
                },
            )

    async def _assert_devices_owned_by_tenant(
        self,
        *,
        tenant_id: str,
        device_ids: list[str],
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> None:
        requested_device_ids = [device_id for device_id in dict.fromkeys(device_ids) if device_id]
        if not requested_device_ids:
            return
        owned_devices = await self._fetch_tenant_owned_device_ids(
            tenant_id=tenant_id,
            device_ids=requested_device_ids,
            accessible_plant_ids=accessible_plant_ids,
        )
        missing_device_ids = [device_id for device_id in requested_device_ids if device_id not in owned_devices]
        if missing_device_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "DEVICE_NOT_FOUND",
                    "message": "One or more devices were not found in tenant scope.",
                    "device_ids": missing_device_ids,
                },
            )

    async def _fetch_tenant_owned_device_ids(
        self,
        *,
        tenant_id: str,
        device_ids: list[str],
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> set[str]:
        if not tenant_id or not TENANT_ID_PATTERN.fullmatch(tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "TENANT_SCOPE_INVALID",
                    "message": "Tenant scope is invalid.",
                },
            )
        requested_device_ids = [device_id for device_id in dict.fromkeys(device_ids) if device_id]
        if not requested_device_ids:
            return set()
        scoped_plant_ids: Optional[list[str]] = None
        if accessible_plant_ids is not None:
            scoped_plant_ids = [plant_id for plant_id in dict.fromkeys(accessible_plant_ids) if plant_id]
            if not scoped_plant_ids:
                return set()
        session_factory = _get_mysql_session_factory()
        async with session_factory() as session:
            query = """
                SELECT DISTINCT device_id
                FROM devices
                WHERE tenant_id = :tenant_id
                  AND device_id IN :device_ids
                  AND deleted_at IS NULL
            """
            params: dict[str, object] = {
                "tenant_id": tenant_id,
                "device_ids": requested_device_ids,
            }
            bind_params = [bindparam("device_ids", expanding=True)]
            if scoped_plant_ids is not None:
                query += "\n  AND plant_id IN :plant_ids"
                params["plant_ids"] = scoped_plant_ids
                bind_params.append(bindparam("plant_ids", expanding=True))
            result = await session.execute(
                text(query).bindparams(*bind_params),
                params,
            )
            return {
                str(row[0]).strip()
                for row in result.all()
                if row[0] is not None and str(row[0]).strip()
            }

    async def close(self) -> None:
        await self.stop()
        try:
            logger.info("DLQ operational stats", **self.dlq_repository.get_operational_stats())
        except Exception as exc:
            logger.warning("Failed to fetch DLQ operational stats", error=str(exc))
        self.influx_repository.close()
        await self.outbox_repository.close()
        self.dlq_repository.close()
        await self.enrichment_service.close()
        await self.rule_engine_client.close()
        await self.device_projection_client.close()
        await self.stage_queue.close()
        logger.info("TelemetryService closed")

    def get_operational_stats(self) -> Dict[str, Any]:
        metrics = {
            "accepted_total": self._accepted_total,
            "persisted_total": self._persisted_total,
            "projection_synced_total": self._projection_synced_total,
            "derived_total": self._derived_total,
            "rule_eval_skipped_total": self._rule_eval_skipped_total,
            "rejected_total": self._rejected_total,
            "queue_overflow_total": dict(QUEUE_OVERFLOW_COUNTER),
            "dlq": self.dlq_repository.get_operational_stats(),
        }
        stats = self._last_stage_metrics
        metrics["stages"] = stats.get("stages", {})
        metrics["dead_letter_depth"] = stats.get("dead_letter_depth", 0)
        metrics["shared_counters"] = stats.get("counters", {})
        metrics["workers"] = stats.get("workers", {})
        return metrics
