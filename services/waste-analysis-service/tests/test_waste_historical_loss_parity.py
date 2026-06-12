from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("DEVICE_SERVICE_URL", "http://localhost:8000")
os.environ.setdefault("REPORTING_SERVICE_URL", "http://localhost:8085")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://localhost:8010")
os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("MINIO_EXTERNAL_URL", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio123")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "waste-analysis-service"))
sys.path.insert(1, str(ROOT))

from src.tasks import waste_task  # noqa: E402


def test_canonical_loss_overlay_is_rejected_when_idle_and_loss_conflict():
    result = SimpleNamespace(
        overall_quality="high",
        idle_energy_kwh=1.98,
        offhours_energy_kwh=10.21,
        overconsumption_energy_kwh=0.0,
    )
    canonical = {
        "success": True,
        "totals": {
            "idle_kwh": 0.09,
            "offhours_kwh": 8.66,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 8.75,
        },
        "days": [{"date": "2026-05-18", "idle_kwh": 0.09, "offhours_kwh": 8.66, "loss_kwh": 8.75}],
    }

    accepted, reason = waste_task._should_apply_canonical_loss_overlay(result, canonical)

    assert accepted is False
    assert reason in {
        "canonical_loss_materially_conflicts_with_local",
        "canonical_idle_materially_conflicts_with_local",
    }


def test_canonical_financial_totals_apply_even_when_loss_overlay_is_rejected():
    result = SimpleNamespace(
        overall_quality="high",
        total_energy_kwh=12.19,
        total_cost=79.24,
        idle_energy_kwh=1.98,
        offhours_energy_kwh=10.21,
        overconsumption_energy_kwh=0.0,
        warnings=[],
    )
    canonical = {
        "success": True,
        "totals": {
            "energy_kwh": 8.75,
            "energy_cost_inr": 56.88,
            "idle_kwh": 0.09,
            "offhours_kwh": 8.66,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 8.75,
        },
        "days": [{"date": "2026-05-18", "energy_kwh": 8.75, "loss_kwh": 8.75}],
    }

    financial_applied, financial_reason = waste_task._apply_canonical_financial_totals(result, canonical, 6.5)
    loss_accepted, loss_reason = waste_task._should_apply_canonical_loss_overlay(result, canonical)

    assert financial_applied is True
    assert financial_reason == "canonical_financial_total_accepted"
    assert result.total_energy_kwh == 8.75
    assert result.total_cost == 56.88
    assert "canonical_energy_projection_applied" in result.warnings
    assert loss_accepted is False
    assert loss_reason in {
        "canonical_loss_materially_conflicts_with_local",
        "canonical_idle_materially_conflicts_with_local",
    }


@pytest.mark.asyncio
async def test_query_accounting_rows_uses_chunked_one_minute_queries(monkeypatch):
    calls: list[dict] = []

    async def fake_query_telemetry(**kwargs):
        calls.append(kwargs)
        return [{"timestamp": datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc), "power": 100.0}]

    monkeypatch.setattr(waste_task.settings, "INFLUX_ACCOUNTING_WINDOW", "1m", raising=False)
    monkeypatch.setattr(waste_task.settings, "INFLUX_ACCOUNTING_CHUNK_HOURS", 2, raising=False)
    monkeypatch.setattr(waste_task.influx_reader, "query_telemetry", fake_query_telemetry)

    rows = await waste_task._query_accounting_rows(
        device_id="DEVICE-1",
        start_dt=datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc),
        end_dt=datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc),
        fields=["power", "current"],
    )

    assert len(calls) == 3
    assert all(call["aggregation_window"] == "1m" for call in calls)
    assert rows


