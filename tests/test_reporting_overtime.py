from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

REPORTING_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "reporting-service"
if str(REPORTING_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(REPORTING_SERVICE_ROOT))

pytest.importorskip("jinja2")
pytest.importorskip("sqlalchemy")

if not os.environ.get("INFLUXDB_URL"):
    pytest.skip("INFLUXDB_URL not configured; reporting-service modules require InfluxDB config", allow_module_level=True)
if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL not configured; reporting-service modules require database config", allow_module_level=True)

from src.pdf import builder as pdf_builder
from src.services.overtime_engine import compute_overtime_breakdown
from src.tasks import report_task


IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def _rows_for_local_period(start_local: datetime, end_local: datetime, *, step_minutes: int = 10) -> list[dict[str, object]]:
    rows = []
    cursor = start_local
    while cursor <= end_local:
        rows.append(
            {
                "timestamp": cursor.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "power": 1000.0,
                "current": 5.0,
                "voltage": 230.0,
            }
        )
        cursor += timedelta(minutes=step_minutes)
    return rows


@pytest.mark.parametrize(
    "start_local,end_local,shift_start,shift_end,expected_minutes,expected_kwh",
    [
        (
            datetime(2026, 4, 1, 10, 0, tzinfo=IST),
            datetime(2026, 4, 1, 11, 0, tzinfo=IST),
            "09:00",
            "17:00",
            0.0,
            0.0,
        ),
        (
            datetime(2026, 4, 1, 18, 0, tzinfo=IST),
            datetime(2026, 4, 1, 19, 0, tzinfo=IST),
            "09:00",
            "17:00",
            60.0,
            1.0,
        ),
        (
            datetime(2026, 4, 1, 16, 30, tzinfo=IST),
            datetime(2026, 4, 1, 17, 30, tzinfo=IST),
            "09:00",
            "17:00",
            30.0,
            0.5,
        ),
        (
            datetime(2026, 4, 1, 5, 30, tzinfo=IST),
            datetime(2026, 4, 1, 6, 30, tzinfo=IST),
            "22:00",
            "06:00",
            30.0,
            0.5,
        ),
    ],
)
def test_compute_overtime_breakdown_handles_shift_boundaries(
    start_local: datetime,
    end_local: datetime,
    shift_start: str,
    shift_end: str,
    expected_minutes: float,
    expected_kwh: float,
) -> None:
    rows = _rows_for_local_period(start_local, end_local)
    shifts = [{"shift_start": shift_start, "shift_end": shift_end, "is_active": True}]

    result = compute_overtime_breakdown(rows, shifts, tariff_rate=10.0, currency="INR")

    assert result.configured is True
    assert result.shift_count == 1
    assert result.total_overtime_minutes == expected_minutes
    assert round(result.total_overtime_kwh, 4) == expected_kwh
    if expected_minutes > 0:
        assert result.total_overtime_cost == round(expected_kwh * 10.0, 2)
    else:
        assert result.total_overtime_cost == 0.0


def test_compute_overtime_breakdown_warns_when_shift_missing() -> None:
    rows = _rows_for_local_period(
        datetime(2026, 4, 1, 18, 0, tzinfo=IST),
        datetime(2026, 4, 1, 19, 0, tzinfo=IST),
    )

    result = compute_overtime_breakdown(rows, [], tariff_rate=10.0, currency="INR")

    assert result.configured is False
    assert result.total_overtime_minutes == 0.0
    assert any("SHIFT_NOT_CONFIGURED" in warning for warning in result.warnings)


def test_compute_overtime_breakdown_emits_exact_window_rows() -> None:
    rows = _rows_for_local_period(
        datetime(2026, 4, 1, 18, 0, tzinfo=IST),
        datetime(2026, 4, 1, 19, 0, tzinfo=IST),
    )
    shifts = [{"shift_start": "09:00", "shift_end": "17:00", "is_active": True}]

    result = compute_overtime_breakdown(rows, shifts, tariff_rate=10.0, currency="INR")

    assert len(result.window_breakdown) == 1
    row = result.window_breakdown[0]
    assert row["date"] == "2026-04-01"
    assert row["window_start"] == "01 Apr 2026, 06:00 PM"
    assert row["window_end"] == "01 Apr 2026, 07:00 PM"
    assert row["overtime_minutes"] == 60.0
    assert row["overtime_hours"] == pytest.approx(1.0, abs=0.001)
    assert row["overtime_kwh"] == pytest.approx(1.0, abs=0.001)
    assert row["overtime_cost"] == 10.0
    assert row["shift_status"] == "Overtime"


