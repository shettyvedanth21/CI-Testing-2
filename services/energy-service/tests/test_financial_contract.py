from __future__ import annotations

import sys
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "energy-service"))
sys.path.insert(1, str(ROOT))

from services.shared.telemetry_normalization import (  # noqa: E402
    INTERVAL_ENERGY_ALGORITHM_VERSION,
    NORMALIZATION_VERSION,
)


def _load_financial_contract_module():
    module_name = "_energy_financial_contract_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = ROOT / "services" / "energy-service" / "app" / "services" / "financial_contract.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load financial contract from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


financial_contract = _load_financial_contract_module()
CANONICAL_COST_FORMULA_VERSION = financial_contract.CANONICAL_COST_FORMULA_VERSION
CANONICAL_FINANCIAL_CONTRACT_VERSION = financial_contract.CANONICAL_FINANCIAL_CONTRACT_VERSION
CANONICAL_SOURCE = financial_contract.CANONICAL_SOURCE
TariffSnapshot = financial_contract.TariffSnapshot
build_canonical_financial_contract = financial_contract.build_canonical_financial_contract


def test_contract_derives_money_from_tariff_snapshot_with_decimal_precision():
    contract = build_canonical_financial_contract(
        tenant_id="SH00000002",
        device_id="AD00000003",
        period_type="device_day",
        period_start=date(2026, 6, 3),
        period_end=date(2026, 6, 3),
        energy_kwh="52.163",
        idle_kwh="7.401517",
        offhours_kwh="14.472718",
        overconsumption_kwh="0",
        tariff={"rate": "6.5", "currency": "INR", "source": "tenant_tariffs", "version_id": 2},
        quality_flags=["counter_accepted"],
    )

    ok, issues = contract.validate()
    payload = contract.to_dict()

    assert ok is True
    assert issues == ()
    assert payload["contract_version"] == CANONICAL_FINANCIAL_CONTRACT_VERSION
    assert payload["source"] == CANONICAL_SOURCE
    assert payload["algorithm_version"] == INTERVAL_ENERGY_ALGORITHM_VERSION
    assert payload["normalization_version"] == NORMALIZATION_VERSION
    assert payload["cost_formula_version"] == CANONICAL_COST_FORMULA_VERSION
    assert payload["totals"]["energy_cost"] == pytest.approx(339.0595)
    assert payload["totals"]["loss_kwh"] == pytest.approx(21.874235)
    assert payload["totals"]["loss_cost"] == pytest.approx(142.1825)
    assert payload["totals"]["cost_source"] == "tariff_snapshot_derived"
    assert payload["tariff"]["version_id"] == 2
    assert payload["quality_flags"] == ["counter_accepted"]


def test_contract_prefers_persisted_costs_so_historical_reports_are_not_repriced():
    contract = build_canonical_financial_contract(
        tenant_id="SH00000002",
        device_id="AD00000003",
        period_type="device_day",
        period_start=date(2026, 6, 3),
        period_end=date(2026, 6, 3),
        energy_kwh=Decimal("52.163"),
        idle_kwh=Decimal("7.401517"),
        offhours_kwh=Decimal("14.472718"),
        persisted_energy_cost=Decimal("341.75"),
        persisted_loss_cost=Decimal("142.18"),
        tariff=TariffSnapshot(rate_per_kwh=Decimal("9.99"), currency="INR", source="newer_tariff"),
    )

    payload = contract.to_dict()

    assert payload["valid"] is True
    assert payload["totals"]["energy_cost"] == pytest.approx(341.75)
    assert payload["totals"]["loss_cost"] == pytest.approx(142.18)
    assert payload["totals"]["cost_source"] == "persisted_aggregate"
    assert payload["tariff"]["rate_per_kwh"] == pytest.approx(9.99)


def test_contract_marks_cost_unavailable_instead_of_guessing_without_tariff():
    contract = build_canonical_financial_contract(
        tenant_id="SH00000002",
        device_id="AD00000003",
        period_type="device_day",
        period_start=date(2026, 6, 3),
        period_end=date(2026, 6, 3),
        energy_kwh=52.163,
        idle_kwh=7.401517,
        offhours_kwh=14.472718,
        tariff=None,
    )

    payload = contract.to_dict()

    assert payload["valid"] is True
    assert payload["totals"]["energy_cost"] is None
    assert payload["totals"]["loss_cost"] is None
    assert payload["totals"]["cost_source"] == "unavailable"
    assert "tariff_snapshot_unavailable" in payload["warnings"]


def test_contract_validation_rejects_money_unsafe_loss_mismatch():
    contract = build_canonical_financial_contract(
        tenant_id="SH00000002",
        device_id="AD00000003",
        period_type="device_day",
        period_start=date(2026, 6, 3),
        period_end=date(2026, 6, 3),
        energy_kwh=10,
        idle_kwh=2,
        offhours_kwh=3,
        overconsumption_kwh=1,
        loss_kwh=4,
        tariff={"rate": 6.5},
    )

    ok, issues = contract.validate()

    assert ok is False
    assert "loss_bucket_sum_mismatch" in issues


def test_contract_validation_rejects_loss_greater_than_energy():
    contract = build_canonical_financial_contract(
        tenant_id="SH00000002",
        device_id="AD00000003",
        period_type="device_day",
        period_start=date(2026, 6, 3),
        period_end=date(2026, 6, 3),
        energy_kwh=5,
        idle_kwh=3,
        offhours_kwh=3,
        overconsumption_kwh=0,
        tariff={"rate": 6.5},
    )

    ok, issues = contract.validate()

    assert ok is False
    assert "loss_exceeds_energy" in issues
