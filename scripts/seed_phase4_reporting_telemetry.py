#!/usr/bin/env python3
"""Backfill telemetry for Phase 4 reporting validation devices.

Publishes dated MQTT samples for the certification tenant so fresh report
validation can exercise populated energy and hidden-overconsumption branches.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt


IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class DeviceProfile:
    device_id: str
    scale: float


DEFAULT_DEVICES = (
    DeviceProfile("AD00000004", 1.05),
    DeviceProfile("AD00000005", 0.95),
    DeviceProfile("AD00000006", 1.00),
    DeviceProfile("AD00000007", 1.10),
    DeviceProfile("AD00000008", 0.90),
)


def _power_profile(kind: str, scale: float) -> list[float]:
    if kind == "above":
        return [0.9 * scale] * 19 + [2.8 * scale] * 5
    if kind == "below":
        return [2.2 * scale] * 18 + [0.35 * scale] * 6
    if kind == "mixed":
        return (
            [0.8 * scale] * 8
            + [2.4 * scale] * 4
            + [0.7 * scale] * 8
            + [1.8 * scale] * 4
        )
    raise ValueError(f"Unknown day profile kind: {kind}")


def _sample_window_bounds(now_local: datetime) -> tuple[datetime, datetime]:
    start_day = now_local.date() - timedelta(days=2)
    start_local = datetime.combine(start_day, dt_time(hour=0, minute=30), tzinfo=IST)
    end_local = now_local.replace(minute=0, second=0, microsecond=0)
    if end_local < start_local:
        raise ValueError("Current local time is earlier than telemetry seed window start.")
    return start_local, end_local


def _day_kind(day: date, now_local: datetime) -> str:
    day_index = (day - (now_local.date() - timedelta(days=2))).days
    if day_index <= 0:
        return "above"
    if day_index == 1:
        return "below"
    return "mixed"


def _build_samples(device: DeviceProfile, *, now_local: datetime) -> list[dict]:
    start_local, end_local = _sample_window_bounds(now_local)
    energy_kwh = 1000.0 + (hash(device.device_id) % 50)
    samples: list[dict] = []

    cursor = start_local
    while cursor <= end_local:
        powers_kw = _power_profile(_day_kind(cursor.date(), now_local), device.scale)
        power_kw = powers_kw[cursor.hour % len(powers_kw)]
        ts_utc = cursor.astimezone(timezone.utc)
        power_w = round(power_kw * 1000, 3)
        power_factor = 0.96
        current_a = round(power_w / (230.0 * power_factor), 3)
        energy_kwh = round(energy_kwh + power_kw, 6)
        samples.append(
            {
                "device_id": device.device_id,
                "timestamp": ts_utc.isoformat(),
                "schema_version": "v1",
                "voltage": 230.0,
                "current": current_a,
                "power": power_w,
                "power_factor": power_factor,
                "energy_kwh": energy_kwh,
            }
        )
        cursor += timedelta(hours=1)
    return samples


def publish_samples(
    *,
    tenant_id: str,
    broker_host: str,
    broker_port: int,
    devices: tuple[DeviceProfile, ...],
    sleep_sec: float,
) -> None:
    now_local = datetime.now(IST)
    client = mqtt.Client(client_id=f"phase4-seed-{int(time.time())}", clean_session=True)
    client.connect(broker_host, broker_port, 60)
    client.loop_start()
    time.sleep(0.5)

    total = 0
    try:
        for device in devices:
            for sample in _build_samples(device, now_local=now_local):
                topic = f"{tenant_id}/devices/{device.device_id}/telemetry"
                payload = {**sample, "tenant_id": tenant_id}
                info = client.publish(topic, json.dumps(payload), qos=1)
                info.wait_for_publish(timeout=10)
                total += 1
                if sleep_sec > 0:
                    time.sleep(sleep_sec)
    finally:
        client.loop_stop()
        client.disconnect()

    print(
        json.dumps(
            {
                "tenant_id": tenant_id,
                "broker_host": broker_host,
                "broker_port": broker_port,
                "devices": [device.device_id for device in devices],
                "samples_published": total,
                "start_date_local": (now_local.date() - timedelta(days=2)).isoformat(),
                "end_date_local": now_local.date().isoformat(),
                "end_hour_local": now_local.replace(minute=0, second=0, microsecond=0).isoformat(),
            },
            indent=2,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed MQTT telemetry for Phase 4 reporting validation.")
    parser.add_argument("--tenant-id", default="SH00000003")
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--sleep-sec", type=float, default=0.01)
    parser.add_argument(
        "--devices",
        nargs="*",
        default=[profile.device_id for profile in DEFAULT_DEVICES],
        help="Device IDs to seed. Defaults to certification validation devices.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    by_id = {profile.device_id: profile for profile in DEFAULT_DEVICES}
    devices = tuple(by_id[device_id] for device_id in args.devices if device_id in by_id)
    if not devices:
        raise SystemExit("No supported devices selected for seeding.")

    publish_samples(
        tenant_id=args.tenant_id,
        broker_host=args.broker_host,
        broker_port=args.broker_port,
        devices=devices,
        sleep_sec=args.sleep_sec,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
