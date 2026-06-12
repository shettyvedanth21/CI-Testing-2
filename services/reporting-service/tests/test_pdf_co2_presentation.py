from __future__ import annotations

import os
import sys
from pathlib import Path

from jinja2 import Template

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

from src.pdf.builder import (
    _prepare_co2_for_template,
    _display_factor_unit,
    _display_factor_source,
    _build_factor_source_display,
    get_consumption_report_template,
)  # noqa: E402


def _co2_overview_available() -> dict:
    return {
        "available": True,
        "reason": None,
        "calculation_version": "co2_report_v1",
        "total_co2_kg": 71.6,
        "total_energy_basis_kwh": 100.0,
        "energy_basis": "normalized_telemetry",
        "off_shift_co2_kg": 14.32,
        "off_shift_energy_basis_kwh": 20.0,
        "off_shift_available": True,
        "per_device": [
            {
                "device_id": "AD00000001",
                "device_name": "Machine A",
                "co2_kg": 42.96,
                "energy_basis_kwh": 60.0,
                "energy_basis": "normalized_telemetry",
                "off_shift_co2_kg": 8.592,
            },
            {
                "device_id": "AD00000002",
                "device_name": "Machine B",
                "co2_kg": 28.64,
                "energy_basis_kwh": 40.0,
                "energy_basis": "normalized_telemetry",
                "off_shift_co2_kg": 5.728,
            },
        ],
        "factor": {
            "value": 0.716,
            "unit": "kg_co2_per_kwh",
            "method": "location_based",
            "country": "IN",
            "region": "all_india_grid",
            "source": "Central Electricity Authority CO2 Baseline Database",
            "source_version": "Version 19.0",
            "factor_year": "FY2022-23",
        },
        "factor_source": "platform_default",
    }


def _co2_overview_unavailable() -> dict:
    return {
        "available": False,
        "reason": "emission_factor_not_configured",
        "factor_source": "unconfigured",
        "calculation_version": "co2_report_v1",
    }


def _base_payload(**overrides) -> dict:
    payload = {
        "report_theme_class": "theme-energy",
        "report_id": "report-1",
        "generated_at": "2026-06-04 12:00",
        "device_label": "All Machines",
        "start_date": "2026-06-01",
        "end_date": "2026-06-04",
        "overall_quality": "high",
        "tariff_rate_used": 8.3,
        "currency": "INR",
        "peak_timestamp": "2026-06-02 10:00",
        "total_kwh": 100.0,
        "peak_demand_kw": 5.0,
        "load_factor_pct": 45.6,
        "average_load_kw": 2.3,
        "total_cost": 830.0,
        "tariff_fetched_at": "2026-06-01 09:00",
        "per_device": [
            {
                "device_id": "AD00000001",
                "device_name": "Machine A",
                "total_kwh": 60.0,
                "peak_demand_kw": 3.0,
                "load_factor_pct": 50.0,
                "quality": "high",
                "total_cost": 498.0,
            },
            {
                "device_id": "AD00000002",
                "device_name": "Machine B",
                "total_kwh": 40.0,
                "peak_demand_kw": 2.0,
                "load_factor_pct": 40.0,
                "quality": "medium",
                "total_cost": 332.0,
            },
        ],
        "daily_series": [],
        "charts": {},
        "overtime_summary": None,
        "warnings": [],
        "insights": [],
        "hidden_overconsumption_insight": None,
        "co2_overview": None,
    }
    payload.update(overrides)
    return payload


def _render(payload: dict) -> str:
    _prepare_co2_for_template(payload)
    return Template(get_consumption_report_template()).render(**payload)


# ── _prepare_co2_for_template unit tests ──


def test_prepare_co2_merges_co2_kg_into_per_device():
    data = _base_payload(
        per_device=[
            {"device_id": "AD00000001", "device_name": "Machine A", "total_kwh": 60.0},
            {"device_id": "AD00000002", "device_name": "Machine B", "total_kwh": 40.0},
        ],
        co2_overview=_co2_overview_available(),
    )
    _prepare_co2_for_template(data)
    assert data["per_device"][0]["co2_kg"] == 42.96
    assert data["per_device"][1]["co2_kg"] == 28.64


def test_prepare_co2_rounds_total_and_off_shift_to_2dp():
    overview = _co2_overview_available()
    overview["total_co2_kg"] = 71.60001
    overview["off_shift_co2_kg"] = 14.31999
    data = _base_payload(
        per_device=[
            {"device_id": "AD01", "device_name": "A", "total_kwh": 60.0},
        ],
        co2_overview=overview,
    )
    _prepare_co2_for_template(data)
    assert data["co2_overview"]["total_co2_kg"] == 71.6
    assert data["co2_overview"]["off_shift_co2_kg"] == 14.32


def test_prepare_co2_rounds_per_device_co2_to_2dp():
    overview = _co2_overview_available()
    overview["per_device"][0]["co2_kg"] = 42.960001
    data = _base_payload(
        per_device=[
            {"device_id": "AD00000001", "device_name": "Machine A", "total_kwh": 60.0},
        ],
        co2_overview=overview,
    )
    _prepare_co2_for_template(data)
    assert data["per_device"][0]["co2_kg"] == 42.96


def test_prepare_co2_noop_when_co2_overview_missing():
    data = _base_payload()
    _prepare_co2_for_template(data)
    assert "co2_kg" not in data["per_device"][0]


def test_prepare_co2_noop_when_co2_overview_unavailable():
    data = _base_payload(co2_overview=_co2_overview_unavailable())
    _prepare_co2_for_template(data)
    assert "co2_kg" not in data["per_device"][0]


