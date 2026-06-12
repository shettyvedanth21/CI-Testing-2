from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "test-internal-service-secret-at-least-32-chars")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")
os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("MINIO_EXTERNAL_URL", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio123")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("REPORTING_SERVICE_URL", "http://localhost:8085")


def _load_module(name, path, service_root):
    paths = [str(REPO), str(REPO / "services"), str(service_root)]
    for p in paths:
        sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for key in list(sys.modules.keys()):
        if key == "src" or key.startswith("src."):
            sys.modules.pop(key, None)
    for p in paths:
        while p in sys.path:
            sys.path.remove(p)
    return mod


rt = _load_module(
    "reporting_report_task",
    REPO / "services" / "reporting-service" / "src" / "tasks" / "report_task.py",
    REPO / "services" / "reporting-service",
)
wt = _load_module(
    "waste_analysis_waste_task",
    REPO / "services" / "waste-analysis-service" / "src" / "tasks" / "waste_task.py",
    REPO / "services" / "waste-analysis-service",
)


def _canonical_accepted(
    energy_kwh=10.0,
    *,
    loss_kwh=None,
    idle_kwh=2.0,
    offhours_kwh=8.0,
    overconsumption_kwh=0.0,
    energy_cost_inr=None,
):
    _loss = loss_kwh if loss_kwh is not None else round(idle_kwh + offhours_kwh + overconsumption_kwh, 6)
    _cost = energy_cost_inr if energy_cost_inr is not None else round(energy_kwh * 6.5, 4)
    return {
        "success": True,
        "totals": {
            "energy_kwh": energy_kwh,
            "energy_cost_inr": _cost,
            "idle_kwh": idle_kwh,
            "offhours_kwh": offhours_kwh,
            "overconsumption_kwh": overconsumption_kwh,
            "loss_kwh": _loss,
        },
        "days": [
            {
                "date": "2026-06-11",
                "energy_kwh": energy_kwh,
                "loss_kwh": _loss,
                "idle_kwh": idle_kwh,
                "offhours_kwh": offhours_kwh,
                "overconsumption_kwh": overconsumption_kwh,
            }
        ],
    }


def _canonical_placeholder_zero():
    return {
        "success": True,
        "totals": {
            "energy_kwh": 0.0,
            "energy_cost_inr": 0.0,
            "idle_kwh": 0.0,
            "offhours_kwh": 0.0,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 0.0,
        },
        "days": [{"date": "2026-06-11", "energy_kwh": 0.0, "loss_kwh": 0.0, "version": 0}],
        "version": 0,
    }


def _energy_result(local_total_kwh=5.0):
    return {"success": True, "data": {"total_kwh": local_total_kwh, "daily_kwh": {}}}


def _waste_result(
    total_energy_kwh=5.0,
    idle_energy_kwh=1.0,
    offhours_energy_kwh=4.0,
    overconsumption_energy_kwh=0.0,
    total_cost=None,
):
    return SimpleNamespace(
        overall_quality="low",
        total_energy_kwh=total_energy_kwh,
        total_cost=total_cost if total_cost is not None else round(total_energy_kwh * 6.5, 2),
        total_loss_kwh=None,
        idle_energy_kwh=idle_energy_kwh,
        offhours_energy_kwh=offhours_energy_kwh,
        overconsumption_energy_kwh=overconsumption_energy_kwh,
        warnings=[],
    )


def test_energy_report_total_kwh_matches_canonical_when_accepted():
    canonical = _canonical_accepted(10.0)
    updated, basis_meta = rt._apply_canonical_energy_overlay(
        _energy_result(5.0),
        canonical,
        local_quality="low",
        local_total_hours=1.0,
        report_window_hours=24.0,
    )
    assert updated["data"]["total_kwh"] == canonical["totals"]["energy_kwh"]
    assert basis_meta["canonical_overlay_applied"] is True


def test_waste_report_total_energy_kwh_matches_canonical_when_financial_accepted():
    canonical = _canonical_accepted(10.0)
    result = _waste_result(5.0)
    financial_applied, financial_reason = wt._apply_canonical_financial_totals(result, canonical, 6.5)
    assert financial_applied is True
    assert result.total_energy_kwh == canonical["totals"]["energy_kwh"]


def test_waste_report_total_loss_kwh_le_total_energy_kwh_under_canonical():
    canonical = _canonical_accepted(10.0, loss_kwh=8.0)
    result = _waste_result(5.0)
    wt._apply_canonical_financial_totals(result, canonical, 6.5)
    assert result.total_loss_kwh <= result.total_energy_kwh
    assert result.total_loss_kwh == 8.0
    assert result.total_energy_kwh == 10.0


def test_waste_report_full_waste_scenario_loss_equals_energy_under_canonical():
    canonical = _canonical_accepted(10.0, loss_kwh=10.0)
    result = _waste_result(5.0)
    wt._apply_canonical_financial_totals(result, canonical, 6.5)
    loss_accepted, loss_reason = wt._should_apply_canonical_loss_overlay(result, canonical)
    assert result.total_loss_kwh == result.total_energy_kwh
    assert loss_accepted is True


def test_both_reports_reflect_same_canonical_energy_truth_when_accepted():
    canonical = _canonical_accepted(10.0, energy_cost_inr=65.0)
    updated, basis_meta = rt._apply_canonical_energy_overlay(
        _energy_result(5.0),
        canonical,
        local_quality="low",
        local_total_hours=1.0,
        report_window_hours=24.0,
    )
    waste = _waste_result(5.0)
    financial_applied, _ = wt._apply_canonical_financial_totals(waste, canonical, 6.5)
    assert updated["data"]["total_kwh"] == waste.total_energy_kwh
    assert basis_meta["canonical_overlay_applied"] is True
    assert financial_applied is True


def test_canonical_rejection_is_explicit_for_placeholder_zero_no_false_parity():
    canonical = _canonical_placeholder_zero()
    updated, basis_meta = rt._apply_canonical_energy_overlay(
        _energy_result(5.0),
        canonical,
        local_quality="low",
        local_total_hours=1.0,
        report_window_hours=24.0,
    )
    waste = _waste_result(5.0)
    financial_applied, _ = wt._apply_canonical_financial_totals(waste, canonical, 6.5)
    assert basis_meta["canonical_overlay_applied"] is False
    assert financial_applied is False
    assert updated["data"]["total_kwh"] == 5.0
    assert waste.total_energy_kwh == 5.0
