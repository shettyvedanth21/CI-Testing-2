from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BASE_DIR = Path(__file__).resolve().parents[1]


def _ensure_app_stubs() -> None:
    if "app" not in sys.modules:
        _app = types.ModuleType("app")
        _app.__path__ = [str(_BASE_DIR / "app")]
        _app.__package__ = "app"
        _app.__file__ = str(_BASE_DIR / "app" / "__init__.py")
        sys.modules["app"] = _app

    if "app.services" not in sys.modules:
        _svc = types.ModuleType("app.services")
        _svc.__path__ = [str(_BASE_DIR / "app" / "services")]
        _svc.__package__ = "app.services"
        _svc.__file__ = str(_BASE_DIR / "app" / "services" / "__init__.py")
        sys.modules["app.services"] = _svc
        sys.modules["app"].services = _svc


_ensure_app_stubs()

from app.services.anomaly.detector import detect_anomalies, merge_events
from app.services.anomaly.types import AnomalyFieldBaseline, AnomalyCandidate


def _make_baseline(field_name, mean=10.0, std=1.0, median=10.0, mad=1.0,
                   quality_score=0.9, quality_band="high", status="active",
                   baseline_version=1):
    return AnomalyFieldBaseline(
        field_name=field_name,
        baseline_mean=mean,
        baseline_std=std,
        baseline_median=median,
        baseline_mad=mad,
        quality_score=quality_score,
        quality_band=quality_band,
        status=status,
        baseline_version=baseline_version,
    )


def _make_feature(current_avg_mean=None, power_mean=None, power_factor_mean=None,
                  voltage_avg_mean=None, phase_imbalance=None):
    class _Input:
        pass
    inp = _Input()
    inp.current_avg_mean = current_avg_mean
    inp.power_mean = power_mean
    inp.power_factor_mean = power_factor_mean
    inp.voltage_avg_mean = voltage_avg_mean
    inp.phase_imbalance = phase_imbalance
    return inp


def test_z_score_computation_mild():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=12.5)
    prior = [AnomalyCandidate(signal_field="current_avg", severity="mild", merged_window_count=1)]
    results = detect_anomalies([bl], fw, prior_events=prior)
    assert len(results) >= 1
    r = results[0]
    assert r.signal_field == "current_avg"
    assert abs(r.z_score - 2.5) < 0.01


def test_z_score_severe():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=15.0)
    results = detect_anomalies([bl], fw, prior_events=[
        AnomalyCandidate(signal_field="current_avg", severity="severe", merged_window_count=1)
    ])
    assert len(results) >= 1
    assert results[0].severity == "severe"


def test_modified_z_score_fallback():
    bl = _make_baseline("current_avg", mean=10.0, std=0.0001, median=10.0, mad=1.0)
    fw = _make_feature(current_avg_mean=14.0)
    results = detect_anomalies([bl], fw, prior_events=[
        AnomalyCandidate(signal_field="current_avg", severity="strong", merged_window_count=1)
    ])
    assert len(results) >= 1
    assert results[0].z_score is not None


def test_threshold_boundary_at_2():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=12.0)
    prior = [AnomalyCandidate(signal_field="current_avg", severity="mild", merged_window_count=1)]
    results = detect_anomalies([bl], fw, prior_events=prior)
    assert len(results) >= 1
    assert results[0].severity == "mild"


def test_threshold_boundary_at_3():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=13.0)
    results = detect_anomalies([bl], fw, prior_events=[
        AnomalyCandidate(signal_field="current_avg", severity="strong", merged_window_count=1)
    ])
    assert len(results) >= 1
    assert results[0].severity == "strong"


def test_threshold_boundary_at_4():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=14.0)
    results = detect_anomalies([bl], fw)
    assert len(results) >= 1
    assert results[0].severity == "severe"


def test_current_two_tailed_increase():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=13.5)
    results = detect_anomalies([bl], fw, prior_events=[
        AnomalyCandidate(signal_field="current_avg", severity="strong", merged_window_count=1)
    ])
    assert len(results) >= 1
    assert results[0].severity == "strong"


def test_current_two_tailed_decrease():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=6.5)
    results = detect_anomalies([bl], fw, prior_events=[
        AnomalyCandidate(signal_field="current_avg", severity="strong", merged_window_count=1)
    ])
    assert len(results) >= 1
    assert results[0].severity == "strong"


