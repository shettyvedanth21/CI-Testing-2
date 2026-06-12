from __future__ import annotations

import os
import sys
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

from src.services.emission_factor_cache import (  # noqa: E402
    EmissionFactorCache,
    build_report_co2_overview,
)


def _factor_payload_configured() -> dict:
    return {
        "configured": True,
        "factor_value": 0.716,
        "factor_unit": "kg_co2_per_kwh",
        "method": "location_based",
        "country": "IN",
        "region": "all_india_grid",
        "source_name": "Central Electricity Authority CO2 Baseline Database",
        "source_version": "Version 19.0",
        "factor_year": "FY2022-23",
        "factor_source": "platform_default",
    }


def _factor_payload_unconfigured() -> dict:
    return {
        "configured": False,
        "factor_value": None,
        "factor_unit": "kg_co2_per_kwh",
        "method": None,
        "country": None,
        "region": None,
        "source_name": None,
        "source_version": None,
        "factor_year": None,
        "factor_source": "unconfigured",
    }


def _per_device(device_id="AD00000001", total_kwh=100.0, overtime_kwh=None) -> dict:
    d = {
        "device_id": device_id,
        "device_name": f"Device {device_id}",
        "total_kwh": total_kwh,
        "energy_basis": "normalized_telemetry",
    }
    if overtime_kwh is not None:
        d["overtime"] = {"total_overtime_kwh": overtime_kwh}
    return d


def _overtime_summary(total_kwh=20.0, configured_devices=1) -> dict:
    return {
        "total_kwh": total_kwh,
        "total_minutes": 120.0,
        "total_hours": 2.0,
        "total_cost": 160.0,
        "configured_devices": configured_devices,
        "devices_without_shift": 0,
        "device_count": 1,
    }


# ── build_report_co2_overview tests ──


def test_total_co2_matches_kwh_times_factor():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0)],
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["available"] is True
    assert overview["total_co2_kg"] == round(100.0 * 0.716, 4)


def test_per_device_co2_sum_equals_total():
    devices = [
        _per_device(device_id="AD01", total_kwh=60.0, overtime_kwh=8.0),
        _per_device(device_id="AD02", total_kwh=40.0, overtime_kwh=12.0),
    ]
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=devices,
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_configured(),
    )
    per_device_sum = sum(d["co2_kg"] for d in overview["per_device"])
    assert abs(per_device_sum - overview["total_co2_kg"]) < 0.01


def test_off_shift_co2_matches_overtime_kwh_times_factor():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0, overtime_kwh=20.0)],
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["off_shift_co2_kg"] == round(20.0 * 0.716, 4)
    assert overview["off_shift_available"] is True


def test_co2_unavailable_when_factor_not_configured():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0)],
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_unconfigured(),
    )
    assert overview["available"] is False
    assert overview["reason"] == "emission_factor_not_configured"
    assert "total_co2_kg" not in overview or overview.get("total_co2_kg") is None


def test_zero_energy_is_zero_co2():
    overview = build_report_co2_overview(
        total_kwh=0.0,
        per_device=[_per_device(total_kwh=0.0)],
        overtime_summary=_overtime_summary(total_kwh=0.0),
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["available"] is True
    assert overview["total_co2_kg"] == 0.0


def test_co2_uses_committed_energy_basis():
    overview = build_report_co2_overview(
        total_kwh=95.0,
        per_device=[_per_device(total_kwh=95.0)],
        overtime_summary=_overtime_summary(total_kwh=15.0),
        energy_basis="canonical_energy_overlay",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["energy_basis"] == "canonical_energy_overlay"
    assert overview["total_energy_basis_kwh"] == 95.0
    assert overview["total_co2_kg"] == round(95.0 * 0.716, 4)


def test_co2_with_canonical_overlay_applied():
    overview = build_report_co2_overview(
        total_kwh=90.0,
        per_device=[{
            "device_id": "AD01",
            "device_name": "Device AD01",
            "total_kwh": 90.0,
            "energy_basis": "canonical_energy_overlay",
            "overtime": {"total_overtime_kwh": 10.0},
        }],
        overtime_summary=_overtime_summary(total_kwh=10.0),
        energy_basis="canonical_energy_overlay",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["per_device"][0]["energy_basis"] == "canonical_energy_overlay"
    assert overview["per_device"][0]["co2_kg"] == round(90.0 * 0.716, 4)


def test_co2_with_mixed_energy_basis():
    devices = [
        _per_device(device_id="AD01", total_kwh=60.0),
        {
            "device_id": "AD02",
            "device_name": "Device AD02",
            "total_kwh": 35.0,
            "energy_basis": "canonical_energy_overlay",
        },
    ]
    overview = build_report_co2_overview(
        total_kwh=95.0,
        per_device=devices,
        overtime_summary=_overtime_summary(total_kwh=10.0),
        energy_basis="mixed_device_bases",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["energy_basis"] == "mixed_device_bases"
    per_device_sum = sum(d["co2_kg"] for d in overview["per_device"])
    assert abs(per_device_sum - overview["total_co2_kg"]) < 0.01


def test_factor_metadata_is_auditable():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0)],
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["factor"]["value"] == 0.716
    assert overview["factor"]["unit"] == "kg_co2_per_kwh"
    assert overview["factor"]["method"] == "location_based"
    assert overview["factor"]["country"] == "IN"
    assert overview["factor"]["region"] == "all_india_grid"
    assert overview["factor"]["source"] == "Central Electricity Authority CO2 Baseline Database"
    assert overview["factor"]["source_version"] == "Version 19.0"
    assert overview["factor"]["factor_year"] == "FY2022-23"
    assert overview["factor_source"] == "platform_default"


def test_off_shift_co2_absent_when_no_overtime_summary():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0)],
        overtime_summary=None,
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["off_shift_co2_kg"] is None
    assert overview["off_shift_available"] is False


