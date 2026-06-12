from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORTING_ROOT = ROOT / "services" / "reporting-service"
SERVICES_ROOT = ROOT / "services"
for path in (str(ROOT), str(REPORTING_ROOT), str(SERVICES_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")

from services.shared.telemetry_normalization import compute_interval_energy_delta, normalize_telemetry_sample
from src.services.energy_engine import calculate_energy
from src.services.report_engine import compute_device_report
from src.tasks import report_task


CSV_PATH = ROOT / "ad00000001_apr20.csv"
DEVICE_POWER_CONFIG = {"energy_flow_mode": "consumption_only", "polarity_mode": "normal"}
EXPECTED_FULL_WINDOW_KWH = 1.399208
EXPECTED_FIRST_INTERVAL_KWH = 0.04858
EXPECTED_COST_INR = 9.094853


def _load_csv_rows() -> list[dict[str, object]]:
    with CSV_PATH.open() as handle:
        rows = list(csv.DictReader(handle.readlines()[3:]))
    return [
        {
            "timestamp": row["_time"],
            "power": float(row["power"]),
            "energy_kwh": float(row["energy_kwh"]),
            "current": float(row["current"]),
            "voltage": float(row["voltage"]),
            "power_factor": float(row["power_factor"]),
        }
        for row in rows
    ]


def _independent_window_metrics(rows: list[dict[str, object]]) -> dict[str, float]:
    parsed = [
        {
            "timestamp": datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")),
            "power_w": float(row["power"]),
        }
        for row in rows
    ]
    energy_kwh = 0.0
    covered_hours = 0.0
    for previous, current in zip(parsed, parsed[1:]):
        dt_sec = (current["timestamp"] - previous["timestamp"]).total_seconds()
        if dt_sec <= 0:
            continue
        covered_hours += dt_sec / 3600.0
        energy_kwh += max((previous["power_w"] + current["power_w"]) / 2.0, 0.0) / 1000.0 * (dt_sec / 3600.0)

    peak_kw = max(row["power_w"] for row in parsed) / 1000.0
    average_load_kw = energy_kwh / covered_hours
    load_factor_pct = (average_load_kw / peak_kw) * 100.0 if peak_kw > 0 else 0.0
    return {
        "total_kwh": energy_kwh,
        "covered_hours": covered_hours,
        "peak_kw": peak_kw,
        "average_load_kw": average_load_kw,
        "load_factor_pct": load_factor_pct,
        "cost_inr": energy_kwh * 6.5,
    }


class _FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRepo:
    def __init__(self, updates: list[dict]):
        self._updates = updates

    async def update_report(self, report_id: str, **kwargs):
        self._updates.append({"report_id": report_id, **kwargs})


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _SuspiciousCanonicalClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers=None, params=None):
        if url.endswith("/api/v1/devices/DEVICE-1"):
            return _FakeResponse(
                200,
                {
                    "data": {
                        "device_id": "DEVICE-1",
                        "device_name": "AD00000001",
                        "data_source_type": "metered",
                        "energy_flow_mode": "consumption_only",
                        "polarity_mode": "normal",
                    }
                },
            )
        if "/api/v1/energy/device/DEVICE-1/range" in url:
            return _FakeResponse(
                200,
                {
                    "success": True,
                    "totals": {"energy_kwh": 10.2},
                    "days": [{"date": "2026-04-20", "energy_kwh": 10.2, "energy_cost_inr": 66.3}],
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


async def _fake_resolve_tariff(db, tenant_id, **kwargs):
    return SimpleNamespace(rate=6.5, currency="INR", fetched_at=None, source="tenant_tariffs", version_id=1)


@pytest.mark.asyncio
async def test_ad00000001_full_window_truth_proof(monkeypatch):
    rows = _load_csv_rows()

    independent = _independent_window_metrics(rows)
    assert independent["total_kwh"] == pytest.approx(EXPECTED_FULL_WINDOW_KWH, abs=1e-6)
    assert independent["cost_inr"] == pytest.approx(EXPECTED_COST_INR, abs=1e-6)

    previous = normalize_telemetry_sample(rows[0], DEVICE_POWER_CONFIG)
    current = normalize_telemetry_sample(rows[1], DEVICE_POWER_CONFIG)
    first_interval = compute_interval_energy_delta(previous, current)
    assert first_interval.energy_delta_method == "power_integration"
    assert first_interval.reason_code == "fallback_measured_power"
    assert first_interval.business_energy_delta_kwh == pytest.approx(EXPECTED_FIRST_INTERVAL_KWH, abs=1e-5)

    report_result = compute_device_report(
        rows=rows,
        device_id="DEVICE-1",
        device_name="AD00000001",
        data_source_type="metered",
        device_power_config=DEVICE_POWER_CONFIG,
    )
    assert report_result.total_kwh == pytest.approx(1.3992, abs=1e-4)
    assert report_result.peak_demand_kw == pytest.approx(8.942, abs=1e-3)
    assert report_result.average_load_kw == pytest.approx(8.7147, abs=1e-4)
    assert report_result.load_factor_pct == pytest.approx(97.46, abs=1e-2)

    comparison_local_result = calculate_energy(rows, "single", device_power_config=DEVICE_POWER_CONFIG)
    assert comparison_local_result["success"] is True
    assert comparison_local_result["data"]["total_kwh"] == pytest.approx(1.4, abs=1e-2)

    updates: list[dict] = []

    async def _fake_query_telemetry(**kwargs):
        return rows

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _SuspiciousCanonicalClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(report_task, "generate_consumption_pdf", lambda payload: b"%PDF-1.4")
    monkeypatch.setattr(report_task.minio_client, "upload_pdf", lambda pdf_bytes, s3_key: None)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "truth-proof-ad00000001",
        {
            "tenant_id": "SH00000001",
            "start_date": "2026-04-20",
            "end_date": "2026-04-20",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed = [item for item in updates if item.get("status") == "completed"]
    assert completed, "report should complete"
    result_json = completed[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]

    assert device["canonical_overlay_applied"] is False
    assert device["canonical_overlay_reason"] == "canonical_conflicts_with_plausible_local"
    assert device["energy_basis"] == "normalized_telemetry"
    assert device["total_kwh"] == pytest.approx(1.3992, abs=1e-4)

    assert summary["energy_basis"] == "normalized_telemetry"
    assert summary["total_kwh"] == pytest.approx(independent["total_kwh"], abs=1e-4)
    assert summary["total_cost"] == pytest.approx(round(independent["cost_inr"], 2), abs=1e-2)
    assert summary["total_kwh"] != pytest.approx(10.2, abs=1e-6)
    assert summary["total_kwh"] != pytest.approx(EXPECTED_FIRST_INTERVAL_KWH, abs=1e-4)
    assert abs(summary["total_kwh"] - comparison_local_result["data"]["total_kwh"]) <= 0.01