def test_power_factor_decrease_only():
    bl = _make_baseline("power_factor", mean=0.9, std=0.05)
    fw = _make_feature(power_factor_mean=0.775)
    prior = [AnomalyCandidate(signal_field="power_factor", severity="mild", merged_window_count=1)]
    results = detect_anomalies([bl], fw, prior_events=prior)
    assert len(results) >= 1
    assert results[0].severity == "mild"


def test_power_factor_increase_not_detected():
    bl = _make_baseline("power_factor", mean=0.9, std=0.05)
    fw = _make_feature(power_factor_mean=0.99)
    results = detect_anomalies([bl], fw)
    assert len(results) == 0


def test_phase_imbalance_increase_only():
    bl = _make_baseline("phase_imbalance", mean=0.02, std=0.01)
    fw = _make_feature(phase_imbalance=0.06)
    results = detect_anomalies([bl], fw, prior_events=[
        AnomalyCandidate(signal_field="phase_imbalance", severity="strong", merged_window_count=1)
    ])
    assert len(results) >= 1
    assert results[0].severity == "strong"


def test_phase_imbalance_decrease_not_detected():
    bl = _make_baseline("phase_imbalance", mean=0.05, std=0.01)
    fw = _make_feature(phase_imbalance=0.01)
    results = detect_anomalies([bl], fw)
    assert len(results) == 0


def test_missing_data_skip():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=None)
    results = detect_anomalies([bl], fw)
    assert len(results) == 0


def test_no_baselines():
    fw = _make_feature(current_avg_mean=15.0)
    results = detect_anomalies([], fw)
    assert len(results) == 0


def test_candidate_baseline_inactive_skipped():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0, status="candidate")
    fw = _make_feature(current_avg_mean=15.0)
    results = detect_anomalies([bl], fw)
    assert len(results) == 0


def test_confidence_with_high_quality():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0, quality_score=0.9, quality_band="high")
    fw = _make_feature(current_avg_mean=14.0)
    results = detect_anomalies([bl], fw)
    assert len(results) >= 1
    assert results[0].confidence > 0.0


def test_confidence_with_low_quality():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0, quality_score=0.35, quality_band="low")
    fw = _make_feature(current_avg_mean=14.0)
    results = detect_anomalies([bl], fw)
    assert len(results) >= 1
    assert results[0].confidence < 0.5


def test_cross_field_boost():
    bl_curr = _make_baseline("current_avg", mean=10.0, std=1.0)
    bl_pf = _make_baseline("power_factor", mean=0.9, std=0.05)
    fw = _make_feature(current_avg_mean=14.0, power_factor_mean=0.78)
    results = detect_anomalies([bl_curr, bl_pf], fw, prior_events=[
        AnomalyCandidate(signal_field="current_avg", severity="severe", merged_window_count=1),
        AnomalyCandidate(signal_field="power_factor", severity="severe", merged_window_count=1),
    ])
    assert len(results) >= 2
    for r in results:
        if r.signal_field == "current_avg":
            assert "power_factor" in r.correlated_signals


def test_startup_adjacent_flag():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    bl2 = _make_baseline("power", mean=500.0, std=50.0)
    fw = _make_feature(current_avg_mean=14.0, power_mean=650.0)
    results = detect_anomalies([bl, bl2], fw, running_state="STARTUP")
    assert len(results) >= 1
    assert any(r.startup_adjacent is True for r in results)


def test_mode_change_flag():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=14.0)
    results = detect_anomalies([bl], fw, running_state="LOAD_CHANGE")
    assert len(results) >= 1
    assert results[0].mode_change is True


def test_supply_related_flag():
    bl_v = _make_baseline("voltage_avg", mean=230.0, std=5.0)
    fw = _make_feature(voltage_avg_mean=245.0)
    results = detect_anomalies([bl_v], fw, prior_events=[
        AnomalyCandidate(signal_field="voltage_avg", severity="severe", merged_window_count=1)
    ])
    assert len(results) >= 1
    assert results[0].supply_related is True


def test_supply_related_false_when_current_also_anomalous():
    bl_v = _make_baseline("voltage_avg", mean=230.0, std=5.0)
    bl_c = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(voltage_avg_mean=245.0, current_avg_mean=15.0)
    results = detect_anomalies([bl_v, bl_c], fw, prior_events=[
        AnomalyCandidate(signal_field="voltage_avg", severity="severe", merged_window_count=1),
        AnomalyCandidate(signal_field="current_avg", severity="severe", merged_window_count=1),
    ])
    voltage_event = [r for r in results if r.signal_field == "voltage_avg"]
    assert len(voltage_event) >= 1
    assert voltage_event[0].supply_related is False


