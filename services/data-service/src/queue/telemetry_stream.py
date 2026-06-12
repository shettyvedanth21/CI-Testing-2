"""Redis Streams-backed telemetry ingest and stage queues."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from src.config import settings
from src.utils import get_logger

logger = get_logger(__name__)


class TelemetryStage(str, Enum):
    """Telemetry pipeline stages backed by Redis Streams."""

    INGEST = "ingest"
    PROJECTION = "projection"
    BROADCAST = "broadcast"
    ENERGY = "energy"
    RULES = "rules"


class TelemetryIngressEnvelope(BaseModel):
    """Durable ingress envelope published by MQTT acceptance."""

    raw_payload: dict[str, Any]
    correlation_id: str
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PersistedEnvelope(BaseModel):
    """Payload published after persistence succeeds."""

    payload: dict[str, Any]
    raw_payload: dict[str, Any]
    outbox_payload: dict[str, Any]
    correlation_id: str
    persisted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DerivedEnvelope(BaseModel):
    """Payload published after projection sync finishes."""

    payload: dict[str, Any]
    outbox_payload: dict[str, Any]
    correlation_id: str
    projection_synced: bool
    projection_state: dict[str, Any] | None = None
    projection_error: str | None = None
    projected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class QueueMessage:
    """Decoded stream entry ready for worker processing."""

    stage: TelemetryStage
    message_id: str
    attempt: int
    correlation_id: str
    enqueued_at: datetime
    payload: dict[str, Any]


class RedisTelemetryStreamQueue:
    """Multi-stage Redis Streams queue with consumer groups."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis = Redis.from_url(
            redis_url or settings.redis_url,
            decode_responses=True,
            max_connections=max(8, settings.redis_max_connections),
        )
        self._stage_config = {
            TelemetryStage.INGEST: {
                "stream": settings.telemetry_ingest_stream_name,
                "group": settings.telemetry_ingest_consumer_group,
                "maxlen": max(1, settings.telemetry_ingest_stream_maxlen),
            },
            TelemetryStage.PROJECTION: {
                "stream": settings.telemetry_projection_stream_name,
                "group": settings.telemetry_projection_consumer_group,
                "maxlen": max(1, settings.telemetry_projection_stream_maxlen),
            },
            TelemetryStage.BROADCAST: {
                "stream": settings.telemetry_broadcast_stream_name,
                "group": settings.telemetry_broadcast_consumer_group,
                "maxlen": max(1, settings.telemetry_broadcast_stream_maxlen),
            },
            TelemetryStage.ENERGY: {
                "stream": settings.telemetry_energy_stream_name,
                "group": settings.telemetry_energy_consumer_group,
                "maxlen": max(1, settings.telemetry_energy_stream_maxlen),
            },
            TelemetryStage.RULES: {
                "stream": settings.telemetry_rules_stream_name,
                "group": settings.telemetry_rules_consumer_group,
                "maxlen": max(1, settings.telemetry_rules_stream_maxlen),
            },
        }
        self._dead_letter_stream = settings.telemetry_dead_letter_stream_name
        self._metrics_key = "telemetry_pipeline_metrics"
        self._worker_health_members_key = "telemetry_worker_health_members"
        self._worker_health_key_prefix = "telemetry_worker_health"
        self._stage_publish_limits = {
            TelemetryStage.INGEST: max(1, settings.telemetry_ingest_reject_threshold),
            TelemetryStage.PROJECTION: max(1, settings.telemetry_projection_backlog_threshold),
            TelemetryStage.BROADCAST: max(1, settings.telemetry_broadcast_backlog_threshold),
            TelemetryStage.ENERGY: max(1, settings.telemetry_energy_backlog_threshold),
            TelemetryStage.RULES: max(1, settings.telemetry_rules_backlog_threshold),
        }
        self._groups_ready = False

    async def ensure_groups(self, *, force: bool = False) -> None:
        if self._groups_ready and not force:
            return
        for config in self._stage_config.values():
            try:
                await self._redis.xgroup_create(
                    config["stream"],
                    config["group"],
                    id="0",
                    mkstream=True,
                )
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
        self._groups_ready = True

    @staticmethod
    def _is_missing_group_error(exc: Exception) -> bool:
        message = str(exc).upper()
        return "NOGROUP" in message or "NO SUCH KEY" in message

    async def _recover_missing_groups(self, *, stage: TelemetryStage, exc: Exception) -> None:
        logger.warning(
            "Telemetry stream consumer group missing; recreating groups",
            stage=stage.value,
            error=str(exc),
        )
        self._groups_ready = False
        await self.ensure_groups(force=True)

    async def close(self) -> None:
        await self._redis.close()

    async def publish(
        self,
        *,
        stage: TelemetryStage,
        payload: BaseModel | dict[str, Any],
        correlation_id: str,
        attempt: int = 1,
    ) -> str:
        await self.ensure_groups()
        config = self._stage_config[stage]
        stream_name = config["stream"]
        stream_length = int(await self._redis.xlen(stream_name))
        if stream_length >= int(self._stage_publish_limits[stage]):
            raise RuntimeError(f"{stage.value} stream backlog threshold reached")
        payload_dict = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        return await self._redis.xadd(
            stream_name,
            {
                "correlation_id": correlation_id,
                "attempt": str(max(1, attempt)),
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
                "payload_json": json.dumps(payload_dict, separators=(",", ":"), sort_keys=True, default=str),
            },
            maxlen=int(config["maxlen"]),
            approximate=True,
        )

    async def read_batch(
        self,
        *,
        stage: TelemetryStage,
        consumer_name: str,
        batch_size: int,
        block_ms: int,
    ) -> list[QueueMessage]:
        await self.ensure_groups()
        config = self._stage_config[stage]
        try:
            entries = await self._redis.xreadgroup(
                groupname=config["group"],
                consumername=consumer_name,
                streams={config["stream"]: ">"},
                count=max(1, batch_size),
                block=max(1, block_ms),
            )
        except Exception as exc:
            if not self._is_missing_group_error(exc):
                raise
            await self._recover_missing_groups(stage=stage, exc=exc)
            entries = await self._redis.xreadgroup(
                groupname=config["group"],
                consumername=consumer_name,
                streams={config["stream"]: ">"},
                count=max(1, batch_size),
                block=max(1, block_ms),
            )
        return self._decode_entries(stage=stage, entries=entries)

    async def reclaim_stale(
        self,
        *,
        stage: TelemetryStage,
        consumer_name: str,
        min_idle_ms: int,
        batch_size: int,
    ) -> list[QueueMessage]:
        await self.ensure_groups()
        config = self._stage_config[stage]
        try:
            _, entries, _ = await self._redis.xautoclaim(
                config["stream"],
                config["group"],
                consumer_name,
                min_idle_time=max(1, min_idle_ms),
                start_id="0-0",
                count=max(1, batch_size),
            )
        except Exception as exc:
            if not self._is_missing_group_error(exc):
                return []
            await self._recover_missing_groups(stage=stage, exc=exc)
            try:
                _, entries, _ = await self._redis.xautoclaim(
                    config["stream"],
                    config["group"],
                    consumer_name,
                    min_idle_time=max(1, min_idle_ms),
                    start_id="0-0",
                    count=max(1, batch_size),
                )
            except Exception:
                return []
        return self._decode_entries(stage=stage, entries=[(config["stream"], entries)])

    async def ack(self, *, stage: TelemetryStage, message_ids: list[str]) -> None:
        if not message_ids:
            return
        config = self._stage_config[stage]
        try:
            await self._redis.xack(config["stream"], config["group"], *message_ids)
        except Exception as exc:
            if not self._is_missing_group_error(exc):
                raise
            await self._recover_missing_groups(stage=stage, exc=exc)
        await self._redis.xdel(config["stream"], *message_ids)

    async def dead_letter(
        self,
        *,
        stage: TelemetryStage,
        message: QueueMessage,
        reason: str,
        terminal_payload: Mapping[str, Any] | None = None,
    ) -> None:
        await self._redis.xadd(
            self._dead_letter_stream,
            {
                "stage": stage.value,
                "message_id": message.message_id,
                "attempt": str(message.attempt),
                "correlation_id": message.correlation_id,
                "reason": reason[:2048],
                "payload_json": json.dumps(
                    dict(terminal_payload or message.payload),
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                ),
                "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=max(1, settings.telemetry_dead_letter_stream_maxlen),
            approximate=True,
        )
        await self.ack(stage=stage, message_ids=[message.message_id])

    async def metrics(self) -> dict[str, Any]:
        await self.ensure_groups()
        data: dict[str, Any] = {
            "dead_letter_depth": int(await self._redis.xlen(self._dead_letter_stream)),
            "stages": {},
            "counters": await self.counters(),
            "workers": await self.worker_health(),
        }
        for stage, config in self._stage_config.items():
            group_info = await self._group_info(stage)
            data["stages"][stage.value] = {
                "stream": config["stream"],
                "consumer_group": config["group"],
                "backlog_depth": int(await self._redis.xlen(config["stream"])),
                "pending": int(group_info.get("pending") or 0),
                "lag": int(group_info.get("lag") or 0),
                "consumers": int(group_info.get("consumers") or 0),
                "oldest_age_seconds": round(await self._oldest_age_seconds(config["stream"]), 3),
            }
        return data

    async def incr_counter(self, name: str, amount: int = 1) -> None:
        await self._redis.hincrby(self._metrics_key, name, int(amount))

    async def counters(self) -> dict[str, int]:
        raw = await self._redis.hgetall(self._metrics_key)
        return {str(key): int(value) for key, value in (raw or {}).items()}

    async def record_worker_health(
        self,
        *,
        worker_name: str,
        role: str,
        ready: bool,
        maintenance_enabled: bool,
        stages: list[str],
        inflight: Mapping[str, int] | None = None,
    ) -> None:
        key = f"{self._worker_health_key_prefix}:{worker_name}"
        payload = {
            "worker_name": worker_name,
            "role": role,
            "ready": "true" if ready else "false",
            "maintenance_enabled": "true" if maintenance_enabled else "false",
            "stages": json.dumps(sorted(set(stages))),
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "inflight": json.dumps(dict(inflight or {}), separators=(",", ":"), sort_keys=True),
        }
        await self._redis.sadd(self._worker_health_members_key, worker_name)
        await self._redis.hset(key, mapping=payload)
        await self._redis.expire(key, max(15, settings.telemetry_worker_heartbeat_ttl_seconds))

    async def worker_health(self) -> dict[str, dict[str, Any]]:
        worker_names = await self._redis.smembers(self._worker_health_members_key)
        results: dict[str, dict[str, Any]] = {}
        for worker_name in sorted(worker_names or []):
            key = f"{self._worker_health_key_prefix}:{worker_name}"
            payload = await self._redis.hgetall(key)
            if not payload:
                await self._redis.srem(self._worker_health_members_key, worker_name)
                continue
            inflight_raw = payload.get("inflight") or "{}"
            stages_raw = payload.get("stages") or "[]"
            try:
                inflight = json.loads(inflight_raw)
            except Exception:
                inflight = {}
            try:
                stages = json.loads(stages_raw)
            except Exception:
                stages = []
            results[str(worker_name)] = {
                "role": payload.get("role") or "worker",
                "ready": str(payload.get("ready") or "").lower() == "true",
                "maintenance_enabled": str(payload.get("maintenance_enabled") or "").lower() == "true",
                "stages": stages,
                "last_heartbeat": payload.get("last_heartbeat"),
                "inflight": inflight,
            }
        return results

    async def _group_info(self, stage: TelemetryStage) -> dict[str, Any]:
        config = self._stage_config[stage]
        try:
            groups = await self._redis.xinfo_groups(config["stream"])
        except Exception as exc:
            if not self._is_missing_group_error(exc):
                raise
            await self._recover_missing_groups(stage=stage, exc=exc)
            groups = await self._redis.xinfo_groups(config["stream"])
        for group in groups or []:
            if str(group.get("name")) == config["group"]:
                return dict(group)
        return {}

    async def _oldest_age_seconds(self, stream_name: str) -> float:
        entries = await self._redis.xrange(stream_name, count=1)
        if not entries:
            return 0.0
        entry_id = entries[0][0]
        try:
            millis = int(str(entry_id).split("-", 1)[0])
        except Exception:
            return 0.0
        created_at = datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())

    def _decode_entries(
        self,
        *,
        stage: TelemetryStage,
        entries: list[tuple[str, list[tuple[str, Mapping[str, str]]]]],
    ) -> list[QueueMessage]:
        decoded: list[QueueMessage] = []
        for _stream_name, records in entries or []:
            for message_id, fields in records or []:
                payload_json = str(fields.get("payload_json") or "{}")
                enqueued_at_raw = str(fields.get("enqueued_at") or datetime.now(timezone.utc).isoformat())
                try:
                    enqueued_at = datetime.fromisoformat(enqueued_at_raw.replace("Z", "+00:00"))
                except Exception:
                    enqueued_at = datetime.now(timezone.utc)
                decoded.append(
                    QueueMessage(
                        stage=stage,
                        message_id=message_id,
                        attempt=int(fields.get("attempt") or 1),
                        correlation_id=str(fields.get("correlation_id") or ""),
                        enqueued_at=enqueued_at,
                        payload=json.loads(payload_json),
                    )
                )
        return decoded
