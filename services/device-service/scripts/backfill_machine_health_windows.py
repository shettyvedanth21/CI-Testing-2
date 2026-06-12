"""Backfill machine health feature windows from historical Influx telemetry.

This is a maintenance command for the derived machine health tables. It does
not modify raw telemetry and defaults to dry-run mode. Use ``--write`` to
upsert missing/changed rows into ``machine_health_feature_windows``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


_SCRIPT_PATH = Path(__file__).resolve()
SERVICE_DIR = _SCRIPT_PATH.parents[1]
REPO_ROOT = _SCRIPT_PATH.parents[3] if len(_SCRIPT_PATH.parents) > 3 else SERVICE_DIR
for path in (SERVICE_DIR, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


BUSINESS_FIELDS = (
    "current",
    "current_avg",
    "current_l1",
    "current_l2",
    "current_l3",
    "power",
    "power_factor",
    "voltage",
    "voltage_avg",
    "voltage_l1",
    "voltage_l2",
    "voltage_l3",
    "frequency",
    "energy_kwh",
)


@dataclass(frozen=True)
class BackfillTarget:
    tenant_id: str
    device_id: str


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _floor_window(ts: datetime, window_seconds: int) -> datetime:
    ts_utc = ts.astimezone(timezone.utc)
    epoch = int(ts_utc.timestamp())
    floored = epoch - (epoch % window_seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def _safe_flux_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def _get_influx_settings() -> dict[str, str]:
    url = _env("INFLUXDB_URL")
    token = _env("INFLUXDB_TOKEN")
    org = _env("INFLUXDB_ORG", "energy-org")
    bucket = _env("INFLUXDB_BUCKET", "telemetry")
    measurement = _env("INFLUXDB_MEASUREMENT", "device_telemetry")
    missing = [name for name, value in {
        "INFLUXDB_URL": url,
        "INFLUXDB_TOKEN": token,
        "INFLUXDB_ORG": org,
        "INFLUXDB_BUCKET": bucket,
    }.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required InfluxDB env vars: {', '.join(missing)}")
    return {
        "url": str(url),
        "token": str(token),
        "org": str(org),
        "bucket": str(bucket),
        "measurement": str(measurement),
    }


async def _load_targets(args: argparse.Namespace) -> list[BackfillTarget]:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.device import Device

    async with AsyncSessionLocal() as session:
        query = select(Device.tenant_id, Device.device_id)
        if args.tenant_id:
            query = query.where(Device.tenant_id == args.tenant_id)
        if args.device_id:
            query = query.where(Device.device_id == args.device_id)
        result = await session.execute(query.order_by(Device.tenant_id, Device.device_id))
        return [BackfillTarget(tenant_id=row[0], device_id=row[1]) for row in result.all()]


def _query_influx_rows(
    *,
    target: BackfillTarget,
    start: datetime,
    stop: datetime,
    settings: dict[str, str],
) -> list[dict[str, Any]]:
    from influxdb_client import InfluxDBClient

    field_filter = " or ".join(f'r._field == "{_safe_flux_string(field)}"' for field in BUSINESS_FIELDS)
    flux = f'''
from(bucket: "{_safe_flux_string(settings["bucket"])}")
  |> range(start: time(v: "{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"), stop: time(v: "{stop.strftime("%Y-%m-%dT%H:%M:%SZ")}"))
  |> filter(fn: (r) => r._measurement == "{_safe_flux_string(settings["measurement"])}")
  |> filter(fn: (r) => r.device_id == "{_safe_flux_string(target.device_id)}")
  |> filter(fn: (r) => {field_filter})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
'''
    client = InfluxDBClient(url=settings["url"], token=settings["token"], org=settings["org"])
    try:
        tables = client.query_api().query(flux)
        rows: list[dict[str, Any]] = []
        for table in tables:
            for record in table.records:
                ts = record.get_time()
                if ts is None:
                    continue
                row = {"timestamp": ts}
                for field in BUSINESS_FIELDS:
                    if field in record.values:
                        row[field] = record.values.get(field)
                rows.append(row)
        return rows
    finally:
        client.close()


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _rows_to_samples(rows: Iterable[dict[str, Any]]):
    from app.services.degradation.types import TelemetrySample

    samples = []
    for row in rows:
        ts = row.get("timestamp")
        if ts is None:
            continue
        if isinstance(ts, str):
            ts = _parse_dt(ts)
        elif ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        samples.append(
            TelemetrySample(
                timestamp=ts,
                current_avg=_float_or_none(row.get("current_avg", row.get("current"))),
                current_l1=_float_or_none(row.get("current_l1")),
                current_l2=_float_or_none(row.get("current_l2")),
                current_l3=_float_or_none(row.get("current_l3")),
                power=_float_or_none(row.get("power")),
                power_factor=_float_or_none(row.get("power_factor")),
                voltage_avg=_float_or_none(row.get("voltage_avg", row.get("voltage"))),
                voltage_l1=_float_or_none(row.get("voltage_l1")),
                voltage_l2=_float_or_none(row.get("voltage_l2")),
                voltage_l3=_float_or_none(row.get("voltage_l3")),
                frequency=_float_or_none(row.get("frequency")),
                energy_kwh=_float_or_none(row.get("energy_kwh")),
            )
        )
    return samples


async def _existing_window_starts(
    target: BackfillTarget,
    start: datetime,
    stop: datetime,
) -> set[datetime]:
    from sqlalchemy import and_, select

    from app.database import AsyncSessionLocal
    from app.models.device import MachineHealthFeatureWindow

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MachineHealthFeatureWindow.window_start).where(
                and_(
                    MachineHealthFeatureWindow.tenant_id == target.tenant_id,
                    MachineHealthFeatureWindow.device_id == target.device_id,
                    MachineHealthFeatureWindow.window_start >= start,
                    MachineHealthFeatureWindow.window_start < stop,
                )
            )
        )
        starts = set()
        for value in result.scalars().all():
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
            starts.add(value)
        return starts


async def _write_windows(window_dicts: list[dict[str, Any]], batch_size: int) -> int:
    from app.database import AsyncSessionLocal
    from app.services.degradation.service import persist_feature_window

    written = 0
    async with AsyncSessionLocal() as session:
        for index, window_dict in enumerate(window_dicts, start=1):
            await persist_feature_window(session, window_dict)
            written += 1
            if index % batch_size == 0:
                await session.commit()
        await session.commit()
    return written


async def _process_target(
    target: BackfillTarget,
    args: argparse.Namespace,
    settings: dict[str, str],
) -> dict[str, Any]:
    from app.services.degradation.service import build_feature_window_from_samples

    start = _parse_dt(args.start)
    stop = _parse_dt(args.stop)
    chunk = timedelta(hours=max(1, int(args.chunk_hours)))
    window_seconds = max(300, int(args.window_seconds))
    expected_samples = max(0, int(args.expected_sample_count))
    existing = await _existing_window_starts(target, start, stop)

    summary: dict[str, Any] = {
        "tenant_id": target.tenant_id,
        "device_id": target.device_id,
        "dry_run": not args.write,
        "raw_rows": 0,
        "candidate_windows": 0,
        "existing_windows": 0,
        "missing_windows": 0,
        "written_windows": 0,
        "state_counts": {},
        "first_window": None,
        "last_window": None,
    }
    state_counts: dict[str, int] = defaultdict(int)
    pending: list[dict[str, Any]] = []

    chunk_start = start
    while chunk_start < stop:
        chunk_stop = min(stop, chunk_start + chunk)
        rows = await asyncio.to_thread(
            _query_influx_rows,
            target=target,
            start=chunk_start,
            stop=chunk_stop,
            settings=settings,
        )
        summary["raw_rows"] += len(rows)
        grouped: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            ts = row["timestamp"]
            if isinstance(ts, str):
                ts = _parse_dt(ts)
            grouped[_floor_window(ts, window_seconds)].append(row)

        for window_start in sorted(grouped):
            window_end = window_start + timedelta(seconds=window_seconds)
            if window_start < start or window_start >= stop:
                continue
            samples = _rows_to_samples(grouped[window_start])
            if not samples:
                continue
            window_dict = build_feature_window_from_samples(
                samples,
                target.tenant_id,
                target.device_id,
                window_start,
                window_end,
                expected_sample_count=expected_samples,
            )
            summary["candidate_windows"] += 1
            state_counts[str(window_dict["running_state"])] += 1
            if summary["first_window"] is None:
                summary["first_window"] = window_start.isoformat()
            summary["last_window"] = window_start.isoformat()
            exists = window_start in existing
            if exists:
                summary["existing_windows"] += 1
            else:
                summary["missing_windows"] += 1
            if args.rewrite_existing or not exists:
                pending.append(window_dict)

        chunk_start = chunk_stop

    summary["state_counts"] = dict(sorted(state_counts.items()))
    if args.write:
        summary["written_windows"] = await _write_windows(pending, max(1, int(args.batch_size)))
    return summary


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.device_id and not args.tenant_id:
        raise ValueError("--device-id requires --tenant-id")
    if args.dry_run and args.write:
        raise ValueError("--dry-run and --write cannot be used together")
    if args.write and not args.confirm_write:
        raise ValueError("--write requires --confirm-write")

    start = _parse_dt(args.start)
    stop = _parse_dt(args.stop)
    if stop <= start:
        raise ValueError("--stop must be after --start")
    max_range_days = max(1, int(args.max_range_days))
    if stop - start > timedelta(days=max_range_days):
        raise ValueError(f"Backfill range exceeds --max-range-days={max_range_days}")

    settings = _get_influx_settings()
    targets = await _load_targets(args)
    summaries = []
    for target in targets:
        summaries.append(await _process_target(target, args, settings))

    return {
        "ok": True,
        "mode": "write" if args.write else "dry-run",
        "target_count": len(targets),
        "total_raw_rows": sum(int(item["raw_rows"]) for item in summaries),
        "total_candidate_windows": sum(int(item["candidate_windows"]) for item in summaries),
        "total_missing_windows": sum(int(item["missing_windows"]) for item in summaries),
        "total_written_windows": sum(int(item["written_windows"]) for item in summaries),
        "targets": summaries,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill machine health feature windows from Influx telemetry.")
    parser.add_argument("--start", required=True, help="Inclusive ISO8601 start timestamp.")
    parser.add_argument("--stop", required=True, help="Exclusive ISO8601 stop timestamp.")
    parser.add_argument("--tenant-id", help="Restrict to one tenant.")
    parser.add_argument("--device-id", help="Restrict to one device. Requires --tenant-id.")
    parser.add_argument("--window-seconds", type=int, default=300, help="Feature-window size. Minimum 300.")
    parser.add_argument("--expected-sample-count", type=int, default=10, help="Expected samples per window.")
    parser.add_argument("--chunk-hours", type=int, default=6, help="Influx query chunk size.")
    parser.add_argument("--batch-size", type=int, default=200, help="DB commit batch size.")
    parser.add_argument("--max-range-days", type=int, default=31, help="Safety guard for one run's time range.")
    parser.add_argument("--rewrite-existing", action="store_true", help="Also rewrite existing derived windows.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only. This is the default.")
    parser.add_argument("--write", action="store_true", help="Persist feature windows.")
    parser.add_argument("--confirm-write", action="store_true", help="Required with --write.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.write:
        args.dry_run = True
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