def test_recurring_flag():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=14.0)
    prior = [AnomalyCandidate(signal_field="current_avg", severity="severe")]
    results = detect_anomalies([bl], fw, prior_events=prior)
    assert len(results) >= 1
    assert results[0].recurring is True


def test_mild_requires_confirmation():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=12.5)
    results = detect_anomalies([bl], fw)
    assert len(results) == 0


def test_mild_confirmed_with_prior_event():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=12.5)
    prior = [AnomalyCandidate(signal_field="current_avg", severity="mild", merged_window_count=1)]
    results = detect_anomalies([bl], fw, prior_events=prior)
    assert len(results) >= 1
    assert results[0].severity == "mild"


def test_merge_events_extends_matching():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        z_score=3.2, signal_value=13.2,
        z_score_history=(3.0, 3.2),
    )
    new_cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=3,
        z_score=3.5, signal_value=13.5,
        z_score_history=(3.0, 3.2, 3.5),
    )
    extended, new = merge_events(
        [new_cand],
        [prior],
        current_window_start=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
        current_window_end=datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc),
    )
    assert len(extended) == 1
    assert len(new) == 0
    assert extended[0].merged_window_count == 3
    assert extended[0].occurred_at == prior.occurred_at
    assert extended[0].ended_at == datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc)
    assert extended[0].duration_seconds == 14400
    assert extended[0].z_score_history == (3.0, 3.2, 3.5)


def test_merge_events_new_when_no_match():
    new_cand = AnomalyCandidate(
        signal_field="power", severity="mild", merged_window_count=1,
        z_score=2.2,
    )
    extended, new = merge_events([new_cand], [])
    assert len(extended) == 0
    assert len(new) == 1
    assert new[0].signal_field == "power"


def test_merge_events_new_when_severity_differs():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="mild", merged_window_count=1,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    new_cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.5,
    )
    extended, new = merge_events([new_cand], [prior])
    assert len(new) == 1
    assert new[0].severity == "strong"


def test_merge_cap_at_6_windows():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=6,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    new_cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=1,
        z_score=3.5,
    )
    extended, new = merge_events([new_cand], [prior])
    assert len(extended) == 0
    assert len(new) == 1


def test_merge_upgrades_anomaly_type_to_persistent():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        anomaly_type="deviation",
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    new_cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=1,
        z_score=3.5,
    )
    extended, _ = merge_events([new_cand], [prior])
    assert extended[0].anomaly_type == "persistent"


def test_no_detection_below_threshold():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    fw = _make_feature(current_avg_mean=11.5)
    results = detect_anomalies([bl], fw)
    assert len(results) == 0


def test_trend_classification_monotonic_drift():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.2, signal_value=13.2,
        z_score_history=(3.0, 3.2),
    )
    fw = _make_feature(current_avg_mean=13.5)
    results = detect_anomalies([bl], fw, prior_events=[prior])
    assert len(results) >= 1
    assert results[0].anomaly_type == "trend"
    assert results[0].z_score_history == (3.0, 3.2, 3.5)


def test_trend_not_detected_without_monotonic_drift():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.5, signal_value=13.5,
        z_score_history=(3.5, 3.2),
    )
    fw = _make_feature(current_avg_mean=13.2)
    results = detect_anomalies([bl], fw, prior_events=[prior])
    assert len(results) >= 1
    assert results[0].anomaly_type != "trend"


def test_trend_not_detected_below_3_windows():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=1,
        z_score=3.0, signal_value=13.0,
        z_score_history=(3.0,),
    )
    fw = _make_feature(current_avg_mean=13.2)
    results = detect_anomalies([bl], fw, prior_events=[prior])
    assert len(results) >= 1
    assert results[0].anomaly_type != "trend"


def test_trend_decrease_direction_power_factor():
    bl = _make_baseline("power_factor", mean=0.9, std=0.05)
    prior = AnomalyCandidate(
        signal_field="power_factor", severity="strong", merged_window_count=2,
        z_score=-3.2, signal_value=0.74,
        z_score_history=(-3.0, -3.2),
    )
    fw = _make_feature(power_factor_mean=0.735)
    results = detect_anomalies([bl], fw, prior_events=[prior])
    assert len(results) >= 1
    assert results[0].anomaly_type == "trend"


