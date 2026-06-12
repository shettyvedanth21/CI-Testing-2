from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE_DIR.parents[1]
SERVICES_DIR = REPO_ROOT / "services"
SCRIPT_PATH = BASE_DIR / "scripts" / "backfill_machine_health_windows.py"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 48)
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "y" * 48)


def _load_script_module():
    spec = importlib.util.spec_from_file_location("backfill_machine_health_windows", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["backfill_machine_health_windows"] = module
    spec.loader.exec_module(module)
    return module


def test_rows_to_samples_maps_legacy_current_and_voltage_fields():
    module = _load_script_module()

    samples = module._rows_to_samples([
        {
            "timestamp": datetime(2026, 5, 30, 6, 45, tzinfo=timezone.utc),
            "current": "12.5",
            "power": "5000.0",
            "power_factor": "0.91",
            "voltage": "229.5",
            "energy_kwh": "100.2",
        }
    ])

    assert len(samples) == 1
    assert samples[0].current_avg == 12.5
    assert samples[0].power == 5000.0
    assert samples[0].power_factor == 0.91
    assert samples[0].voltage_avg == 229.5
    assert samples[0].energy_kwh == 100.2


def test_floor_window_uses_utc_epoch_boundary():
    module = _load_script_module()

    ts = datetime(2026, 5, 30, 6, 47, 42, tzinfo=timezone.utc)

    assert module._floor_window(ts, 300) == datetime(2026, 5, 30, 6, 45, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_process_target_dry_run_never_writes(monkeypatch):
    module = _load_script_module()
    target = module.BackfillTarget(tenant_id="TENANT-A", device_id="DEVICE-A")
    args = argparse.Namespace(
        start="2026-05-30T06:45:00Z",
        stop="2026-05-30T06:50:00Z",
        window_seconds=300,
        expected_sample_count=2,
        chunk_hours=1,
        write=False,
        confirm_write=False,
        rewrite_existing=False,
        batch_size=100,
        max_range_days=31,
    )
    monkeypatch.setattr(module, "_existing_window_starts", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        module,
        "_query_influx_rows",
        lambda **_kwargs: [
            {
                "timestamp": datetime(2026, 5, 30, 6, 45, tzinfo=timezone.utc),
                "current": 10.0,
                "power": 5000.0,
                "power_factor": 0.9,
                "voltage": 230.0,
            },
            {
                "timestamp": datetime(2026, 5, 30, 6, 45, 30, tzinfo=timezone.utc),
                "current": 10.2,
                "power": 5010.0,
                "power_factor": 0.91,
                "voltage": 231.0,
            },
        ],
    )
    write_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(module, "_write_windows", write_mock)

    summary = await module._process_target(target, args, {"bucket": "telemetry"})

    assert summary["dry_run"] is True
    assert summary["raw_rows"] == 2
    assert summary["candidate_windows"] == 1
    assert summary["missing_windows"] == 1
    assert summary["written_windows"] == 0
    write_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_target_write_requires_explicit_write_flag(monkeypatch):
    module = _load_script_module()
    target = module.BackfillTarget(tenant_id="TENANT-A", device_id="DEVICE-A")
    args = argparse.Namespace(
        start="2026-05-30T06:45:00Z",
        stop="2026-05-30T06:50:00Z",
        window_seconds=300,
        expected_sample_count=1,
        chunk_hours=1,
        write=True,
        confirm_write=True,
        rewrite_existing=False,
        batch_size=100,
        max_range_days=31,
    )
    monkeypatch.setattr(module, "_existing_window_starts", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        module,
        "_query_influx_rows",
        lambda **_kwargs: [
            {
                "timestamp": datetime(2026, 5, 30, 6, 45, tzinfo=timezone.utc),
                "current": 10.0,
                "power": 5000.0,
            },
        ],
    )
    write_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(module, "_write_windows", write_mock)

    summary = await module._process_target(target, args, {"bucket": "telemetry"})

    assert summary["dry_run"] is False
    assert summary["written_windows"] == 1
    write_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_target_write_skips_existing_windows_by_default(monkeypatch):
    module = _load_script_module()
    target = module.BackfillTarget(tenant_id="TENANT-A", device_id="DEVICE-A")
    existing_start = datetime(2026, 5, 30, 6, 45, tzinfo=timezone.utc)
    args = argparse.Namespace(
        start="2026-05-30T06:45:00Z",
        stop="2026-05-30T06:50:00Z",
        window_seconds=300,
        expected_sample_count=1,
        chunk_hours=1,
        write=True,
        confirm_write=True,
        rewrite_existing=False,
        batch_size=100,
        max_range_days=31,
    )
    monkeypatch.setattr(module, "_existing_window_starts", AsyncMock(return_value={existing_start}))
    monkeypatch.setattr(
        module,
        "_query_influx_rows",
        lambda **_kwargs: [
            {
                "timestamp": existing_start,
                "current": 10.0,
                "power": 5000.0,
            },
        ],
    )
    write_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(module, "_write_windows", write_mock)

    summary = await module._process_target(target, args, {"bucket": "telemetry"})

    assert summary["candidate_windows"] == 1
    assert summary["existing_windows"] == 1
    assert summary["missing_windows"] == 0
    assert summary["written_windows"] == 0
    write_mock.assert_awaited_once_with([], 100)


@pytest.mark.asyncio
async def test_run_write_requires_confirm_write(monkeypatch):
    module = _load_script_module()
    args = argparse.Namespace(
        start="2026-05-30T06:45:00Z",
        stop="2026-05-30T06:50:00Z",
        tenant_id="TENANT-A",
        device_id="DEVICE-A",
        dry_run=False,
        write=True,
        confirm_write=False,
        max_range_days=31,
    )
    monkeypatch.setattr(module, "_get_influx_settings", lambda: {"bucket": "telemetry"})
    monkeypatch.setattr(module, "_load_targets", AsyncMock(return_value=[]))

    with pytest.raises(ValueError, match="--write requires --confirm-write"):
        await module._run(args)


@pytest.mark.asyncio
async def test_run_rejects_stop_before_start(monkeypatch):
    module = _load_script_module()
    args = argparse.Namespace(
        start="2026-05-30T06:50:00Z",
        stop="2026-05-30T06:45:00Z",
        tenant_id="TENANT-A",
        device_id="DEVICE-A",
        dry_run=True,
        write=False,
        confirm_write=False,
        max_range_days=31,
    )
    monkeypatch.setattr(module, "_get_influx_settings", lambda: {"bucket": "telemetry"})
    monkeypatch.setattr(module, "_load_targets", AsyncMock(return_value=[]))

    with pytest.raises(ValueError, match="--stop must be after --start"):
        await module._run(args)


@pytest.mark.asyncio
async def test_run_rejects_range_beyond_max_range_days(monkeypatch):
    module = _load_script_module()
    args = argparse.Namespace(
        start="2026-05-01T00:00:00Z",
        stop="2026-06-02T00:00:00Z",
        tenant_id="TENANT-A",
        device_id="DEVICE-A",
        dry_run=True,
        write=False,
        confirm_write=False,
        max_range_days=31,
    )
    monkeypatch.setattr(module, "_get_influx_settings", lambda: {"bucket": "telemetry"})
    monkeypatch.setattr(module, "_load_targets", AsyncMock(return_value=[]))

    with pytest.raises(ValueError, match="Backfill range exceeds --max-range-days=31"):
        await module._run(args)
