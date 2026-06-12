from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import pytest

from tests._bootstrap import bootstrap_paths

bootstrap_paths()

from src.queue.telemetry_stream import RedisTelemetryStreamQueue, TelemetryIngressEnvelope, TelemetryStage


class _FakeRedisStore:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = defaultdict(list)
        self.groups: set[tuple[str, str]] = set()
        self.deleted: set[str] = set()
        self.counter = 0
        self.hashes: dict[str, dict[str, str]] = defaultdict(dict)
        self.sets: dict[str, set[str]] = defaultdict(set)


class _FakeRedis:
    def __init__(self, store: _FakeRedisStore) -> None:
        self.store = store

    @classmethod
    def from_url(cls, _url: str, decode_responses: bool = True, max_connections: int | None = None):
        assert decode_responses is True
        assert max_connections is None or max_connections >= 1
        return cls(cls._store)

    async def xgroup_create(self, stream: str, group: str, id: str = "0", mkstream: bool = True):
        del id, mkstream
        self.store.groups.add((stream, group))

    async def xlen(self, stream: str) -> int:
        return len([entry for entry in self.store.streams[stream] if entry[0] not in self.store.deleted])

    async def xadd(self, stream: str, payload: dict[str, str], maxlen: int = 0, approximate: bool = False):
        del maxlen, approximate
        self.store.counter += 1
        message_id = f"{self.store.counter}-0"
        self.store.streams[stream].append((message_id, dict(payload)))
        return message_id

    async def xreadgroup(self, groupname: str, consumername: str, streams: dict[str, str], count: int, block: int):
        del consumername, count, block
        stream = next(iter(streams.keys()))
        if (stream, groupname) not in self.store.groups:
            raise RuntimeError("NOGROUP No such key or consumer group")
        items = [(message_id, payload) for message_id, payload in self.store.streams[stream] if message_id not in self.store.deleted]
        if not items:
            return []
        return [(stream, [items[0]])]

    async def xautoclaim(self, stream: str, group: str, consumer: str, min_idle_time: int, start_id: str, count: int):
        del consumer, min_idle_time, start_id, count
        if (stream, group) not in self.store.groups:
            raise RuntimeError("NOGROUP No such key or consumer group")
        return "0-0", [], "0-0"

    async def xack(self, stream: str, group: str, *message_ids: str):
        if (stream, group) not in self.store.groups:
            raise RuntimeError("NOGROUP No such key or consumer group")
        return len(message_ids)

    async def xdel(self, stream: str, *message_ids: str):
        del stream
        self.store.deleted.update(message_ids)
        return len(message_ids)

    async def xinfo_groups(self, stream: str):
        return [
            {
                "name": group,
                "pending": 0,
                "lag": await self.xlen(stream),
                "consumers": 1,
            }
            for candidate_stream, group in self.store.groups
            if candidate_stream == stream
        ]

    async def xrange(self, stream: str, count: int = 1):
        del count
        return [(message_id, payload) for message_id, payload in self.store.streams[stream] if message_id not in self.store.deleted][:1]

    async def close(self):
        return None

    async def sadd(self, key: str, value: str):
        self.store.sets[key].add(value)
        return 1

    async def smembers(self, key: str):
        return set(self.store.sets.get(key, set()))

    async def srem(self, key: str, value: str):
        self.store.sets[key].discard(value)
        return 1

    async def hset(self, key: str, mapping: dict[str, str]):
        self.store.hashes[key].update(mapping)
        return len(mapping)

    async def hgetall(self, key: str):
        return dict(self.store.hashes.get(key, {}))

    async def expire(self, key: str, ttl: int):
        del key, ttl
        return True


def _group_for(queue: RedisTelemetryStreamQueue, stage: TelemetryStage) -> tuple[str, str]:
    config = queue._stage_config[stage]
    return config["stream"], config["group"]


@pytest.mark.asyncio
async def test_ingest_stream_survives_queue_reinstantiation(monkeypatch):
    store = _FakeRedisStore()
    _FakeRedis._store = store
    monkeypatch.setattr("src.queue.telemetry_stream.Redis", _FakeRedis)

    queue_one = RedisTelemetryStreamQueue(redis_url="redis://fake")
    await queue_one.ensure_groups()
    await queue_one.publish(
        stage=TelemetryStage.INGEST,
        payload=TelemetryIngressEnvelope(
            raw_payload={
                "device_id": "DEVICE-STREAM-1",
                "tenant_id": "TENANT-A",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "power": 120.0,
                "current": 1.0,
                "voltage": 230.0,
            },
            correlation_id="corr-1",
        ),
        correlation_id="corr-1",
    )
    await queue_one.close()

    queue_two = RedisTelemetryStreamQueue(redis_url="redis://fake")
    await queue_two.ensure_groups()
    messages = await queue_two.read_batch(
        stage=TelemetryStage.INGEST,
        consumer_name="consumer-a",
        batch_size=10,
        block_ms=1,
    )

    assert len(messages) == 1
    assert messages[0].payload["raw_payload"]["device_id"] == "DEVICE-STREAM-1"
    await queue_two.ack(stage=TelemetryStage.INGEST, message_ids=[messages[0].message_id])
    metrics = await queue_two.metrics()
    assert metrics["stages"]["ingest"]["backlog_depth"] == 0