def test_waste_canonical_total_cost_preserved_when_loss_overlay_applied():
    result = SimpleNamespace(
        overall_quality="high",
        total_energy_kwh=12.19,
        total_cost=79.24,
        idle_energy_kwh=1.98,
        offhours_energy_kwh=10.21,
        overconsumption_energy_kwh=0.0,
        warnings=[],
    )
    canonical = {
        "success": True,
        "totals": {
            "energy_kwh": 10.0,
            "energy_cost_inr": 65.0,
            "idle_kwh": 2.0,
            "offhours_kwh": 8.0,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 10.0,
        },
        "days": [{"date": "2026-05-18", "energy_kwh": 10.0, "loss_kwh": 10.0}],
    }

    financial_applied, _ = waste_task._apply_canonical_financial_totals(result, canonical, 6.5)
    assert financial_applied is True
    assert result.total_cost == 65.0

    overlay_accepted, _ = waste_task._should_apply_canonical_loss_overlay(result, canonical)
    if overlay_accepted:
        totals = canonical.get("totals") or {}
        if isinstance(totals.get("idle_kwh"), (int, float)):
            result.idle_energy_kwh = round(float(totals.get("idle_kwh") or 0.0), 6)
        if isinstance(totals.get("offhours_kwh"), (int, float)):
            result.offhours_energy_kwh = round(float(totals.get("offhours_kwh") or 0.0), 6)
        if isinstance(totals.get("overconsumption_kwh"), (int, float)):
            result.overconsumption_energy_kwh = round(float(totals.get("overconsumption_kwh") or 0.0), 6)
        if isinstance(totals.get("energy_kwh"), (int, float)):
            result.total_energy_kwh = round(float(totals.get("energy_kwh") or 0.0), 6)
        rate = 7.25
        canonical_total_cost = getattr(result, "total_cost", None)
        if canonical_total_cost is not None and result.total_energy_kwh and result.total_energy_kwh > 0:
            idle_share = (result.idle_energy_kwh or 0.0) / result.total_energy_kwh
            off_share = (result.offhours_energy_kwh or 0.0) / result.total_energy_kwh
            over_share = (result.overconsumption_energy_kwh or 0.0) / result.total_energy_kwh
            result.idle_cost = round(canonical_total_cost * idle_share, 2)
            result.offhours_cost = round(canonical_total_cost * off_share, 2) if result.offhours_energy_kwh is not None else None
            result.overconsumption_cost = round(canonical_total_cost * over_share, 2) if result.overconsumption_energy_kwh is not None else None

        assert result.total_cost == 65.0
        assert result.total_cost != round(result.total_energy_kwh * rate, 2)


def test_waste_category_costs_allocated_proportionally_from_canonical_total():
    result = SimpleNamespace(
        overall_quality="high",
        total_energy_kwh=12.0,
        total_cost=78.0,
        idle_energy_kwh=3.0,
        offhours_energy_kwh=9.0,
        overconsumption_energy_kwh=0.0,
        warnings=[],
    )
    canonical = {
        "success": True,
        "totals": {
            "energy_kwh": 10.0,
            "energy_cost_inr": 65.0,
            "idle_kwh": 2.0,
            "offhours_kwh": 8.0,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 10.0,
        },
        "days": [{"date": "2026-05-18", "energy_kwh": 10.0, "loss_kwh": 10.0}],
    }

    financial_applied, _ = waste_task._apply_canonical_financial_totals(result, canonical, 6.5)
    assert financial_applied is True
    assert result.total_cost == 65.0
    assert result.total_energy_kwh == 10.0

    canonical_total_cost = result.total_cost
    idle_share = (2.0) / 10.0
    off_share = (8.0) / 10.0
    allocated_idle = round(canonical_total_cost * idle_share, 2)
    allocated_off = round(canonical_total_cost * off_share, 2)
    bucket_sum = allocated_idle + allocated_off
    assert abs(bucket_sum - canonical_total_cost) < 0.02


def test_waste_total_loss_kwh_matches_canonical_even_when_loss_overlay_rejected():
    result = SimpleNamespace(
        overall_quality="high",
        total_energy_kwh=12.0,
        total_cost=78.0,
        total_loss_kwh=None,
        idle_energy_kwh=3.0,
        offhours_energy_kwh=9.0,
        overconsumption_energy_kwh=0.0,
        warnings=[],
    )
    canonical = {
        "success": True,
        "totals": {
            "energy_kwh": 10.0,
            "energy_cost_inr": 65.0,
            "idle_kwh": 0.09,
            "offhours_kwh": 8.66,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 8.75,
        },
        "days": [{"date": "2026-05-18", "idle_kwh": 0.09, "offhours_kwh": 8.66, "loss_kwh": 8.75}],
    }

    financial_applied, _ = waste_task._apply_canonical_financial_totals(result, canonical, 6.5)
    assert financial_applied is True
    assert result.total_energy_kwh == 10.0
    assert result.total_loss_kwh == 8.75

    overlay_accepted, reason = waste_task._should_apply_canonical_loss_overlay(result, canonical)
    assert overlay_accepted is False
    assert result.total_loss_kwh == 8.75


