from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICES_ROOT = ROOT / "services"
DEVICE_SERVICE_ROOT = ROOT / "services" / "device-service"
for path in (ROOT, SERVICES_ROOT, DEVICE_SERVICE_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services.degradation import service as degradation_service


class _FakeQuery:
    def __init__(self) -> None:
        self.limit_value = None
        self.order_by_value = None
        self.where_args = None

    def where(self, *args):
        self.where_args = args
        return self

    def order_by(self, value):
        self.order_by_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_load_feature_windows_for_device_limits_to_recent_five_and_restores_chronological_order(monkeypatch):
    query = _FakeQuery()
    monkeypatch.setattr(degradation_service, "select", lambda *_args, **_kwargs: query)

    rows = [
        SimpleNamespace(
            current_avg_mean=float(index),
            current_avg_std=0.1,
            current_avg_p95=0.2,
            current_l1_mean=0.0,
            current_l2_mean=0.0,
            current_l3_mean=0.0,
            power_mean=100.0 + index,
            power_p95=120.0 + index,
            power_factor_mean=0.95,
            voltage_avg_mean=230.0,
            voltage_imbalance=0.0,
            phase_imbalance=0.0,
            frequency_mean=50.0,
            energy_kwh=1.0,
            running_state="STEADY_RUNNING",
            telemetry_coverage=1.0,
            sample_count=60,
            window_start=datetime(2026, 4, index, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 4, index, 1, 0, tzinfo=timezone.utc),
        )
        for index in (10, 9, 8, 7, 6)
    ]

    class _FakeSession:
        async def execute(self, incoming_query):
            assert incoming_query is query
            return _FakeExecuteResult(rows)

    windows = await degradation_service.load_feature_windows_for_device(
        _FakeSession(),
        tenant_id="tenant-a",
        device_id="DEVICE-1",
    )

    assert query.limit_value == 5
    assert [window.window_start.day for window in windows] == [6, 7, 8, 9, 10]
    assert [window.window.current_avg_mean for window in windows] == [6.0, 7.0, 8.0, 9.0, 10.0]


@pytest.mark.asyncio
async def test_load_feature_windows_for_baseline_uses_minimum_days_derived_limit(monkeypatch):
    query = _FakeQuery()
    monkeypatch.setattr(degradation_service, "select", lambda *_args, **_kwargs: query)

    rows = [
        SimpleNamespace(
            current_avg_mean=1.0,
            current_avg_std=0.1,
            current_avg_p95=0.2,
            current_l1_mean=0.0,
            current_l2_mean=0.0,
            current_l3_mean=0.0,
            power_mean=100.0,
            power_p95=120.0,
            power_factor_mean=0.95,
            voltage_avg_mean=230.0,
            voltage_imbalance=0.0,
            phase_imbalance=0.0,
            frequency_mean=50.0,
            energy_kwh=1.0,
            running_state="STEADY_RUNNING",
            telemetry_coverage=1.0,
            sample_count=60,
            window_start=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc),
        )
    ]

    class _FakeSession:
        async def execute(self, incoming_query):
            assert incoming_query is query
            return _FakeExecuteResult(rows)

    windows = await degradation_service.load_feature_windows_for_baseline(
        _FakeSession(),
        tenant_id="tenant-a",
        device_id="DEVICE-1",
        minimum_days=7,
        interval_seconds=300,
    )

    assert query.limit_value == (7 * 288) + 288
    assert len(windows) == 1


@pytest.mark.asyncio
async def test_score_device_uses_candidate_baseline_for_fresh_learning_snapshots(monkeypatch):
    baseline = degradation_service.BaselineInput(
        current_avg_std=0.5,
        power_factor_mean=0.95,
        power_mean=5000.0,
        phase_imbalance_mean=0.02,
        quality_score=0.2,
        quality_band="insufficient",
    )
    windows = [
        degradation_service.FeatureWindowResult(
            window=degradation_service.FeatureWindowInput(
                current_avg_std=0.5,
                power_factor_mean=0.95,
                power_mean=5000.0,
                phase_imbalance=0.02,
            ),
            running_state="STEADY_RUNNING",
            telemetry_coverage=1.0,
            sample_count=60,
        )
    ]

    monkeypatch.setattr(degradation_service, "load_latest_baseline_for_device", AsyncMock(return_value=baseline))
    monkeypatch.setattr(degradation_service, "load_feature_windows_for_device", AsyncMock(return_value=windows))
    monkeypatch.setattr(degradation_service, "load_prior_scores_for_device", AsyncMock(return_value=[]))

    result = await degradation_service.score_device(object(), "tenant-a", "DEVICE-1")

    assert result is not None
    assert result.status == "learning"