@pytest.mark.asyncio
async def test_worker_health_is_exposed_in_metrics(monkeypatch):
    store = _FakeRedisStore()
    _FakeRedis._store = store
    monkeypatch.setattr("src.queue.telemetry_stream.Redis", _FakeRedis)

    queue = RedisTelemetryStreamQueue(redis_url="redis://fake")
    await queue.record_worker_health(
        worker_name="worker-a",
        role="worker",
        ready=True,
        maintenance_enabled=False,
        stages=["projection", "rules"],
        inflight={"projection": 12},
    )
    metrics = await queue.metrics()
    assert metrics["workers"]["worker-a"]["ready"] is True
    assert metrics["workers"]["worker-a"]["inflight"]["projection"] == 12


@pytest.mark.asyncio
async def test_read_batch_recreates_missing_groups_after_initial_bootstrap(monkeypatch):
    store = _FakeRedisStore()
    _FakeRedis._store = store
    monkeypatch.setattr("src.queue.telemetry_stream.Redis", _FakeRedis)

    queue = RedisTelemetryStreamQueue(redis_url="redis://fake")
    await queue.ensure_groups()
    await queue.publish(
        stage=TelemetryStage.INGEST,
        payload=TelemetryIngressEnvelope(
            raw_payload={
                "device_id": "DEVICE-STREAM-2",
                "tenant_id": "TENANT-A",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "power": 125.0,
                "current": 1.1,
                "voltage": 231.0,
            },
            correlation_id="corr-2",
        ),
        correlation_id="corr-2",
    )

    store.groups.clear()

    messages = await queue.read_batch(
        stage=TelemetryStage.INGEST,
        consumer_name="consumer-b",
        batch_size=10,
        block_ms=1,
    )

    assert len(messages) == 1
    assert _group_for(queue, TelemetryStage.INGEST) in store.groups


@pytest.mark.asyncio
async def test_reclaim_stale_recreates_missing_groups_after_initial_bootstrap(monkeypatch):
    store = _FakeRedisStore()
    _FakeRedis._store = store
    monkeypatch.setattr("src.queue.telemetry_stream.Redis", _FakeRedis)

    queue = RedisTelemetryStreamQueue(redis_url="redis://fake")
    await queue.ensure_groups()

    store.groups.clear()

    messages = await queue.reclaim_stale(
        stage=TelemetryStage.PROJECTION,
        consumer_name="consumer-c",
        min_idle_ms=1,
        batch_size=10,
    )

    assert messages == []
    assert _group_for(queue, TelemetryStage.PROJECTION) in store.groups


@pytest.mark.asyncio
async def test_ack_recreates_missing_groups_after_initial_bootstrap(monkeypatch):
    store = _FakeRedisStore()
    _FakeRedis._store = store
    monkeypatch.setattr("src.queue.telemetry_stream.Redis", _FakeRedis)

    queue = RedisTelemetryStreamQueue(redis_url="redis://fake")
    await queue.ensure_groups()
    await queue.publish(
        stage=TelemetryStage.INGEST,
        payload=TelemetryIngressEnvelope(
            raw_payload={
                "device_id": "DEVICE-STREAM-3",
                "tenant_id": "TENANT-A",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "power": 130.0,
                "current": 1.2,
                "voltage": 232.0,
            },
            correlation_id="corr-3",
        ),
        correlation_id="corr-3",
    )

    messages = await queue.read_batch(
        stage=TelemetryStage.INGEST,
        consumer_name="consumer-d",
        batch_size=10,
        block_ms=1,
    )
    assert len(messages) == 1

    store.groups.clear()

    await queue.ack(stage=TelemetryStage.INGEST, message_ids=[messages[0].message_id])

    assert _group_for(queue, TelemetryStage.INGEST) in store.groups
    metrics = await queue.metrics()
    assert metrics["stages"]["ingest"]["backlog_depth"] == 0