def test_compute_overtime_breakdown_splits_multiple_windows_in_same_day() -> None:
    rows = _rows_for_local_period(
        datetime(2026, 4, 1, 7, 0, tzinfo=IST),
        datetime(2026, 4, 1, 19, 0, tzinfo=IST),
    )
    shifts = [{"shift_start": "09:00", "shift_end": "17:00", "is_active": True}]

    result = compute_overtime_breakdown(rows, shifts, tariff_rate=10.0, currency="INR")

    assert [row["window_start"] for row in result.window_breakdown] == [
        "01 Apr 2026, 07:00 AM",
        "01 Apr 2026, 05:00 PM",
    ]
    assert [row["window_end"] for row in result.window_breakdown] == [
        "01 Apr 2026, 09:00 AM",
        "01 Apr 2026, 07:00 PM",
    ]
    assert sum(float(row["overtime_minutes"]) for row in result.window_breakdown) == 240.0
    assert sum(float(row["overtime_kwh"]) for row in result.window_breakdown) == pytest.approx(4.0, abs=0.001)


def test_compute_overtime_breakdown_formats_overnight_windows_in_platform_time() -> None:
    rows = _rows_for_local_period(
        datetime(2026, 4, 1, 5, 30, tzinfo=IST),
        datetime(2026, 4, 1, 6, 30, tzinfo=IST),
    )
    shifts = [{"shift_start": "22:00", "shift_end": "06:00", "is_active": True}]

    result = compute_overtime_breakdown(rows, shifts, tariff_rate=10.0, currency="INR")

    assert len(result.window_breakdown) == 1
    row = result.window_breakdown[0]
    assert row["window_start"] == "01 Apr 2026, 06:00 AM"
    assert row["window_end"] == "01 Apr 2026, 06:30 AM"
    assert row["overtime_minutes"] == 30.0


def test_generate_consumption_pdf_renders_overtime_section(monkeypatch) -> None:
    captured = {}

    def fake_render(html_content: str) -> bytes:
        captured["html"] = html_content
        return b"PDF"

    monkeypatch.setattr(pdf_builder, "_render_pdf", fake_render)

    payload = {
        "report_id": "RPT-1",
        "device_label": "All Machines",
        "start_date": "2026-04-01",
        "end_date": "2026-04-01",
        "total_kwh": 12.5,
        "peak_demand_kw": 5.2,
        "peak_timestamp": "2026-04-01T10:00:00+05:30",
        "average_load_kw": 2.1,
        "load_factor_pct": 40.0,
        "load_factor_band": "moderate",
        "total_cost": 125.0,
        "currency": "INR",
        "tariff_rate_used": 10.0,
        "daily_series": [{"date": "2026-04-01", "kwh": 12.5}],
        "per_device": [{"device_name": "Machine A", "total_kwh": 12.5}],
        "overtime_summary": {
            "configured_devices": 1,
            "devices_without_shift": 0,
            "total_minutes": 30.0,
            "total_hours": 0.5,
            "total_kwh": 0.5,
            "total_cost": 5.0,
            "currency": "INR",
            "tariff_rate_used": 10.0,
            "device_count": 1,
            "rows": [
                {
                    "date": "2026-04-01",
                    "device_name": "Machine A",
                    "window_start": "01 Apr 2026, 06:00 PM",
                    "window_end": "01 Apr 2026, 06:30 PM",
                    "overtime_minutes": 30.0,
                    "overtime_hours": 0.5,
                    "overtime_kwh": 0.5,
                    "overtime_cost": 5.0,
                    "shift_status": "Overtime",
                }
            ],
            "device_summary": [
                {
                    "device_name": "Machine A",
                    "configured": True,
                    "shift_count": 1,
                    "total_overtime_minutes": 30.0,
                    "total_overtime_hours": 0.5,
                    "total_overtime_kwh": 0.5,
                    "total_overtime_cost": 5.0,
                    "currency": "INR",
                }
            ],
        },
        "overtime_rows": [],
        "overtime_device_summary": [],
        "insights": [],
        "warnings": [],
        "overall_quality": "high",
        "tariff_fetched_at": "2026-04-01T00:00:00+05:30",
        "generated_at": "2026-04-01T12:00:00+05:30",
    }

    pdf_bytes = pdf_builder.generate_consumption_pdf(payload)

    assert pdf_bytes == b"PDF"
    assert "Overtime Breakdown" in captured["html"]
    assert "Overtime Cost" in captured["html"]
    assert "Same as off-hours running" in captured["html"]
    assert "Each overtime proof row below shows the exact outside-shift window" in captured["html"]
    assert "<th>From</th>" in captured["html"]
    assert "<th>To</th>" in captured["html"]
    assert "01 Apr 2026, 06:00 PM" in captured["html"]
    assert "01 Apr 2026, 06:30 PM" in captured["html"]