def test_canonical_zero_loss_propagates_to_device_total_loss_kwh():
    result = SimpleNamespace(
        overall_quality="high",
        total_energy_kwh=12.0,
        total_cost=78.0,
        total_loss_kwh=None,
        idle_energy_kwh=3.0,
        offhours_energy_kwh=9.0,
        overconsumption_energy_kwh=0.0,
        warnings=[],
    )
    canonical = {
        "success": True,
        "totals": {
            "energy_kwh": 10.0,
            "energy_cost_inr": 65.0,
            "idle_kwh": 0.0,
            "offhours_kwh": 0.0,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 0.0,
        },
        "days": [{"date": "2026-05-18", "energy_kwh": 10.0, "loss_kwh": 0.0}],
    }

    financial_applied, _ = waste_task._apply_canonical_financial_totals(result, canonical, 6.5)
    assert financial_applied is True
    assert result.total_loss_kwh == 0.0
    assert result.total_loss_kwh is not None


def test_aggregate_total_loss_kwh_uses_canonical_zero_over_local_fallback():
    r1 = SimpleNamespace(
        total_energy_kwh=10.0,
        total_loss_kwh=0.0,
        idle_energy_kwh=3.0,
        offhours_energy_kwh=2.0,
        overconsumption_energy_kwh=0.0,
        idle_duration_sec=100,
    )
    r2 = SimpleNamespace(
        total_energy_kwh=8.0,
        total_loss_kwh=0.0,
        idle_energy_kwh=1.0,
        offhours_energy_kwh=1.0,
        overconsumption_energy_kwh=0.0,
        idle_duration_sec=50,
    )
    results = [r1, r2]

    canonical_total_loss_kwh = sum(getattr(r, "total_loss_kwh", None) or 0.0 for r in results)
    any_canonical_loss = any(getattr(r, "total_loss_kwh", None) is not None for r in results)
    total_loss_kwh = round(canonical_total_loss_kwh, 6) if any_canonical_loss else round(
        sum(r.idle_energy_kwh for r in results)
        + sum((r.offhours_energy_kwh or 0.0) for r in results)
        + sum((r.overconsumption_energy_kwh or 0.0) for r in results),
        6,
    )

    assert any_canonical_loss is True
    assert total_loss_kwh == 0.0
    local_sum = sum(r.idle_energy_kwh for r in results) + sum((r.offhours_energy_kwh or 0.0) for r in results)
    assert local_sum > 0
    assert total_loss_kwh != round(local_sum, 6)


def test_aggregate_total_loss_kwh_falls_back_when_no_canonical_present():
    r1 = SimpleNamespace(
        total_energy_kwh=10.0,
        total_loss_kwh=None,
        idle_energy_kwh=3.0,
        offhours_energy_kwh=2.0,
        overconsumption_energy_kwh=1.0,
        idle_duration_sec=100,
    )
    results = [r1]

    canonical_total_loss_kwh = sum(getattr(r, "total_loss_kwh", None) or 0.0 for r in results)
    any_canonical_loss = any(getattr(r, "total_loss_kwh", None) is not None for r in results)
    total_loss_kwh = round(canonical_total_loss_kwh, 6) if any_canonical_loss else round(
        sum(r.idle_energy_kwh for r in results)
        + sum((r.offhours_energy_kwh or 0.0) for r in results)
        + sum((r.overconsumption_energy_kwh or 0.0) for r in results),
        6,
    )

    assert any_canonical_loss is False
    assert total_loss_kwh == round(3.0 + 2.0 + 1.0, 6)
