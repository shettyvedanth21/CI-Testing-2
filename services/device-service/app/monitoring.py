"""Prometheus metrics and fleet stream broadcaster primitives."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from redis.asyncio import Redis


SNAPSHOT_AGE_SECONDS = Gauge(
    "dashboard_snapshot_age_seconds",
    "Age of dashboard snapshots in seconds",
    ["snapshot_key"],
)
SNAPSHOT_MATERIALIZE_DURATION_SECONDS = Histogram(
    "dashboard_snapshot_materialize_duration_seconds",
    "Duration to materialize dashboard snapshots",
    ["snapshot_key"],
)
SNAPSHOT_MATERIALIZE_FAILURES_TOTAL = Counter(
    "dashboard_snapshot_materialize_failures_total",
    "Number of snapshot materialization failures",
    ["snapshot_key", "reason"],
)
DASHBOARD_SCHEDULER_LAG_SECONDS = Gauge(
    "dashboard_scheduler_lag_seconds",
    "Observed lag for the dashboard snapshot scheduler",
)

FLEET_STREAM_CONNECTED_CLIENTS = Gauge(
    "fleet_stream_connected_clients",
    "Number of active fleet stream clients",
)
FLEET_STREAM_EMIT_LAG_SECONDS = Histogram(
    "fleet_stream_emit_lag_seconds",
    "Lag between event creation and stream emission",
)
FLEET_STREAM_EVENTS_TOTAL = Counter(
    "fleet_stream_events_total",
    "Total fleet stream events produced",
    ["event_type"],
)
FLEET_STREAM_DISCONNECTS_TOTAL = Counter(
    "fleet_stream_disconnects_total",
    "Fleet stream disconnections",
    ["reason"],
)
FLEET_STREAM_QUEUE_DEPTH = Gauge(
    "fleet_stream_queue_depth",
    "Max queue depth across connected fleet stream clients",
)
FLEET_STREAM_QUEUE_DROPS_TOTAL = Counter(
    "fleet_stream_queue_drops_total",
    "Dropped fleet stream events due to queue backpressure",
)

DASHBOARD_COST_DATA_AGE_SECONDS = Gauge(
    "dashboard_cost_data_age_seconds",
    "Age of dashboard INR cost data in seconds",
)
DASHBOARD_COST_DATA_STATE_TOTAL = Counter(
    "dashboard_cost_data_state_total",
    "Observed dashboard INR cost data states",
    ["state"],
)
DASHBOARD_COST_REFRESH_FAILURES_TOTAL = Counter(
    "dashboard_cost_refresh_failures_total",
    "Dashboard INR cost refresh failures",
    ["reason"],
)
CALENDAR_COST_SNAPSHOT_AGE_SECONDS = Gauge(
    "calendar_cost_snapshot_age_seconds",
    "Age of monthly calendar INR cost snapshot in seconds",
)
DEVICE_LIVE_UPDATE_BATCH_DURATION_SECONDS = Histogram(
    "device_live_update_batch_duration_seconds",
    "Duration of device live-update batch requests in seconds",
    ["outcome"],
)
DEVICE_LIVE_UPDATE_BATCH_ROWS = Histogram(
    "device_live_update_batch_rows",
    "Number of rows in each device live-update batch request",
)
DEVICE_LIVE_UPDATE_BATCH_ITEMS_TOTAL = Counter(
    "device_live_update_batch_items_total",
    "Per-item outcomes for device live-update batch requests",
    ["outcome"],
)
DEVICE_LIVE_UPDATE_BATCH_CHUNK_FALLBACK_TOTAL = Counter(
    "device_live_update_batch_chunk_fallback_total",
    "Number of times a chunk-level savepoint failed and fell back to per-item savepoint isolation",
)
DEVICE_LIVE_UPDATE_BATCH_VERSION_CONFLICT_TOTAL = Counter(
    "device_live_update_batch_version_conflict_total",
    "Number of version-guarded batch projection writes that detected an unexpected concurrent update",
)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


@dataclass
class FleetStreamMessage:
    id: str
    event: str
    data: dict[str, Any]
    created_at: datetime


class FleetStreamBroadcaster:
    """Fan out fleet update events to stream subscribers with bounded queues."""

    def __init__(self, queue_size: int = 64):
        self._queue_size = max(1, queue_size)
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, dict[int, asyncio.Queue[FleetStreamMessage]]] = {}
        self._next_subscriber_id = 0
        self._next_event_id_by_tenant: dict[str, int] = {}
        self._redis: Optional[Redis] = None
        self._redis_channel_template: Optional[str] = None
        self._redis_pubsubs: dict[str, Any] = {}
        self._redis_listener_tasks: dict[str, asyncio.Task] = {}

    async def start(self, redis_url: Optional[str], channel_template: Optional[str]) -> None:
        if not redis_url or not channel_template:
            return
        try:
            self._redis = Redis.from_url(redis_url, decode_responses=True)
            await self._redis.ping()
            self._redis_channel_template = channel_template
        except Exception:
            self._redis = None
            self._redis_channel_template = None

    async def stop(self) -> None:
        tasks = list(self._redis_listener_tasks.values())
        pubsubs = list(self._redis_pubsubs.values())
        self._redis_listener_tasks = {}
        self._redis_pubsubs = {}

        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        for pubsub in pubsubs:
            try:
                await pubsub.close()
            except Exception:
                pass
        if self._redis is not None:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None
        self._redis_channel_template = None

    def configure_queue_size(self, queue_size: int) -> None:
        self._queue_size = max(1, queue_size)

    def latest_event_id(self, tenant_id: str) -> str:
        return str(self._next_event_id_by_tenant.get(tenant_id, 0))

    async def subscribe(self, tenant_id: str) -> tuple[int, asyncio.Queue[FleetStreamMessage]]:
        async with self._lock:
            self._next_subscriber_id += 1
            subscriber_id = self._next_subscriber_id
            queue: asyncio.Queue[FleetStreamMessage] = asyncio.Queue(maxsize=self._queue_size)
            tenant_subscribers = self._subscribers.setdefault(tenant_id, {})
            tenant_subscribers[subscriber_id] = queue
            FLEET_STREAM_CONNECTED_CLIENTS.set(sum(len(queues) for queues in self._subscribers.values()))
            self._set_queue_depth()
            if self._redis is not None and tenant_id not in self._redis_pubsubs:
                pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(self._redis_channel_for_tenant(tenant_id))
                self._redis_pubsubs[tenant_id] = pubsub
                self._redis_listener_tasks[tenant_id] = asyncio.create_task(
                    self._redis_listener_loop(tenant_id, pubsub)
                )
        return subscriber_id, queue

    async def unsubscribe(self, tenant_id: str, subscriber_id: int, reason: str = "client_disconnect") -> None:
        existed = False
        pubsub = None
        listener_task = None
        async with self._lock:
            tenant_subscribers = self._subscribers.get(tenant_id)
            if tenant_subscribers is not None:
                existed = tenant_subscribers.pop(subscriber_id, None) is not None
                if not tenant_subscribers:
                    self._subscribers.pop(tenant_id, None)
                    pubsub = self._redis_pubsubs.pop(tenant_id, None)
                    listener_task = self._redis_listener_tasks.pop(tenant_id, None)
            FLEET_STREAM_CONNECTED_CLIENTS.set(sum(len(queues) for queues in self._subscribers.values()))
            self._set_queue_depth()
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if pubsub is not None:
            try:
                await pubsub.close()
            except Exception:
                pass
        if existed:
            FLEET_STREAM_DISCONNECTS_TOTAL.labels(reason=reason).inc()

    async def publish(self, tenant_id: str, event: str, data: dict[str, Any]) -> FleetStreamMessage:
        if self._redis and self._redis_channel_template:
            try:
                event_id = await self._redis.incr(self._redis_event_counter_key(tenant_id))
                now = datetime.now(timezone.utc)
                payload = {
                    "tenant_id": tenant_id,
                    "id": str(event_id),
                    "event": event,
                    "data": data,
                    "created_at": now.isoformat(),
                }
                await self._redis.publish(
                    self._redis_channel_for_tenant(tenant_id),
                    json.dumps(payload, separators=(",", ":")),
                )
                message = FleetStreamMessage(
                    id=str(event_id),
                    event=event,
                    data=data,
                    created_at=now,
                )
                if tenant_id not in self._redis_listener_tasks or self._redis_listener_tasks[tenant_id].done():
                    await self._fanout_local(tenant_id, message)
                else:
                    async with self._lock:
                        self._next_event_id_by_tenant[tenant_id] = max(
                            self._next_event_id_by_tenant.get(tenant_id, 0),
                            int(event_id),
                        )
                FLEET_STREAM_EVENTS_TOTAL.labels(event_type=event).inc()
                return message
            except Exception:
                pass

        now = datetime.now(timezone.utc)
        async with self._lock:
            next_event_id = self._next_event_id_by_tenant.get(tenant_id, 0) + 1
            self._next_event_id_by_tenant[tenant_id] = next_event_id
            message = FleetStreamMessage(
                id=str(next_event_id),
                event=event,
                data=data,
                created_at=now,
            )
        await self._fanout_local(tenant_id, message)
        FLEET_STREAM_EVENTS_TOTAL.labels(event_type=event).inc()
        return message

    async def _fanout_local(self, tenant_id: str, message: FleetStreamMessage) -> None:
        stale_ids: list[int] = []
        pubsub = None
        listener_task = None
        async with self._lock:
            try:
                self._next_event_id_by_tenant[tenant_id] = max(
                    self._next_event_id_by_tenant.get(tenant_id, 0),
                    int(message.id),
                )
            except (TypeError, ValueError):
                pass
            tenant_subscribers = self._subscribers.get(tenant_id, {})
            for subscriber_id, queue in tenant_subscribers.items():
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    stale_ids.append(subscriber_id)
                    FLEET_STREAM_QUEUE_DROPS_TOTAL.inc()
                    FLEET_STREAM_DISCONNECTS_TOTAL.labels(reason="queue_overflow").inc()

            for subscriber_id in stale_ids:
                tenant_subscribers.pop(subscriber_id, None)
            if tenant_subscribers:
                self._subscribers[tenant_id] = tenant_subscribers
            else:
                self._subscribers.pop(tenant_id, None)
                pubsub = self._redis_pubsubs.pop(tenant_id, None)
                listener_task = self._redis_listener_tasks.pop(tenant_id, None)

            FLEET_STREAM_CONNECTED_CLIENTS.set(sum(len(queues) for queues in self._subscribers.values()))
            self._set_queue_depth()
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if pubsub is not None:
            try:
                await pubsub.close()
            except Exception:
                pass

    async def _redis_listener_loop(self, tenant_id: str, pubsub: Any) -> None:
        while True:
            try:
                raw = await pubsub.get_message(timeout=1.0)
                if not raw:
                    await asyncio.sleep(0.01)
                    continue
                data = raw.get("data")
                if not isinstance(data, str):
                    continue
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    continue
                created_raw = payload.get("created_at")
                created_at = datetime.now(timezone.utc)
                if isinstance(created_raw, str):
                    try:
                        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                    except Exception:
                        created_at = datetime.now(timezone.utc)
                msg = FleetStreamMessage(
                    id=str(payload.get("id") or self.latest_event_id(tenant_id)),
                    event=str(payload.get("event") or "fleet_update"),
                    data=dict(payload.get("data") or {}),
                    created_at=created_at,
                )
                await self._fanout_local(tenant_id, msg)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.1)

    def _redis_channel_for_tenant(self, tenant_id: str) -> str:
        if not self._redis_channel_template:
            raise RuntimeError("Fleet stream Redis channel template is not configured")
        return self._redis_channel_template.format(tenant_id=tenant_id)

    @staticmethod
    def _redis_event_counter_key(tenant_id: str) -> str:
        return f"factoryops:fleet_stream:{tenant_id}:event_id"

    def _set_queue_depth(self) -> None:
        if not self._subscribers:
            FLEET_STREAM_QUEUE_DEPTH.set(0)
            return
        max_depth = max(
            queue.qsize()
            for tenant_subscribers in self._subscribers.values()
            for queue in tenant_subscribers.values()
        )
        FLEET_STREAM_QUEUE_DEPTH.set(max_depth)


fleet_stream_broadcaster = FleetStreamBroadcaster()


def configure_fleet_stream_broadcaster(queue_size: int) -> None:
    """Reset broadcaster queue policy during startup before subscriptions begin."""
    fleet_stream_broadcaster.configure_queue_size(max(1, queue_size))
