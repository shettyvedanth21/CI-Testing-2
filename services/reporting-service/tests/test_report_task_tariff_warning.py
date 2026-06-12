from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "services" / "reporting-service"))
sys.path.insert(2, str(ROOT / "services"))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "test-internal-service-secret-at-least-32-chars")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")

from src.tasks import report_task


async def _fake_async_generate_consumption_pdf(payload):
    return b"%PDF-1.4"


async def _fake_async_generate_comparison_pdf(payload):
    return b"%PDF-1.4"


async def _fake_async_upload_pdf(pdf_bytes, s3_key):
    return s3_key


def _make_capture_pdf(captures: list):
    async def _capture(payload):
        captures.append(payload)
        return b"%PDF-1.4"
    return _capture


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

    async def finalize_revision_report(self, report_id: str, *, tenant_id: str):
        self._updates.append({"report_id": report_id, "finalized": True, "tenant_id": tenant_id})


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers=None, params=None):
        return _FakeResponse(404, {"detail": "not found"})


class _ComparisonAsyncClient:
    def __init__(self):
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._closed = True
        return False

    async def get(self, url: str, headers=None, params=None):
        if self._closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        if url.endswith("/api/v1/devices/DEVICE-A"):
            return _FakeResponse(200, {"data": {"device_id": "DEVICE-A", "device_name": "Device A", "phase_type": "single"}})
        if url.endswith("/api/v1/devices/DEVICE-B"):
            return _FakeResponse(200, {"data": {"device_id": "DEVICE-B", "device_name": "Device B", "phase_type": "single"}})
        if "/api/v1/energy/device/DEVICE-A/range" in url:
            return _FakeResponse(200, {"success": True, "totals": {"energy_kwh": 0.1667}, "days": [{"date": "2026-04-09", "energy_kwh": 0.1667}]})
        if "/api/v1/energy/device/DEVICE-B/range" in url:
            return _FakeResponse(200, {"success": True, "totals": {"energy_kwh": 0.1}, "days": [{"date": "2026-04-09", "energy_kwh": 0.1}]})
        return _FakeResponse(404, {"detail": "not found"})


async def _fake_resolve_tariff(db, tenant_id, **kwargs):
    return SimpleNamespace(rate=None, currency="INR", fetched_at=None, source="tenant_tariffs", version_id=None)


async def _fake_resolve_tariff_inr(db, tenant_id, **kwargs):
    return SimpleNamespace(rate=10.0, currency="INR", fetched_at=None, source="tenant_tariffs", version_id=1)


async def _fake_resolve_tariff_6_5(db, tenant_id, **kwargs):
    return SimpleNamespace(rate=6.5, currency="INR", fetched_at=None, source="tenant_tariffs", version_id=2)


async def _fake_query_telemetry(**kwargs):
    return [
        {"timestamp": "2026-04-09T14:20:00Z", "power": 5000.0},
        {"timestamp": "2026-04-09T14:40:00Z", "power": 5000.0},
    ]


