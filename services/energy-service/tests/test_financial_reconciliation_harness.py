from __future__ import annotations

from pathlib import Path

import pytest

from reconciliation_harness import compare_financial_surfaces, parse_influx_annotated_csv


def test_financial_reconciliation_harness_detects_report_vs_canonical_drift():
    rows = [
        {"timestamp": "2026-06-03T03:45:00+00:00", "power": 8600.0, "energy_kwh": 100.0, "current": 12.0, "voltage": 245.0, "power_factor": 0.95},
        {"timestamp": "2026-06-03T03:45:30+00:00", "power": 8600.0, "energy_kwh": 100.1, "current": 12.0, "voltage": 245.0, "power_factor": 0.95},
        {"timestamp": "2026-06-03T03:46:00+00:00", "power": 8600.0, "energy_kwh": 100.2, "current": 12.0, "voltage": 245.0, "power_factor": 0.95},
    ]

    comparison = compare_financial_surfaces(rows)

    assert comparison.reporting.energy_kwh == pytest.approx(0.2, abs=1e-6)
    assert comparison.canonical.energy_kwh == pytest.approx(0.2, abs=1e-6)
    assert comparison.drift_kwh == pytest.approx(0.0, abs=1e-6)
    assert comparison.drift_ratio == pytest.approx(0.0, abs=1e-6)


def test_financial_reconciliation_harness_classifies_loss_buckets_with_shift_rules():
    shifts = [
        {"day_of_week": i, "shift_start": "09:30", "shift_end": "18:30", "is_active": True}
        for i in range(7)
    ]
    rows = [
        # Inside shift idle interval.
        {"timestamp": "2026-06-03T04:00:00+00:00", "power": 1000.0, "energy_kwh": 10.0, "current": 1.0, "voltage": 245.0, "power_factor": 0.95},
        {"timestamp": "2026-06-03T04:05:00+00:00", "power": 1000.0, "energy_kwh": 10.083333, "current": 1.0, "voltage": 245.0, "power_factor": 0.95},
        # Outside shift interval.
        {"timestamp": "2026-06-03T13:00:00+00:00", "power": 2000.0, "energy_kwh": 10.2, "current": 8.0, "voltage": 245.0, "power_factor": 0.95},
        {"timestamp": "2026-06-03T13:05:00+00:00", "power": 2000.0, "energy_kwh": 10.366667, "current": 8.0, "voltage": 245.0, "power_factor": 0.95},
    ]

    comparison = compare_financial_surfaces(
        rows,
        shifts=shifts,
        idle_threshold=2.0,
        over_threshold=20.0,
    )

    assert comparison.canonical.idle_kwh == pytest.approx(0.083333, abs=1e-5)
    assert comparison.canonical.offhours_kwh == pytest.approx(0.166667, abs=1e-5)
    assert comparison.canonical.loss_kwh == pytest.approx(0.25, abs=1e-5)


def test_local_ad00000003_june3_csv_documents_current_drift_when_available():
    """Diagnostic snapshot for the downloaded production CSV.

    This intentionally skips in CI when the private downloaded CSV is absent.
    The reporting path now uses canonical interval accounting, so the old
    hard-coded drift expectations are stale.  The test now validates that
    the comparison machinery completes and returns sane numeric values
    without asserting on a specific drift magnitude — that belongs in the
    synthetic parity tests above.
    """

    path = Path("/Users/vedanthshetty/Downloads/AD00000003_2026-06-03.csv")
    if not path.exists():
        pytest.skip("AD00000003 June 3 Influx CSV is not present on this machine")

    rows = parse_influx_annotated_csv(path)
    comparison = compare_financial_surfaces(rows)

    assert len(rows) > 0
    assert comparison.reporting.energy_kwh >= 0.0
    assert comparison.canonical.energy_kwh >= 0.0
    assert isinstance(comparison.drift_kwh, float)
    assert isinstance(comparison.drift_ratio, float)
