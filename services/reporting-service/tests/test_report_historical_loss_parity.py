from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "test-internal-service-secret-at-least-32-chars")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "services" / "reporting-service"))
sys.path.insert(2, str(ROOT / "services"))

from src.tasks import report_task  # noqa: E402


def test_canonical_offhours_overlay_is_rejected_when_it_conflicts_with_local():
    overtime_dict = {
        "total_overtime_kwh": 10.21,
        "daily_breakdown": [{"date": "2026-05-18", "overtime_minutes": 330.0, "overtime_hours": 5.5, "overtime_kwh": 10.21}],
        "window_breakdown": [],
    }
    canonical = {
        "success": True,
        "totals": {"offhours_kwh": 5.0},
        "days": [{"date": "2026-05-18", "offhours_kwh": 5.0}],
    }

    merged = report_task._apply_canonical_offhours(
        overtime_dict=overtime_dict,
        canonical=canonical,
        tariff_rate=6.5,
        currency="INR",
        local_total_hours=24.0,
        report_window_hours=24.0,
    )

    assert merged["total_overtime_kwh"] == 10.21
    assert merged["daily_breakdown"][0]["overtime_kwh"] == 10.21


@pytest.mark.asyncio
async def test_query_accounting_rows_uses_chunked_one_minute_queries(monkeypatch):
    calls: list[dict] = []

    async def fake_query_telemetry(**kwargs):
        calls.append(kwargs)
        return [{"timestamp": datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc), "power": 100.0}]

    monkeypatch.setattr(report_task.settings, "INFLUX_ACCOUNTING_WINDOW", "1m", raising=False)
    monkeypatch.setattr(report_task.settings, "INFLUX_ACCOUNTING_CHUNK_HOURS", 2, raising=False)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", fake_query_telemetry)

    rows = await report_task._query_accounting_rows(
        device_id="DEVICE-1",
        start_dt=datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc),
        end_dt=datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc),
        fields=["power", "current"],
    )

    assert len(calls) == 3
    assert all(call["aggregation_window"] == "1m" for call in calls)
    assert rows