def test_trend_increase_direction_phase_imbalance():
    bl = _make_baseline("phase_imbalance", mean=0.02, std=0.01)
    prior = AnomalyCandidate(
        signal_field="phase_imbalance", severity="strong", merged_window_count=2,
        z_score=3.2, signal_value=0.052,
        z_score_history=(3.0, 3.2),
    )
    fw = _make_feature(phase_imbalance=0.055)
    results = detect_anomalies([bl], fw, prior_events=[prior])
    assert len(results) >= 1
    assert results[0].anomaly_type == "trend"


def test_trend_two_tailed_mixed_sign_not_trend():
    bl = _make_baseline("current_avg", mean=10.0, std=1.0)
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.2, signal_value=13.2,
        z_score_history=(-3.5, 3.2),
    )
    fw = _make_feature(current_avg_mean=13.5)
    results = detect_anomalies([bl], fw, prior_events=[prior])
    assert len(results) >= 1
    assert results[0].anomaly_type != "trend"


def test_merge_duration_seconds_computed():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="severe", merged_window_count=1,
        z_score=4.0,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
        z_score_history=(4.0,),
    )
    cand = AnomalyCandidate(
        signal_field="current_avg", severity="severe", merged_window_count=2,
        z_score=4.2, signal_value=14.2,
        z_score_history=(4.0, 4.2),
    )
    extended, _ = merge_events(
        [cand], [prior],
        current_window_start=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        current_window_end=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
    )
    assert len(extended) == 1
    assert extended[0].occurred_at == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert extended[0].ended_at == datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
    assert extended[0].duration_seconds == 10800


def test_merge_keeps_strongest_z_score_and_signal_value():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.8, signal_value=13.8,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        z_score_history=(3.5, 3.8),
    )
    cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=3,
        z_score=3.2, signal_value=13.2,
        z_score_history=(3.5, 3.8, 3.2),
    )
    extended, _ = merge_events([cand], [prior])
    assert extended[0].z_score == 3.8
    assert extended[0].signal_value == 13.8


def test_merge_updates_strongest_z_score_when_new_is_stronger():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.2, signal_value=13.2,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        z_score_history=(3.0, 3.2),
    )
    cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=3,
        z_score=3.8, signal_value=13.8,
        z_score_history=(3.0, 3.2, 3.8),
    )
    extended, _ = merge_events([cand], [prior])
    assert extended[0].z_score == 3.8
    assert extended[0].signal_value == 13.8


def test_merge_gap_starts_new_event():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.5, signal_value=13.5,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        z_score_history=(3.0, 3.5),
    )
    cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=3,
        z_score=3.6, signal_value=13.6,
        z_score_history=(3.0, 3.5, 3.6),
    )
    extended, new = merge_events(
        [cand], [prior],
        current_window_start=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        current_window_end=datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc),
        gap_threshold=1,
    )
    assert len(extended) == 0
    assert len(new) == 1
    assert new[0].merged_window_count == 1


def test_merge_no_gap_when_consecutive():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.5, signal_value=13.5,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        z_score_history=(3.0, 3.5),
    )
    cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=3,
        z_score=3.6, signal_value=13.6,
        z_score_history=(3.0, 3.5, 3.6),
    )
    extended, new = merge_events(
        [cand], [prior],
        current_window_start=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
        current_window_end=datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc),
        gap_threshold=1,
    )
    assert len(extended) == 1
    assert len(new) == 0


def test_merge_new_event_duration_seconds():
    cand = AnomalyCandidate(
        signal_field="power", severity="mild", merged_window_count=1,
        z_score=2.2, signal_value=550.0,
        z_score_history=(2.2,),
    )
    _, new = merge_events(
        [cand], [],
        current_window_start=datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc),
        current_window_end=datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc),
    )
    assert len(new) == 1
    assert new[0].occurred_at == datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)
    assert new[0].ended_at == datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)
    assert new[0].duration_seconds == 3600


def test_merge_z_score_history_propagated_on_extend():
    prior = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=2,
        z_score=3.2,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        z_score_history=(3.0, 3.2),
    )
    cand = AnomalyCandidate(
        signal_field="current_avg", severity="strong", merged_window_count=3,
        z_score=3.5,
        z_score_history=(3.0, 3.2, 3.5),
    )
    extended, _ = merge_events([cand], [prior])
    assert extended[0].z_score_history == (3.0, 3.2, 3.5)


def test_merge_z_score_history_on_new_event():
    cand = AnomalyCandidate(
        signal_field="power", severity="mild", merged_window_count=1,
        z_score=2.2,
        z_score_history=(2.2,),
    )
    _, new = merge_events([cand], [])
    assert len(new) == 1
    assert new[0].z_score_history == (2.2,)
