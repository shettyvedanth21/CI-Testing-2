from __future__ import annotations

import os
import sys
from types import SimpleNamespace

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


def _load_helper():
    sys.modules.pop("src.config", None)
    sys.modules.pop("src.database", None)
    sys.modules.pop("src.tasks", None)
    sys.modules.pop("src.tasks.waste_task", None)
    from src.tasks.waste_task import (
        _build_waste_coverage_result,
        _build_device_summary,
        _public_warnings,
        _sync_canonical_overlay_warnings,
    )

    return _build_device_summary, _public_warnings, _sync_canonical_overlay_warnings, _build_waste_coverage_result


def test_build_device_summary_exposes_flat_fla_metadata_and_overconsumption_alias():
    build_device_summary, _, _, _ = _load_helper()
    result = SimpleNamespace(
        device_id="D1",
        device_name="Device 1",
        data_source_type="metered",
        idle_duration_sec=120,
        idle_energy_kwh=0.22,
        idle_cost=0.0,
        standby_power_kw=0.1,
        standby_energy_kwh=0.22,
        standby_cost=0.0,
        total_energy_kwh=0.62,
        total_cost=0.0,
        offhours_energy_kwh=0.0,
        offhours_cost=0.0,
        offhours_duration_sec=0,
        offhours_skipped_reason=None,
        offhours_pf_estimated=False,
        overconsumption_duration_sec=60,
        overconsumption_energy_kwh=0.04,
        overconsumption_cost=0.0,
        overconsumption_skipped_reason=None,
        overconsumption_pf_estimated=False,
        overconsumption_config_source="device_service_derived",
        overconsumption_config_used={
            "full_load_current_a": 20.0,
            "idle_threshold_pct_of_fla": 0.25,
            "derived_idle_threshold_a": 5.0,
            "derived_overconsumption_threshold_a": 20.0,
        },
        unoccupied_duration_sec=None,
        unoccupied_energy_kwh=None,
        unoccupied_cost=None,
        unoccupied_skipped_reason="Disabled by policy",
        unoccupied_pf_estimated=False,
        unoccupied_config_source=None,
        unoccupied_config_used=None,
        data_quality="high",
        energy_quality="high",
        idle_quality="high",
        standby_quality="high",
        overall_quality="high",
        idle_status="configured",
        power_unit_input="kW",
        power_unit_normalized_to="kW",
        normalization_applied=False,
        pf_estimated=False,
        warnings=["canonical_energy_projection_applied"],
        calculation_method="interval_power",
    )

    payload = build_device_summary(result, tariff_rate=0.0)

    assert payload["full_load_current_a"] == 20.0
    assert payload["idle_threshold_pct_of_fla"] == 0.25
    assert payload["derived_idle_threshold_a"] == 5.0
    assert payload["derived_overconsumption_threshold_a"] == 20.0
    assert payload["overconsumption_kwh"] == 0.04
    assert payload["overconsumption_energy_kwh"] == 0.04
    assert payload["overconsumption"]["config_used"]["derived_overconsumption_threshold_a"] == 20.0
    assert "standby_power_kw" not in payload
    assert "standby_energy_kwh" not in payload
    assert "standby_cost" not in payload
    assert "data_quality" not in payload
    assert "energy_quality" not in payload
    assert "idle_quality" not in payload
    assert "standby_quality" not in payload
    assert "overall_quality" not in payload
    assert payload["warnings"] == []


def test_public_warnings_filters_internal_and_non_actionable_noise():
    _, public_warnings, _, _ = _load_helper()

    warnings = [
        "canonical_energy_projection_applied",
        "POWER_UNIT_ASSUMED_WATTS: normalized power/active_power to kW",
        "OVERCONSUMPTION: No overconsumption detected in this period",
        "FLA_NOT_CONFIGURED: full load current is required for idle waste calculation",
    ]

    assert public_warnings(warnings) == [
        "FLA_NOT_CONFIGURED: full load current is required for idle waste calculation",
    ]


def test_sync_canonical_overlay_warnings_removes_stale_zero_bucket_messages():
    _, _, sync_warnings, _ = _load_helper()
    result = SimpleNamespace(
        offhours_energy_kwh=0.0,
        overconsumption_energy_kwh=0.04,
        warnings=[
            "OFF_HOURS: No off-hours consumption detected",
            "OVERCONSUMPTION: No overconsumption detected in this period",
            "canonical_energy_projection_applied",
        ],
    )

    sync_warnings(result)

    assert "OVERCONSUMPTION: No overconsumption detected in this period" not in result.warnings
    assert "OFF_HOURS: No off-hours consumption detected" in result.warnings


def test_waste_coverage_contract_marks_quality_gate_payload_business_blocked():
    _, _, _, build_waste_coverage_result = _load_helper()
    result = SimpleNamespace(device_id="D1", overall_quality="insufficient")

    coverage = build_waste_coverage_result(
        devices=[{"device_id": "D1"}],
        results=[result],
        quality_failures=[{"device_id": "D1", "code": "INSUFFICIENT_DATA", "message": "Device quality is insufficient"}],
        warnings=["Device quality is insufficient"],
        artifact_generation_allowed=False,
    )

    assert coverage["level"] == "insufficient_coverage"
    assert coverage["usable_for_business_decisions"] is False
    assert coverage["artifact_generation_allowed"] is False
