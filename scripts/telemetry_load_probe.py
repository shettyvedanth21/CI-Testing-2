#!/usr/bin/env python3
"""Publish synthetic MQTT telemetry and collect live runtime evidence."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import httpx
import paho.mqtt.client as mqtt
import pymysql


def _device_id(index: int) -> str:
    return f"P6E{index:04d}"


def _sample(index: int) -> tuple[float, float]:
    pattern = index % 3
    if pattern == 1:
        return 0.8, 180.0
    if pattern == 2:
        return 1.6, 360.0
    return 0.1, 20.0


def _health(client: httpx.Client, base_url: str, *, timeout: float = 30.0) -> dict:
    return client.get(f"{base_url.rstrip('/')}/health", timeout=timeout).json()


def _counters(payload: dict) -> dict[str, int]:
    telemetry = payload.get("telemetry") or {}
    shared = telemetry.get("shared_counters") or {}
    return {str(key): int(value) for key, value in shared.items()}


def _stage_backlogs(payload: dict) -> dict[str, int]:
    telemetry = payload.get("telemetry") or {}
    stages = telemetry.get("stages") or {}
    return {
        stage: int(details.get("backlog_depth") or 0)
        for stage, details in stages.items()
    }


def _stage_oldest_age(payload: dict) -> dict[str, float]:
    telemetry = payload.get("telemetry") or {}
    stages = telemetry.get("stages") or {}
    return {
        stage: float(details.get("oldest_age_seconds") or 0.0)
        for stage, details in stages.items()
    }


def _query_mysql(args, start_index: int, device_count: int) -> dict:
    start_id = _device_id(start_index)
    end_id = _device_id(start_index + device_count - 1)
    conn = pymysql.connect(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM device_live_state
                WHERE device_id BETWEEN %s AND %s
                """,
                (start_id, end_id),
            )
            live_state_count = int(cursor.fetchone()["count"])

            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM telemetry_outbox
                WHERE device_id BETWEEN %s AND %s
                """,
                (start_id, end_id),
            )
            outbox_count = int(cursor.fetchone()["count"])

            cursor.execute(
                """
                SELECT device_id, load_state, runtime_status, last_current_a, last_telemetry_ts
                FROM device_live_state
                WHERE device_id IN (%s, %s, %s)
                ORDER BY device_id
                """,
                (start_id, _device_id(start_index + 1), _device_id(start_index + 2)),
            )
            samples = cursor.fetchall()

        return {
            "live_state_count": live_state_count,
            "outbox_count": outbox_count,
            "sample_live_state": samples,
        }
    finally:
        conn.close()


def _validate_device_contract(args, start_index: int, device_count: int) -> None:
    start_id = _device_id(start_index)
    end_id = _device_id(start_index + device_count - 1)
    conn = pymysql.connect(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT device_id, COALESCE(LOWER(TRIM(phase_type)), '<null>') AS phase_type
                FROM devices
                WHERE tenant_id = %s
                  AND device_id BETWEEN %s AND %s
                  AND deleted_at IS NULL
                ORDER BY device_id
                """,
                (args.tenant_id, start_id, end_id),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    if len(rows) != device_count:
        raise RuntimeError(
            f"expected {device_count} devices for load band but found {len(rows)} between {start_id} and {end_id}"
        )

    invalid = [
        {"device_id": row["device_id"], "phase_type": row["phase_type"]}
        for row in rows
        if row["phase_type"] not in {"single", "three", "<null>"}
    ]
    if invalid:
        preview = ", ".join(f"{row['device_id']}={row['phase_type']}" for row in invalid[:10])
        raise RuntimeError(
            "device metadata contract validation failed before load generation: "
            f"invalid phase_type values detected ({preview})"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--band", required=True)
    parser.add_argument("--device-count", type=int, required=True)
    parser.add_argument("--hz", type=float, required=True)
    parser.add_argument("--duration-sec", type=int, default=20)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--tenant-id", default="SH00000001")
    parser.add_argument("--mqtt-host", default="emqx")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--health-url", default="http://data-service:8081")
    parser.add_argument("--mysql-host", default="mysql")
    parser.add_argument("--mysql-port", type=int, default=3306)
    parser.add_argument("--mysql-user", default="energy")
    parser.add_argument("--mysql-password", default="energy")
    parser.add_argument("--mysql-database", default="ai_factoryops")
    parser.add_argument("--drain-timeout-sec", type=int, default=90)
    parser.add_argument("--preflight-idle-sec", type=int, default=10)
    parser.add_argument("--preflight-timeout-sec", type=int, default=120)
    args = parser.parse_args()

    _validate_device_contract(args, args.start_index, args.device_count)

    mqtt_client = mqtt.Client(client_id=f"p6e-{args.band}-{int(time.time())}", clean_session=True)
    mqtt_client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    mqtt_client.loop_start()

    http_client = httpx.Client(timeout=10.0)
    idle_started = time.perf_counter()
    preflight_started = time.perf_counter()
    while True:
        before_health = _health(http_client, args.health_url)
        backlogs = _stage_backlogs(before_health)
        if backlogs and all(value == 0 for value in backlogs.values()):
            if time.perf_counter() - idle_started >= args.preflight_idle_sec:
                break
        else:
            idle_started = time.perf_counter()
        if time.perf_counter() - preflight_started >= args.preflight_timeout_sec:
            raise RuntimeError("preflight clean window not reached before timeout")
        time.sleep(1.0)
    before_counters = _counters(before_health)

    total_ticks = int(args.duration_sec * args.hz)
    attempted = 0
    publish_start = time.perf_counter()
    for tick in range(total_ticks):
        tick_started = time.perf_counter()
        for device_offset in range(args.device_count):
            device_index = args.start_index + device_offset
            device_id = _device_id(device_index)
            current_a, power_w = _sample(device_index)
            payload = {
                "device_id": device_id,
                "tenant_id": args.tenant_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "schema_version": "v1",
                "current": current_a,
                "voltage": 230.0,
                "power": power_w,
            }
            topic = f"{args.tenant_id}/devices/{device_id}/telemetry"
            result = mqtt_client.publish(topic, json.dumps(payload), qos=1)
            result.wait_for_publish()
            attempted += 1

        interval = 1.0 / args.hz
        elapsed = time.perf_counter() - tick_started
        if elapsed < interval:
            time.sleep(interval - elapsed)

    publish_duration = time.perf_counter() - publish_start
    try:
        after_publish_health = _health(http_client, args.health_url)
    except Exception as exc:
        after_publish_health = {"telemetry": {}, "health_error": str(exc)}

    drain_started = time.perf_counter()
    drained = False
    drain_snapshots: list[dict] = []
    while time.perf_counter() - drain_started <= args.drain_timeout_sec:
        try:
            snapshot = _health(http_client, args.health_url)
        except Exception as exc:
            snapshot = {"telemetry": {}, "health_error": str(exc)}
        backlogs = _stage_backlogs(snapshot)
        drain_snapshots.append({"elapsed": round(time.perf_counter() - drain_started, 3), "backlogs": backlogs})
        if backlogs and all(value == 0 for value in backlogs.values()):
            drained = True
            after_drain_health = snapshot
            break
        time.sleep(2.0)
    else:
        try:
            after_drain_health = _health(http_client, args.health_url)
        except Exception as exc:
            after_drain_health = {"telemetry": {}, "health_error": str(exc)}

    mysql_snapshot = _query_mysql(args, args.start_index, args.device_count)

    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    http_client.close()

    after_counters = _counters(after_drain_health)
    delta_counters = {
        key: int(after_counters.get(key, 0)) - int(before_counters.get(key, 0))
        for key in sorted(set(before_counters) | set(after_counters))
    }

    print(
        json.dumps(
            {
                "band": args.band,
                "device_count": args.device_count,
                "hz": args.hz,
                "duration_sec": args.duration_sec,
                "attempted": attempted,
                "publish_duration_sec": round(publish_duration, 3),
                "before_counters": before_counters,
                "after_publish_stage_backlogs": _stage_backlogs(after_publish_health),
                "after_publish_stage_oldest_age_seconds": _stage_oldest_age(after_publish_health),
                "after_drain_stage_backlogs": _stage_backlogs(after_drain_health),
                "after_drain_stage_oldest_age_seconds": _stage_oldest_age(after_drain_health),
                "delta_counters": delta_counters,
                "dead_letter_depth": int((after_drain_health.get("telemetry") or {}).get("dead_letter_depth") or 0),
                "drained": drained,
                "drain_time_sec": round(time.perf_counter() - drain_started, 3),
                "drain_snapshots": drain_snapshots,
                "mysql": mysql_snapshot,
                "worker_health": (after_drain_health.get("telemetry") or {}).get("workers") or {},
                "service_status": after_drain_health.get("status"),
            },
            default=str,
        )
    )


if __name__ == "__main__":
    main()