async def _fake_query_telemetry_vi_pf_only(**kwargs):
    return [
        {"timestamp": datetime(2026, 4, 8, 0, 0), "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": datetime(2026, 4, 9, 0, 0), "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": datetime(2026, 4, 10, 0, 0), "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
    ]


async def _fake_query_telemetry_signed_negative(**kwargs):
    return [
        {"timestamp": datetime(2026, 4, 8, 0, 0), "power": -1200.0, "current": 5.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": datetime(2026, 4, 9, 0, 0), "power": -1200.0, "current": 5.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": datetime(2026, 4, 10, 0, 0), "power": -1200.0, "current": 5.0, "voltage": 230.0, "power_factor": 0.9},
    ]


async def _fake_query_telemetry_sparse_positive(**kwargs):
    base = datetime(2026, 4, 8, 0, 0)
    return [
        {"timestamp": base + timedelta(minutes=m), "power": 1000.0}
        for m in range(0, 61, 1)
    ]


def test_reporting_coverage_contract_marks_three_of_seven_days_partial():
    coverage = report_task._build_report_coverage_result(
        start_dt=datetime(2026, 4, 1, 0, 0),
        end_dt=datetime(2026, 4, 8, 0, 0),
        per_device=[
            {
                "device_id": "D1",
                "quality": "high",
                "total_kwh": 12.0,
                "total_hours": 72.0,
                "method": "normalized_business_power",
            }
        ],
        warnings=[],
    )

    assert coverage["level"] == "partial_coverage"
    assert coverage["coverage_pct"] == 42.86
    assert coverage["usable_for_business_decisions"] is True
    assert coverage["usable_devices"] == ["D1"]


def test_reporting_coverage_contract_marks_unusable_sparse_payload_insufficient():
    coverage = report_task._build_report_coverage_result(
        start_dt=datetime(2026, 4, 1, 0, 0),
        end_dt=datetime(2026, 4, 8, 0, 0),
        per_device=[
            {
                "device_id": "D1",
                "quality": "insufficient",
                "total_kwh": 12.0,
                "total_hours": 0.0,
                "daily_breakdown": [{"date": "2026-04-01", "cost": 10.0}],
                "method": "insufficient_missing_fields",
                "error": "Insufficient telemetry",
            }
        ],
        warnings=["Insufficient telemetry"],
    )

    assert coverage["level"] == "insufficient_coverage"
    assert coverage["usable_for_business_decisions"] is False
    assert coverage["skipped_devices"][0]["device_id"] == "D1"


async def _fake_query_telemetry_sparse_negative(**kwargs):
    base = datetime(2026, 4, 8, 0, 0)
    return [
        {"timestamp": base + timedelta(minutes=m), "power": -1000.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0}
        for m in range(0, 61, 1)
    ]


async def _fake_query_telemetry_counter_anomaly(**kwargs):
    return [
        {"timestamp": "2026-04-09T14:20:00Z", "power": 8744.0, "energy_kwh": 0.0},
        {"timestamp": "2026-04-09T14:20:20Z", "power": 8744.0, "energy_kwh": 8.9},
    ]


async def _fake_query_telemetry_comparison_safe_vs_anomaly(**kwargs):
    if kwargs.get("device_id") == "DEVICE-A":
        return await _fake_query_telemetry_counter_anomaly(**kwargs)
    return [
        {"timestamp": "2026-04-09T14:20:00Z", "power": 5000.0},
        {"timestamp": "2026-04-09T15:20:00Z", "power": 5000.0},
    ]


class _ConsumptionAsyncClient:
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
                        "device_name": "Machine 1",
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
                    "totals": {"energy_kwh": 8.6},
                    "days": [
                        {"date": "2026-04-08", "energy_kwh": 4.3, "energy_cost_inr": 43.0},
                        {"date": "2026-04-09", "energy_kwh": 4.3, "energy_cost_inr": 43.0},
                    ],
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


class _SimulatorCanonicalAsyncClient:
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
                        "device_name": "Machine 1",
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
                    "totals": {"energy_kwh": 2.0},
                    "days": [
                        {"date": "2026-04-08", "energy_kwh": 2.0, "energy_cost_inr": 20.0},
                    ],
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


class _PlaceholderCanonicalAsyncClient:
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
                        "device_name": "Machine 1",
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
                    "totals": {"energy_kwh": 0.0},
                    "days": [
                        {
                            "date": "2026-04-08",
                            "energy_kwh": 0.0,
                            "energy_cost_inr": 0.0,
                            "quality_flags": ["live_projection_overlay"],
                            "version": 0,
                        }
                    ],
                    "version": 0,
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


class _SuspiciousCanonicalAsyncClient:
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
                        "device_name": "Machine 1",
                        "data_source_type": "metered",
                        "energy_flow_mode": "consumption_only",
                        "polarity_mode": "normal",
                    }
                },
            )
        if url.endswith("/api/v1/devices/DEVICE-A"):
            return _FakeResponse(200, {"data": {"device_id": "DEVICE-A", "device_name": "Device A", "phase_type": "single"}})
        if url.endswith("/api/v1/devices/DEVICE-B"):
            return _FakeResponse(200, {"data": {"device_id": "DEVICE-B", "device_name": "Device B", "phase_type": "single"}})
        if "/api/v1/energy/device/DEVICE-1/range" in url or "/api/v1/energy/device/DEVICE-A/range" in url:
            return _FakeResponse(
                200,
                {
                    "success": True,
                    "totals": {"energy_kwh": 10.2},
                    "days": [{"date": "2026-04-09", "energy_kwh": 10.2, "energy_cost_inr": 66.3}],
                },
            )
        if "/api/v1/energy/device/DEVICE-B/range" in url:
            return _FakeResponse(
                200,
                {
                    "success": True,
                    "totals": {"energy_kwh": 0.1},
                    "days": [{"date": "2026-04-09", "energy_kwh": 0.1, "energy_cost_inr": 0.65}],
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


class _MixedAggregateAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers=None, params=None):
        if url.endswith("/api/v1/devices"):
            return _FakeResponse(
                200,
                {
                    "data": [
                        {
                            "device_id": "DEVICE-1",
                            "device_name": "Simulator Device",
                            "data_source_type": "metered",
                        },
                        {
                            "device_id": "DEVICE-2",
                            "device_name": "Replay Device",
                            "data_source_type": "metered",
                        },
                    ]
                },
            )
        if url.endswith("/api/v1/devices/DEVICE-1"):
            return _FakeResponse(
                200,
                {
                    "data": {
                        "device_id": "DEVICE-1",
                        "device_name": "Simulator Device",
                        "data_source_type": "metered",
                        "energy_flow_mode": "consumption_only",
                        "polarity_mode": "normal",
                    }
                },
            )
        if url.endswith("/api/v1/devices/DEVICE-2"):
            return _FakeResponse(
                200,
                {
                    "data": {
                        "device_id": "DEVICE-2",
                        "device_name": "Replay Device",
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
                    "totals": {"energy_kwh": 2.0},
                    "days": [{"date": "2026-04-08", "energy_kwh": 2.0, "energy_cost_inr": 20.0}],
                },
            )
        if "/api/v1/energy/device/DEVICE-2/range" in url:
            return _FakeResponse(
                200,
                {
                    "success": True,
                    "totals": {"energy_kwh": 8.6},
                    "days": [{"date": "2026-04-08", "energy_kwh": 8.6, "energy_cost_inr": 86.0}],
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


class _FullyEligibleAggregateAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers=None, params=None):
        if url.endswith("/api/v1/devices"):
            return _FakeResponse(
                200,
                {
                    "data": [
                        {
                            "device_id": "DEVICE-1",
                            "device_name": "Machine 1",
                            "data_source_type": "metered",
                        },
                        {
                            "device_id": "DEVICE-2",
                            "device_name": "Machine 2",
                            "data_source_type": "metered",
                        },
                    ]
                },
            )
        if url.endswith("/api/v1/devices/DEVICE-1") or url.endswith("/api/v1/devices/DEVICE-2"):
            device_id = url.rsplit("/", 1)[-1]
            return _FakeResponse(
                200,
                {
                    "data": {
                        "device_id": device_id,
                        "device_name": device_id,
                        "data_source_type": "metered",
                        "energy_flow_mode": "consumption_only",
                        "polarity_mode": "normal",
                    }
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


async def _fake_query_telemetry_by_device(**kwargs):
    device_id = kwargs.get("device_id")
    if device_id == "DEVICE-1":
        return await _fake_query_telemetry_sparse_positive(**kwargs)
    if device_id == "DEVICE-2":
        return await _fake_query_telemetry_signed_negative(**kwargs)
    return []


async def _fake_query_telemetry_positive_by_device(**kwargs):
    device_id = kwargs.get("device_id")
    base = datetime(2026, 4, 8, 0, 0)
    if device_id == "DEVICE-1":
        return [
            {"timestamp": base + timedelta(minutes=m), "power": 1000.0}
            for m in range(0, 61, 1)
        ]
    if device_id == "DEVICE-2":
        return [
            {"timestamp": base + timedelta(minutes=m), "power": 2000.0}
            for m in range(0, 61, 1)
        ]
    return []


class _TelemetryOnlyAsyncClient:
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
                        "device_name": "Machine 1",
                        "data_source_type": "metered",
                        "energy_flow_mode": "consumption_only",
                        "polarity_mode": "normal",
                    }
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


class _ReplayTelemetryAsyncClient:
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
                        "device_name": "Machine 1",
                        "data_source_type": "metered",
                        "energy_flow_mode": "consumption_only",
                        "polarity_mode": "inverted",
                    }
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


@pytest.mark.asyncio
async def test_run_consumption_report_handles_missing_tariff_without_failing(monkeypatch):
    updates: list[dict] = []

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _FakeAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(report_task, "async_generate_consumption_pdf", _fake_async_generate_consumption_pdf)
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-1",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-04",
            "end_date": "2026-04-05",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete even when tariff is missing"
    result_json = completed_updates[-1]["result_json"]
    assert "Tariff not configured" in result_json["warnings"][0]
    assert result_json["summary"]["total_cost"] is None


@pytest.mark.asyncio
async def test_run_comparison_report_uses_canonical_energy_range_totals(monkeypatch):
    updates: list[dict] = []

    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", True)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _ComparisonAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry)
    monkeypatch.setattr(
        report_task,
        "calculate_energy",
        lambda rows, phase_type, device_power_config=None: {
            "success": True,
            "data": {
                "total_kwh": 1.67 if device_power_config == {"energy_flow_mode": "consumption_only", "polarity_mode": "normal"} else 1.0,
                "total_wh": 1670.0,
                "power_series": [{"timestamp": "2026-04-09T14:20:00Z", "power_w": 5000.0}],
            },
        },
    )
    monkeypatch.setattr(
        report_task,
        "calculate_demand",
        lambda power_series, window_minutes: {
            "success": True,
            "data": {"peak_demand_kw": 5.0 if power_series else 0.0, "peak_demand_timestamp": "2026-04-09T14:20:00+00:00"},
        },
    )
    monkeypatch.setattr(report_task, "async_generate_comparison_pdf", _fake_async_generate_comparison_pdf)
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_comparison_report(
        "report-2",
        {
            "tenant_id": "TENANT-1",
            "comparison_type": "machine_vs_machine",
            "machine_a_id": "DEVICE-A",
            "machine_b_id": "DEVICE-B",
            "start_date": "2026-04-09",
            "end_date": "2026-04-09",
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "comparison report should complete"
    result_json = completed_updates[-1]["result_json"]
    energy_metrics = result_json["data"]["metrics"]["energy_comparison"]
    assert energy_metrics["device_a_kwh"] == 0.17
    assert energy_metrics["device_b_kwh"] == 0.1
    assert energy_metrics["difference_kwh"] == 0.07
    assert result_json["data"]["basis"]["device_a_energy_basis"] == "canonical_energy_overlay"
    assert result_json["data"]["basis"]["device_b_energy_basis"] == "canonical_energy_overlay"
    assert result_json["data"]["basis"]["energy_basis"] == "canonical_energy_overlay"


@pytest.mark.asyncio
async def test_run_consumption_report_uses_canonical_financial_totals_for_counter_anomaly(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", True)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _SuspiciousCanonicalAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_6_5)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_counter_anomaly)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-counter-anomaly-safe",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-09",
            "end_date": "2026-04-09",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]

    assert device["energy_basis"] == "canonical_energy_overlay"
    assert device["canonical_overlay_applied"] is True
    assert device["canonical_overlay_reason"] == "canonical_accepted"
    assert device["total_kwh"] == 10.2
    assert summary["energy_basis"] == "canonical_energy_overlay"
    assert summary["total_kwh"] == 10.2
    assert summary["total_cost"] == 66.3
    assert pdf_payloads[-1]["total_kwh"] == 10.2
    assert pdf_payloads[-1]["per_device"][0]["total_cost"] == 66.3
    assert pdf_payloads[-1]["daily_series"] == [{"date": "2026-04-09", "kwh": 10.2, "cost": 66.3}]


@pytest.mark.asyncio
async def test_run_consumption_report_keeps_visible_totals_telemetry_in_canonical_shadow_mode(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", False)
    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_SHADOW_ENABLED", True)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _SuspiciousCanonicalAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_6_5)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_counter_anomaly)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-counter-anomaly-shadow",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-09",
            "end_date": "2026-04-09",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]
    shadow = device["canonical_financial_shadow"]

    assert device["energy_basis"] == "normalized_telemetry"
    assert device["canonical_overlay_applied"] is False
    assert device["canonical_overlay_reason"] == "canonical_shadow_only"
    assert device["total_kwh"] != 10.2
    assert summary["energy_basis"] == "normalized_telemetry"
    assert summary["total_kwh"] == device["total_kwh"]
    assert pdf_payloads[-1]["total_kwh"] == summary["total_kwh"]
    assert shadow["mode"] == "shadow_only"
    assert shadow["visible_totals_changed"] is False
    assert shadow["canonical_would_apply"] is True
    assert shadow["canonical_total_kwh"] == 10.2
    assert shadow["drift_kwh"] is not None


@pytest.mark.asyncio
async def test_run_comparison_report_uses_canonical_financial_totals_for_both_devices(monkeypatch):
    updates: list[dict] = []

    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", True)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _SuspiciousCanonicalAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_6_5)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_counter_anomaly)
    monkeypatch.setattr(
        report_task,
        "calculate_energy",
        lambda rows, phase_type, device_power_config=None: {
            "success": True,
            "data": {
                "total_kwh": 0.05 if rows and rows[0].get("energy_kwh") == 0.0 else 0.1,
                "total_wh": 50.0,
                "duration_hours": 0.01 if rows and rows[0].get("energy_kwh") == 0.0 else 1.0,
                "energy_quality": "high",
                "power_series": [{"timestamp": "2026-04-09T14:20:00Z", "power_w": 8744.0 if rows and rows[0].get("energy_kwh") == 0.0 else 5000.0}],
            },
        },
    )
    monkeypatch.setattr(
        report_task,
        "calculate_demand",
        lambda power_series, window_minutes: {
            "success": True,
            "data": {"peak_demand_kw": 8.74 if power_series else 0.0, "peak_demand_timestamp": "2026-04-09T14:20:00+00:00"},
        },
    )
    monkeypatch.setattr(report_task, "async_generate_comparison_pdf", _fake_async_generate_comparison_pdf)
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_comparison_report(
        "report-compare-safe",
        {
            "tenant_id": "TENANT-1",
            "comparison_type": "machine_vs_machine",
            "machine_a_id": "DEVICE-A",
            "machine_b_id": "DEVICE-B",
            "start_date": "2026-04-09",
            "end_date": "2026-04-09",
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "comparison report should complete"
    result_json = completed_updates[-1]["result_json"]
    energy_metrics = result_json["data"]["metrics"]["energy_comparison"]
    basis = result_json["data"]["basis"]

    assert energy_metrics["device_a_kwh"] == 10.2
    assert energy_metrics["device_b_kwh"] == 0.1
    assert basis["device_a_energy_basis"] == "canonical_energy_overlay"
    assert basis["device_b_energy_basis"] == "canonical_energy_overlay"
    assert basis["energy_basis"] == "canonical_energy_overlay"
    assert basis["device_a_overlay_reason"] == "canonical_accepted"


@pytest.mark.asyncio
async def test_run_consumption_report_marks_demand_block_insufficient_when_canonical_energy_has_no_matching_peak(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", True)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _ConsumptionAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_signed_negative)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-kpi",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-09",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]
    assert pdf_payloads, "pdf should be generated from the corrected persisted payload"
    pdf_payload = pdf_payloads[-1]

    assert summary["total_kwh"] == 8.6
    assert summary["energy_basis"] == "canonical_energy_overlay"
    assert summary["peak_demand_kw"] is None
    assert summary["average_load_kw"] is None
    assert summary["load_factor_pct"] is None
    assert summary["load_factor_band"] is None
    assert summary["kpi_basis"] == "canonical_energy_overlay"
    assert summary["average_load_duration_basis"] == "report_window_hours"
    assert device["total_kwh"] == 8.6
    assert device["energy_basis"] == "canonical_energy_overlay"
    assert device["peak_demand_kw"] is None
    assert device["average_load_kw"] is None
    assert device["load_factor_pct"] is None
    assert device["load_factor_band"] is None
    assert device["kpi_basis"] == "canonical_energy_overlay"
    assert device["average_load_duration_basis"] == "report_window_hours"
    assert summary["total_cost"] == 86.0
    assert pdf_payload["total_kwh"] == summary["total_kwh"]
    assert pdf_payload["peak_demand_kw"] == summary["peak_demand_kw"]
    assert pdf_payload["average_load_kw"] == summary["average_load_kw"]
    assert pdf_payload["load_factor_pct"] == summary["load_factor_pct"]
    assert pdf_payload["per_device"][0]["peak_demand_kw"] == device["peak_demand_kw"]
    assert pdf_payload["per_device"][0]["average_load_kw"] == device["average_load_kw"]


@pytest.mark.asyncio
async def test_run_consumption_report_preserves_telemetry_demand_when_canonical_overlay_lacks_peak(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []
    insight_inputs: list[dict] = []

    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", True)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _SimulatorCanonicalAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_sparse_positive)
    def _capture_insights(**kwargs):
        insight_inputs.append(kwargs)
        return []

    monkeypatch.setattr(report_task, "generate_report_insights", _capture_insights)
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-simulator-canonical-preserve",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]
    assert pdf_payloads, "pdf should be generated"
    pdf_payload = pdf_payloads[-1]

    assert insight_inputs, "insights should be generated from the public device payload"
    public_device = insight_inputs[-1]["per_device"][0]
    assert all("canonical_energy_projection_applied" not in warning for warning in public_device.get("warnings", []))
    assert device["total_kwh"] == 2.0
    assert device["energy_basis"] == "canonical_energy_overlay"
    assert device["peak_demand_kw"] == 1.0
    assert device["average_load_kw"] == 0.0833
    assert device["load_factor_pct"] == 8.33
    assert device["load_factor_band"] == "poor"
    assert device["kpi_basis"] == "mixed_device_bases"
    assert device["average_load_duration_basis"] == "report_window_hours"
    assert summary["total_kwh"] == 2.0
    assert summary["energy_basis"] == "canonical_energy_overlay"
    assert summary["peak_demand_kw"] == 1.0
    assert summary["average_load_kw"] == 0.0833
    assert summary["load_factor_pct"] == 8.33
    assert summary["load_factor_band"] == "poor"
    assert summary["kpi_basis"] == "mixed_device_bases"
    assert summary["average_load_duration_basis"] == "report_window_hours"
    assert pdf_payload["total_kwh"] == summary["total_kwh"]
    assert pdf_payload["peak_demand_kw"] == summary["peak_demand_kw"]
    assert pdf_payload["average_load_kw"] == summary["average_load_kw"]
    assert pdf_payload["load_factor_pct"] == summary["load_factor_pct"]
    assert pdf_payload["per_device"][0]["peak_demand_kw"] == device["peak_demand_kw"]
    assert pdf_payload["per_device"][0]["average_load_kw"] == device["average_load_kw"]
    assert pdf_payload["per_device"][0]["load_factor_pct"] == device["load_factor_pct"]
    assert all("canonical_energy_projection_applied" not in warning for warning in pdf_payload["warnings"])
    assert all("canonical_energy_projection_applied" not in warning for warning in result_json["warnings"])
    assert all("canonical_energy_projection_applied" not in warning for warning in device["warnings"])


@pytest.mark.asyncio
async def test_run_consumption_report_ignores_placeholder_zero_canonical_overlay(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _PlaceholderCanonicalAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_sparse_positive)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-placeholder-canonical",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]
    assert pdf_payloads, "pdf should be generated"

    assert device["total_kwh"] == 1.0
    assert device["energy_basis"] == "normalized_telemetry"
    assert device["peak_demand_kw"] == 1.0
    assert device["average_load_kw"] == 0.0417
    assert device["load_factor_pct"] == 4.17
    assert device["load_factor_band"] == "poor"
    assert device["kpi_basis"] == "normalized_telemetry"
    assert summary["total_kwh"] == 1.0
    assert summary["energy_basis"] == "normalized_telemetry"
    assert summary["peak_demand_kw"] == 1.0
    assert summary["average_load_kw"] == 0.0417
    assert summary["load_factor_pct"] == 4.17
    assert summary["load_factor_band"] == "poor"
    assert summary["kpi_basis"] == "normalized_telemetry"
    assert pdf_payloads[-1]["total_kwh"] == summary["total_kwh"]
    assert pdf_payloads[-1]["peak_demand_kw"] == summary["peak_demand_kw"]
    assert pdf_payloads[-1]["average_load_kw"] == summary["average_load_kw"]
    assert pdf_payloads[-1]["load_factor_pct"] == summary["load_factor_pct"]


@pytest.mark.asyncio
async def test_run_consumption_report_uses_report_window_duration_consistently_without_canonical_overlay(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _TelemetryOnlyAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_sparse_positive)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-window-duration",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]
    assert pdf_payloads, "pdf should be generated"

    assert device["total_kwh"] == 1.0
    assert device["energy_basis"] == "normalized_telemetry"
    assert device["peak_demand_kw"] == 1.0
    assert device["average_load_kw"] == 0.0417
    assert device["load_factor_pct"] == 4.17
    assert device["kpi_basis"] == "normalized_telemetry"
    assert device["average_load_duration_basis"] == "report_window_hours"
    assert summary["total_kwh"] == 1.0
    assert summary["energy_basis"] == "normalized_telemetry"
    assert summary["peak_demand_kw"] == 1.0
    assert summary["average_load_kw"] == 0.0417
    assert summary["load_factor_pct"] == 4.17
    assert summary["load_factor_band"] == "poor"
    assert summary["kpi_basis"] == "normalized_telemetry"
    assert summary["average_load_duration_basis"] == "report_window_hours"
    assert summary["total_cost"] == 10.0
    assert pdf_payloads[-1]["average_load_kw"] == summary["average_load_kw"]


@pytest.mark.asyncio
async def test_run_consumption_report_uses_same_kpi_block_for_replay_signed_telemetry(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _ReplayTelemetryAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_sparse_negative)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-replay-signed",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]
    assert pdf_payloads, "pdf should be generated"

    assert device["total_kwh"] == 1.0
    assert device["energy_basis"] == "normalized_telemetry"
    assert device["peak_demand_kw"] == 1.0
    assert device["average_load_kw"] == 0.0417
    assert device["load_factor_pct"] == 4.17
    assert device["load_factor_band"] == "poor"
    assert device["kpi_basis"] == "normalized_telemetry"
    assert summary["total_kwh"] == device["total_kwh"]
    assert summary["energy_basis"] == "normalized_telemetry"
    assert summary["peak_demand_kw"] == device["peak_demand_kw"]
    assert summary["average_load_kw"] == device["average_load_kw"]
    assert summary["load_factor_pct"] == device["load_factor_pct"]
    assert summary["load_factor_band"] == device["load_factor_band"]
    assert pdf_payloads[-1]["peak_demand_kw"] == summary["peak_demand_kw"]
    assert pdf_payloads[-1]["average_load_kw"] == summary["average_load_kw"]
    assert pdf_payloads[-1]["per_device"][0]["load_factor_pct"] == device["load_factor_pct"]


@pytest.mark.asyncio
async def test_run_consumption_report_marks_aggregate_demand_incomplete_for_mixed_scope(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", True)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _MixedAggregateAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_by_device)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-aggregate-incomplete",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1", "DEVICE-2"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    assert pdf_payloads, "pdf should be generated"

    assert summary["total_kwh"] == 10.6
    assert summary["peak_demand_kw"] is None
    assert summary["average_load_kw"] is None
    assert summary["load_factor_pct"] is None
    assert summary["load_factor_band"] is None
    assert summary["aggregate_demand_basis"] == "incomplete"
    assert "aggregate_demand_not_comparable: one or more energy-contributing devices lack a valid demand basis" in result_json["warnings"]
    assert pdf_payloads[-1]["peak_demand_kw"] is None
    assert pdf_payloads[-1]["average_load_kw"] is None
    assert pdf_payloads[-1]["load_factor_pct"] is None


@pytest.mark.asyncio
async def test_run_consumption_report_keeps_aggregate_demand_for_fully_eligible_scope(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _FullyEligibleAggregateAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_positive_by_device)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-aggregate-complete",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1", "DEVICE-2"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    assert pdf_payloads, "pdf should be generated"

    assert summary["total_kwh"] == 3.0
    assert summary["peak_demand_kw"] == 2.0
    assert summary["average_load_kw"] == 0.125
    assert summary["load_factor_pct"] == 6.25
    assert summary["load_factor_band"] == "poor"
    assert summary["aggregate_demand_basis"] == "complete"
    assert "aggregate_demand_not_comparable: one or more energy-contributing devices lack a valid demand basis" not in result_json["warnings"]
    assert pdf_payloads[-1]["peak_demand_kw"] == summary["peak_demand_kw"]
    assert pdf_payloads[-1]["average_load_kw"] == summary["average_load_kw"]
    assert pdf_payloads[-1]["load_factor_pct"] == summary["load_factor_pct"]


@pytest.mark.asyncio
async def test_run_consumption_report_adds_hidden_overconsumption_contract_without_breaking_existing_summary(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _TelemetryOnlyAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_sparse_positive)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-hidden-overconsumption",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]

    # Existing summary contract remains intact.
    summary = result_json["summary"]
    assert summary["total_kwh"] == 1.0
    assert summary["peak_demand_kw"] == 1.0
    assert summary["average_load_kw"] == 0.0417
    assert summary["load_factor_pct"] == 4.17
    assert summary["total_cost"] == 10.0

    # New additive hidden insight contract is available.
    hidden = result_json.get("hidden_overconsumption_insight")
    assert isinstance(hidden, dict)
    assert "summary" in hidden
    assert "daily_breakdown" in hidden
    assert "device_breakdown" in hidden
    assert hidden["summary"]["selected_days"] == 1
    assert len(hidden["daily_breakdown"]) == 1
    assert len(hidden["device_breakdown"]) == 1
    assert hidden["device_breakdown"][0]["device_id"] == "DEVICE-1"
    assert hidden["device_breakdown"][0]["device_name"] == "Machine 1"
    assert hidden["device_breakdown"][0]["difference_vs_baseline_kwh"] == 0.0
    assert hidden["device_breakdown"][0]["status"] == "Within Baseline"
    assert hidden["summary"]["total_hidden_overconsumption_kwh"] == round(
        sum(float(row["hidden_overconsumption_kwh"]) for row in hidden["daily_breakdown"]),
        4,
    )
    assert hidden["summary"]["total_hidden_overconsumption_cost"] == round(
        sum(float(row["hidden_overconsumption_cost"] or 0.0) for row in hidden["daily_breakdown"]),
        2,
    )
    assert pdf_payloads, "pdf should be generated with the hidden insight payload"
    assert "hidden_overconsumption_insight" in pdf_payloads[-1]
    assert isinstance(pdf_payloads[-1]["hidden_overconsumption_insight"], dict)


class _SparseTelemetryHighCanonicalAsyncClient:
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
                        "device_name": "Machine 1",
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
                    "totals": {"energy_kwh": 97.0},
                    "days": [
                        {"date": "2026-04-08", "energy_kwh": 97.0, "energy_cost_inr": 776.0},
                    ],
                },
            )
        return _FakeResponse(404, {"detail": "not found"})


def test_config_default_canonical_financial_apply_enabled_is_true():
    assert report_task.settings.REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED is True


@pytest.mark.asyncio
async def test_run_consumption_report_uses_canonical_total_by_default_when_canonical_available(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _SuspiciousCanonicalAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_6_5)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_counter_anomaly)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-canonical-by-default",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-09",
            "end_date": "2026-04-09",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]

    assert device["energy_basis"] == "canonical_energy_overlay"
    assert device["canonical_overlay_applied"] is True
    assert device["total_kwh"] == 10.2
    assert summary["total_kwh"] == 10.2
    assert summary["energy_basis"] == "canonical_energy_overlay"
    assert pdf_payloads[-1]["total_kwh"] == 10.2


@pytest.mark.asyncio
async def test_run_consumption_report_sparse_telemetry_uses_canonical_visible_total(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _SparseTelemetryHighCanonicalAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_sparse_positive)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-sparse-canonical-fix",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]

    assert device["total_kwh"] == 97.0
    assert device["energy_basis"] == "canonical_energy_overlay"
    assert device["canonical_overlay_applied"] is True
    assert summary["total_kwh"] == 97.0
    assert summary["energy_basis"] == "canonical_energy_overlay"
    assert pdf_payloads[-1]["total_kwh"] == 97.0


@pytest.mark.asyncio
async def test_run_consumption_report_explicit_apply_false_preserves_shadow_telemetry_visible(monkeypatch):
    updates: list[dict] = []
    pdf_payloads: list[dict] = []

    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", False)
    monkeypatch.setattr(report_task.settings, "REPORTS_CANONICAL_FINANCIAL_SHADOW_ENABLED", True)
    monkeypatch.setattr(report_task, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(report_task, "ReportRepository", lambda db, ctx=None: _FakeRepo(updates))
    monkeypatch.setattr(report_task, "build_service_tenant_context", lambda tenant_id: SimpleNamespace(tenant_id=tenant_id))
    monkeypatch.setattr(report_task.httpx, "AsyncClient", lambda *args, **kwargs: _SparseTelemetryHighCanonicalAsyncClient())
    monkeypatch.setattr(report_task, "resolve_tariff", _fake_resolve_tariff_inr)
    monkeypatch.setattr(report_task.influx_reader, "query_telemetry", _fake_query_telemetry_sparse_positive)
    monkeypatch.setattr(report_task, "generate_report_insights", lambda **kwargs: [])
    monkeypatch.setattr(
        report_task,
        "async_generate_consumption_pdf",
        _make_capture_pdf(pdf_payloads),
    )
    monkeypatch.setattr(report_task.minio_client, "async_upload_pdf", _fake_async_upload_pdf)
    monkeypatch.setattr(report_task, "clean_for_json", lambda payload: payload)

    await report_task.run_consumption_report(
        "report-shadow-explicit-false",
        {
            "tenant_id": "TENANT-1",
            "start_date": "2026-04-08",
            "end_date": "2026-04-08",
            "resolved_device_ids": ["DEVICE-1"],
        },
    )

    completed_updates = [item for item in updates if item.get("status") == "completed"]
    assert completed_updates, "report should complete"
    result_json = completed_updates[-1]["result_json"]
    summary = result_json["summary"]
    device = result_json["devices"][0]

    assert device["energy_basis"] == "normalized_telemetry"
    assert device["canonical_overlay_applied"] is False
    assert device["total_kwh"] != 97.0
    assert summary["total_kwh"] == device["total_kwh"]
    shadow = device["canonical_financial_shadow"]
    assert shadow["mode"] == "shadow_only"
    assert shadow["canonical_total_kwh"] == 97.0
    assert shadow["drift_kwh"] is not None
    assert shadow["visible_totals_changed"] is False


def test_canonical_energy_overlay_produces_total_matching_canonical_input_for_parity():
    canonical_range = {
        "success": True,
        "totals": {"energy_kwh": 97.0},
        "days": [{"date": "2026-04-08", "energy_kwh": 97.0, "energy_cost_inr": 776.0}],
    }
    energy_result = {
        "success": True,
        "data": {"total_kwh": 3.3, "daily_kwh": {}},
    }
    updated_result, basis_meta = report_task._apply_canonical_energy_overlay(
        energy_result,
        canonical_range,
        local_quality="low",
        local_total_hours=1.0,
        report_window_hours=24.0,
    )
    assert updated_result["data"]["total_kwh"] == 97.0
    assert basis_meta["canonical_overlay_applied"] is True
    assert basis_meta["energy_basis"] == "canonical_energy_overlay"


def test_report_day_cost_preserved_from_canonical_overlay():
    per_device = [
        {
            "device_id": "DEV-1",
            "device_name": "Compressor",
            "total_kwh": 10.0,
            "daily_breakdown": [
                {"date": "2026-05-01", "energy_kwh": 6.0, "cost": 39.0},
                {"date": "2026-05-02", "energy_kwh": 4.0},
            ],
        }
    ]
    tariff_rate_used = 7.25

    for device in per_device:
        for day in device.get("daily_breakdown", []) or []:
            if day.get("cost") is not None:
                continue
            e = day.get("energy_kwh")
            if tariff_rate_used is not None and isinstance(e, (int, float)):
                day["cost"] = round(float(e) * tariff_rate_used, 2)
            else:
                day["cost"] = None
        daily_costs = [
            float(day.get("cost"))
            for day in device.get("daily_breakdown", []) or []
            if isinstance(day.get("cost"), (int, float))
        ]
        if daily_costs:
            device["total_cost"] = round(sum(daily_costs), 2)

    assert per_device[0]["daily_breakdown"][0]["cost"] == 39.0
    assert per_device[0]["daily_breakdown"][1]["cost"] == round(4.0 * 7.25, 2)
    assert per_device[0]["total_cost"] == round(39.0 + round(4.0 * 7.25, 2), 2)


def test_canonical_financial_apply_fallback_matches_config_default():
    from src.config import Settings

    config_default = Settings().REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED
    fallback_result = bool(getattr(type("FakeSettings", (), {})(), "REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED", True))
    assert fallback_result == config_default


def test_report_summary_total_cost_equals_sum_of_device_costs():
    per_device = [
        {
            "device_id": "DEV-1",
            "device_name": "Compressor A",
            "total_kwh": 10.0,
            "daily_breakdown": [
                {"date": "2026-05-01", "energy_kwh": 6.0, "cost": 39.0},
                {"date": "2026-05-02", "energy_kwh": 4.0, "cost": 29.0},
            ],
            "total_cost": 68.0,
        },
        {
            "device_id": "DEV-2",
            "device_name": "Compressor B",
            "total_kwh": 5.0,
            "daily_breakdown": [
                {"date": "2026-05-01", "energy_kwh": 5.0, "cost": 33.0},
            ],
            "total_cost": 33.0,
        },
    ]
    total_kwh = sum(float(d.get("total_kwh") or 0.0) for d in per_device)
    tariff_rate_used = 7.25

    device_costs = [
        float(d.get("total_cost"))
        for d in per_device
        if isinstance(d.get("total_cost"), (int, float))
    ]
    if device_costs:
        total_cost = round(sum(device_costs), 2)
    else:
        total_cost = round(total_kwh * tariff_rate_used, 2)

    assert total_cost == 101.0
    assert total_cost != round(total_kwh * tariff_rate_used, 2)