def test_prepare_co2_handles_device_id_mismatch_gracefully():
    overview = _co2_overview_available()
    data = _base_payload(
        per_device=[
            {"device_id": "ZZ99", "device_name": "Unknown", "total_kwh": 10.0},
        ],
        co2_overview=overview,
    )
    _prepare_co2_for_template(data)
    assert "co2_kg" not in data["per_device"][0]


def test_prepare_co2_sets_co2_kg_none_when_per_device_entry_has_none():
    overview = _co2_overview_available()
    overview["per_device"][0]["co2_kg"] = None
    data = _base_payload(
        per_device=[
            {"device_id": "AD00000001", "device_name": "Machine A", "total_kwh": 60.0},
        ],
        co2_overview=overview,
    )
    _prepare_co2_for_template(data)
    assert data["per_device"][0]["co2_kg"] is None


def test_prepare_co2_handles_empty_per_device():
    overview = _co2_overview_available()
    overview["per_device"] = []
    data = _base_payload(per_device=[], co2_overview=overview)
    _prepare_co2_for_template(data)


# ── Template rendering tests ──


def test_co2_kpi_section_renders_when_available():
    html = _render(_base_payload(co2_overview=_co2_overview_available()))
    assert "Total CO₂" in html
    assert "Off-Shift CO₂" in html
    assert "71.6" in html
    assert "14.32" in html


def test_co2_factor_footnote_renders_when_available():
    html = _render(_base_payload(co2_overview=_co2_overview_available()))
    assert "0.716" in html
    assert "kg CO\u2082/kWh" in html
    assert "Central Electricity Authority CO2 Baseline Database" in html
    assert "Platform Default" in html
    assert "CO₂ estimated using emission factor" in html


def test_co2_unavailable_notice_renders_when_not_configured():
    html = _render(_base_payload(co2_overview=_co2_overview_unavailable()))
    assert "CO₂ emissions estimation is unavailable" in html
    assert "emission factor has not been configured" in html


def test_no_co2_section_when_co2_overview_absent():
    html = _render(_base_payload())
    assert "Total CO₂" not in html
    assert "Off-Shift CO₂" not in html
    assert "CO₂ emissions estimation is unavailable" not in html


def test_co2_column_in_device_breakdown_when_available():
    html = _render(_base_payload(co2_overview=_co2_overview_available()))
    assert "CO₂ (kg)" in html
    assert "42.96" in html
    assert "28.64" in html


def test_no_co2_column_in_device_breakdown_when_absent():
    html = _render(_base_payload())
    assert "CO₂ (kg)" not in html


def test_co2_off_shift_shows_na_when_not_available():
    overview = _co2_overview_available()
    overview["off_shift_available"] = False
    overview["off_shift_co2_kg"] = None
    html = _render(_base_payload(co2_overview=overview))
    assert "Off-Shift CO₂" in html
    assert "Shift configuration not available" in html


def test_co2_section_preserves_existing_executive_summary():
    html = _render(_base_payload(co2_overview=_co2_overview_available()))
    assert "Executive Summary" in html
    assert "Total Energy" in html
    assert "Peak Demand" in html
    assert "Load Factor" in html
    assert "Total Cost" in html


def test_co2_section_preserves_device_breakdown_cost_column():
    html = _render(_base_payload(co2_overview=_co2_overview_available()))
    assert ">Cost<" in html
    assert "498.0" in html


def test_zero_co2_renders_as_zero():
    overview = _co2_overview_available()
    overview["total_co2_kg"] = 0.0
    overview["off_shift_co2_kg"] = 0.0
    html = _render(_base_payload(co2_overview=overview))
    assert "0.0 kg" in html


def test_display_factor_unit_maps_kg_co2_per_kwh():
    assert _display_factor_unit("kg_co2_per_kwh") == "kg CO\u2082/kWh"


def test_display_factor_unit_passes_through_unknown():
    assert _display_factor_unit("tonnes_per_mwh") == "tonnes_per_mwh"


def test_display_factor_source_maps_platform_default():
    assert _display_factor_source("platform_default") == "Platform Default"


def test_display_factor_source_maps_tenant_default():
    assert _display_factor_source("tenant_default") == "Organisation Default"


def test_display_factor_source_omits_unconfigured():
    assert _display_factor_source("unconfigured") == ""


def test_display_factor_source_omits_unknown():
    assert _display_factor_source("unknown") == ""


def test_build_factor_source_display_combines_source_name_and_classification():
    result = _build_factor_source_display(
        "Central Electricity Authority CO2 Baseline Database", "platform_default"
    )
    assert result == "Central Electricity Authority CO2 Baseline Database, Platform Default"


def test_build_factor_source_display_classification_only():
    result = _build_factor_source_display("", "platform_default")
    assert result == "Platform Default"


def test_build_factor_source_display_source_name_only():
    result = _build_factor_source_display("Custom Source", "unconfigured")
    assert result == "Custom Source"


def test_build_factor_source_display_both_omit():
    result = _build_factor_source_display("", "unconfigured")
    assert result == ""


def test_footnote_no_dangling_comma_when_source_empty():
    overview = _co2_overview_available()
    overview["factor"]["source"] = ""
    html = _render(_base_payload(co2_overview=overview))
    assert "(, " not in html
    assert "Platform Default" in html


def test_unavailable_notice_uses_organisation_not_tenant():
    html = _render(_base_payload(co2_overview=_co2_overview_unavailable()))
    assert "organisation" in html
    assert "tenant" not in html