@pytest.mark.asyncio
async def test_consumption_report_includes_overtime_in_result_json(monkeypatch) -> None:
    captured = {}

    class FakeReportRepository:
        def __init__(self, db, ctx=None, allow_cross_tenant=False):
            self.db = db
            self.ctx = ctx
            self.allow_cross_tenant = allow_cross_tenant

        async def update_report(self, report_id, **kwargs):  # noqa: ANN001
            captured.setdefault("updates", []).append({"report_id": report_id, **kwargs})

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object]):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):  # noqa: ANN001
            if url.endswith("/api/v1/devices/DEV-1"):
                return FakeResponse(200, {"data": {"device_name": "Machine A", "data_source_type": "metered"}})
            return FakeResponse(404, {})

    class FakeAsyncSessionContext:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_db = object()

    async def fake_query_telemetry(**kwargs):  # noqa: ANN001
        return _rows_for_local_period(
            datetime(2026, 4, 1, 18, 0, tzinfo=IST),
            datetime(2026, 4, 1, 19, 0, tzinfo=IST),
        )

    async def fake_shift_config(client, device_id, tenant_id):  # noqa: ANN001
        return [{"shift_start": "09:00", "shift_end": "17:00", "is_active": True}]

    async def fake_canonical_energy_range(*args, **kwargs):  # noqa: ANN001
        return {
            "success": True,
            "totals": {
                "energy_kwh": 1.0,
                "offhours_kwh": 0.6,
                "idle_kwh": 0.0,
                "overconsumption_kwh": 0.0,
            },
            "days": [
                {
                    "date": "2026-04-01",
                    "energy_kwh": 1.0,
                    "energy_cost_inr": 10.0,
                    "offhours_kwh": 0.6,
                }
            ],
        }

    async def fake_resolve_tariff(db, tenant_id):  # noqa: ANN001
        return SimpleNamespace(rate=10.0, currency="INR", fetched_at="2026-04-01T00:00:00+05:30", source="test")

    def fake_upload_pdf(*args, **kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(report_task, "ReportRepository", FakeReportRepository)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: FakeAsyncSessionContext(fake_db))
    monkeypatch.setattr(report_task, "httpx", SimpleNamespace(AsyncClient=FakeAsyncClient))
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", fake_query_telemetry)
    monkeypatch.setattr(report_task, "_fetch_shift_config", fake_shift_config)
    monkeypatch.setattr(report_task, "_fetch_canonical_energy_range", fake_canonical_energy_range)
    monkeypatch.setattr(report_task, "resolve_tariff", fake_resolve_tariff)
    monkeypatch.setattr(report_task, "generate_consumption_pdf", lambda payload: b"PDF")
    monkeypatch.setattr(report_task.minio_client, "upload_pdf", fake_upload_pdf)

    await report_task.run_consumption_report(
        "RPT-TEST",
        {
            "tenant_id": "TENANT-1",
            "resolved_device_ids": ["DEV-1"],
            "start_date": "2026-04-01",
            "end_date": "2026-04-01",
        },
    )

    completed = [item for item in captured.get("updates", []) if item.get("status") == "completed"]
    assert completed, "expected the report to complete successfully"
    result_json = completed[-1]["result_json"]
    assert result_json["summary"]["overtime_minutes"] == 60.0
    assert result_json["summary"]["overtime_kwh"] == 0.6
    assert result_json["summary"]["overtime_cost"] == 6.0
    assert result_json["overtime"]["rows"]
    assert result_json["overtime"]["rows"][0]["window_start"] == "01 Apr 2026, 06:00 PM"
    assert result_json["overtime"]["rows"][0]["window_end"] == "01 Apr 2026, 07:00 PM"
    assert result_json["devices"][0]["overtime"]["total_overtime_minutes"] == 60.0
    assert result_json["devices"][0]["overtime"]["total_overtime_kwh"] == 0.6


def test_canonical_offhours_can_zero_out_window_cost_without_losing_timestamps() -> None:
    overtime_dict = {
        "daily_breakdown": [
            {
                "date": "2026-04-01",
                "overtime_minutes": 70.0,
                "overtime_hours": 1.1667,
                "overtime_kwh": 1.0,
                "overtime_cost": 10.0,
            }
        ],
        "window_breakdown": [
            {
                "date": "2026-04-01",
                "window_start": "01 Apr 2026, 06:00 PM",
                "window_end": "01 Apr 2026, 07:10 PM",
                "window_start_iso": "2026-04-01T18:00:00+05:30",
                "window_end_iso": "2026-04-01T19:10:00+05:30",
                "overtime_minutes": 70.0,
                "overtime_hours": 1.1667,
                "overtime_kwh": 1.0,
                "overtime_cost": 10.0,
                "shift_status": "Overtime",
            }
        ],
    }
    canonical = {"totals": {"offhours_kwh": 0.0}, "days": [{"date": "2026-04-01", "offhours_kwh": 0.0}]}

    merged = report_task._apply_canonical_offhours(overtime_dict, canonical, tariff_rate=10.0, currency="INR")

    assert merged["daily_breakdown"][0]["overtime_minutes"] == 70.0
    assert merged["daily_breakdown"][0]["overtime_kwh"] == 0.0
    assert merged["window_breakdown"][0]["window_start"] == "01 Apr 2026, 06:00 PM"
    assert merged["window_breakdown"][0]["window_end"] == "01 Apr 2026, 07:10 PM"
    assert merged["window_breakdown"][0]["overtime_kwh"] == 0.0
    assert merged["window_breakdown"][0]["overtime_cost"] == 0.0