def test_per_device_off_shift_co2_absent_when_no_overtime():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0, overtime_kwh=None)],
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_configured(),
    )
    assert overview["per_device"][0]["off_shift_co2_kg"] is None


def test_payload_backward_compatibility():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0)],
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload=_factor_payload_configured(),
    )
    assert "calculation_version" in overview
    assert overview["calculation_version"] == "co2_report_v1"
    assert isinstance(overview["per_device"], list)
    assert isinstance(overview["factor"], dict)


def test_negative_factor_returns_unavailable():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0)],
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload={
            "configured": True,
            "factor_value": -0.5,
            "factor_unit": "kg_co2_per_kwh",
            "method": "location_based",
            "country": "IN",
            "region": "all_india_grid",
            "source_name": "test",
            "factor_source": "test_invalid",
        },
    )
    assert overview["available"] is False
    assert overview["reason"] == "emission_factor_not_configured"


def test_zero_factor_returns_unavailable():
    overview = build_report_co2_overview(
        total_kwh=100.0,
        per_device=[_per_device(total_kwh=100.0)],
        overtime_summary=_overtime_summary(total_kwh=20.0),
        energy_basis="normalized_telemetry",
        factor_payload={
            "configured": True,
            "factor_value": 0.0,
            "factor_unit": "kg_co2_per_kwh",
            "method": "location_based",
            "country": "IN",
            "region": "all_india_grid",
            "source_name": "test",
            "factor_source": "test_zero",
        },
    )
    assert overview["available"] is False


# ── EmissionFactorCache unit tests (no DB, cache-only) ──


@pytest.mark.asyncio
async def test_cache_returns_unconfigured_when_empty():
    EmissionFactorCache.invalidate()
    result = await EmissionFactorCache.get("SH00000001")
    assert result["configured"] is False
    assert result["factor_source"] == "unconfigured"


@pytest.mark.asyncio
async def test_cache_serves_stale_on_db_error():
    EmissionFactorCache._value["SH_TEST"] = {
        "configured": True,
        "factor_value": 0.716,
        "factor_unit": "kg_co2_per_kwh",
        "method": "location_based",
        "country": "IN",
        "region": "all_india_grid",
        "source_name": "test",
        "factor_source": "platform_default",
    }
    from datetime import datetime, timezone, timedelta
    EmissionFactorCache._expires_at["SH_TEST"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    class _BrokenSession:
        async def __aenter__(self):
            raise Exception("DB unavailable")
        async def __aexit__(self, *args):
            pass

    import src.services.emission_factor_cache as efc_module
    original_session = efc_module.AsyncSessionLocal
    efc_module.AsyncSessionLocal = _BrokenSession
    try:
        result = await EmissionFactorCache.get("SH_TEST")
        assert result["configured"] is True
        assert result.get("stale") is True
    finally:
        efc_module.AsyncSessionLocal = original_session
        EmissionFactorCache.invalidate()


# ── Integration: co2_overview in report_task pipeline ──


def test_report_task_imports_co2_module():
    from src.tasks import report_task
    assert hasattr(report_task, "build_report_co2_overview")
    assert hasattr(report_task, "EmissionFactorCache")


def test_schema_version_bumped():
    from src.tasks import report_task
    assert "4.0" in open(report_task.__file__).read()
