"""Tests for the pure degradation scorer.

Uses app-package stubs so the test runs from repo root without requiring
the full device-service runtime (DATABASE_URL, shared modules, etc.).
"""

from __future__ import annotations

import math
import sys
import types
from datetime import datetime, timezone, timedelta
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

from app.services.degradation.scorer import compute_degradation_score
from app.services.degradation.types import (
    BaselineInput,
    Contribution,
    FeatureWindowInput,
    PriorScoreEntry,
    ScoreResult,
)


def _healthy_baseline(**overrides) -> BaselineInput:
    defaults = dict(
        current_avg_mean=10.0,
        current_avg_std=0.5,
        power_mean=5000.0,
        power_p95=5500.0,
        power_factor_mean=0.95,
        voltage_avg_mean=400.0,
        phase_imbalance_mean=0.02,
        frequency_mean=50.0,
        quality_score=0.9,
    )
    defaults.update(overrides)
    return BaselineInput(**defaults)


def _healthy_window(**overrides) -> FeatureWindowInput:
    defaults = dict(
        current_avg_mean=10.0,
        current_avg_std=0.5,
        power_mean=5000.0,
        power_p95=5500.0,
        power_factor_mean=0.95,
        phase_imbalance=0.02,
    )
    defaults.update(overrides)
    return FeatureWindowInput(**defaults)


class TestHappyPath:
    def test_healthy_score_near_minimum(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window() for _ in range(3)]
        result = compute_degradation_score(baseline, windows)

        assert result.score is not None
        assert 1.0 <= result.score <= 10.0
        assert result.status == "healthy"
        assert result.confidence > 0.0
        assert len(result.contributions) == 5

    def test_slight_deviation_gives_watch_status(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window(
            current_avg_std=0.8,
            power_factor_mean=0.88,
            phase_imbalance=0.06,
        )]
        result = compute_degradation_score(baseline, windows)

        assert result.score is not None
        assert result.score > 1.0
        assert result.status in ("healthy", "watch", "warning", "critical", "learning")


class TestScoreClamping:
    def test_score_clamped_to_max_on_extreme_drift(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window(
            current_avg_std=5.0,
            power_factor_mean=0.0,
            power_mean=20000.0,
            phase_imbalance=0.5,
        )]
        now = datetime.now(timezone.utc)
        result = compute_degradation_score(
            baseline, windows,
            prior_scores=[
                PriorScoreEntry(score=1.0, computed_at=now - timedelta(hours=2)),
                PriorScoreEntry(score=5.0, computed_at=now - timedelta(hours=1)),
                PriorScoreEntry(score=9.0, computed_at=now),
            ],
        )

        assert result.score == 10.0

    def test_score_at_minimum_when_matching_baseline(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.score == pytest.approx(1.0)

    def test_score_never_below_one(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window(
            current_avg_std=0.3,
            power_factor_mean=0.99,
            power_mean=4900.0,
            phase_imbalance=0.01,
        )]
        result = compute_degradation_score(baseline, windows)

        assert result.score >= 1.0


class TestConfidenceClamping:
    def test_confidence_never_below_zero(self):
        baseline = BaselineInput(quality_score=0.0)
        windows = [FeatureWindowInput()]
        result = compute_degradation_score(baseline, windows)

        assert result.confidence >= 0.0

    def test_confidence_never_above_one(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.confidence <= 1.0


class TestMissingSignals:
    def test_all_signals_missing_gives_insufficient_signals(self):
        baseline = BaselineInput(quality_score=1.0)
        windows = [FeatureWindowInput()]
        result = compute_degradation_score(baseline, windows)

        assert result.status == "insufficient_signals"
        assert result.score is None
        assert result.confidence == 0.0

    def test_missing_pf_withholds_score(self):
        baseline = BaselineInput(current_avg_std=0.5, power_mean=5000.0, quality_score=1.0)
        windows = [_healthy_window(current_avg_std=0.6)]
        result = compute_degradation_score(baseline, windows)

        assert result.status == "insufficient_signals"
        assert result.score is None

    def test_missing_current_variability_withholds_score(self):
        baseline = BaselineInput(power_factor_mean=0.95, power_mean=5000.0, quality_score=1.0)
        windows = [_healthy_window(power_factor_mean=0.90)]
        result = compute_degradation_score(baseline, windows)

        assert result.status == "insufficient_signals"
        assert result.score is None


class TestRequiredSignalPolicy:
    def test_both_required_signals_present_allows_score(self):
        baseline = BaselineInput(
            current_avg_std=0.5,
            power_factor_mean=0.95,
            power_mean=5000.0,
            quality_score=1.0,
        )
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.status != "insufficient_signals"
        assert result.score is not None

    def test_pf_and_current_present_phase_missing_still_scores(self):
        baseline = BaselineInput(
            current_avg_std=0.5,
            power_factor_mean=0.95,
            power_mean=5000.0,
            quality_score=1.0,
        )
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.score is not None
        assert result.status != "insufficient_signals"
        assert result.confidence < 1.0

    def test_low_signal_completeness_withholds_score(self):
        baseline = BaselineInput(
            current_avg_std=0.5,
            quality_score=1.0,
        )
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.status == "insufficient_signals"
        assert result.score is None

    def test_insufficient_signals_explains_which_missing(self):
        baseline = BaselineInput(power_mean=5000.0, quality_score=1.0)
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.status == "insufficient_signals"
        assert any("insufficient_signal_coverage" in r for r in result.top_reasons)

    def test_zero_drift_signals_are_available(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.status == "healthy"
        for c in result.contributions:
            if c.signal in ("current_variability_drift", "power_factor_drop",
                            "abnormal_power_draw", "phase_imbalance_drift"):
                assert c.available is True
                assert c.drift == 0.0

    def test_missing_signal_has_available_false(self):
        baseline = BaselineInput(
            current_avg_std=0.5,
            power_factor_mean=0.95,
            power_mean=5000.0,
            quality_score=1.0,
        )
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        phase_c = [c for c in result.contributions if c.signal == "phase_imbalance_drift"][0]
        assert phase_c.available is False
        assert phase_c.drift == 0.0


class TestWeakBaseline:
    def test_weak_baseline_caps_confidence(self):
        baseline = BaselineInput(
            current_avg_std=0.5,
            power_factor_mean=0.95,
            power_mean=5000.0,
            phase_imbalance_mean=0.02,
            quality_score=0.2,
        )
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.confidence <= 0.2

    def test_weak_baseline_returns_learning_status(self):
        baseline = _healthy_baseline(quality_score=0.2)
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.status == "learning"


class TestEmptyWindows:
    def test_empty_windows_returns_unavailable(self):
        baseline = _healthy_baseline()
        result = compute_degradation_score(baseline, [])

        assert result.score is None
        assert result.status == "unavailable"
        assert result.confidence == 0.0
        assert "insufficient_data" in result.top_reasons

    def test_unavailable_has_no_contributions(self):
        baseline = _healthy_baseline()
        result = compute_degradation_score(baseline, [])

        assert result.contributions == ()


class TestTrendWorsening:
    def test_trend_worsening_raises_contribution(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window(current_avg_std=0.8)]
        now = datetime.now(timezone.utc)

        result_stable = compute_degradation_score(
            baseline, windows,
            prior_scores=[
                PriorScoreEntry(score=2.0, computed_at=now - timedelta(hours=2)),
                PriorScoreEntry(score=2.1, computed_at=now - timedelta(hours=1)),
            ],
        )
        result_worsening = compute_degradation_score(
            baseline, windows,
            prior_scores=[
                PriorScoreEntry(score=2.0, computed_at=now - timedelta(hours=2)),
                PriorScoreEntry(score=5.0, computed_at=now - timedelta(hours=1)),
            ],
        )

        stable_trend = [c for c in result_stable.contributions if c.signal == "trend_worsening"][0]
        worsening_trend = [c for c in result_worsening.contributions if c.signal == "trend_worsening"][0]

        assert worsening_trend.drift > stable_trend.drift

    def test_improving_trend_gives_zero_trend_contribution(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window()]
        now = datetime.now(timezone.utc)

        result = compute_degradation_score(
            baseline, windows,
            prior_scores=[
                PriorScoreEntry(score=5.0, computed_at=now - timedelta(hours=2)),
                PriorScoreEntry(score=2.0, computed_at=now - timedelta(hours=1)),
            ],
        )

        trend = [c for c in result.contributions if c.signal == "trend_worsening"][0]
        assert trend.drift == 0.0

    def test_no_prior_scores_means_no_trend(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        trend = [c for c in result.contributions if c.signal == "trend_worsening"][0]
        assert trend.drift == 0.0


class TestNoNaNorInfinity:
    def test_zero_baseline_std_with_zero_recent(self):
        baseline = BaselineInput(
            current_avg_std=0.0,
            power_factor_mean=0.0,
            power_mean=0.0,
            phase_imbalance_mean=0.0,
            quality_score=1.0,
        )
        windows = [FeatureWindowInput(
            current_avg_std=0.0,
            power_factor_mean=0.0,
            power_mean=0.0,
            phase_imbalance=0.0,
        )]
        result = compute_degradation_score(baseline, windows)

        assert result.score is not None
        assert math.isfinite(result.score)
        assert math.isfinite(result.confidence)

    def test_zero_baseline_std_with_nonzero_recent(self):
        baseline = BaselineInput(
            current_avg_std=0.0,
            power_factor_mean=0.95,
            phase_imbalance_mean=0.0,
            quality_score=1.0,
        )
        windows = [FeatureWindowInput(
            current_avg_std=1.0,
            power_factor_mean=0.90,
            phase_imbalance=0.5,
        )]
        result = compute_degradation_score(baseline, windows)

        assert result.score is not None
        assert math.isfinite(result.score)
        assert math.isfinite(result.confidence)

    def test_nan_input_treated_as_missing(self):
        baseline = _healthy_baseline(current_avg_std=float("nan"))
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.status == "insufficient_signals"
        assert result.score is None
        assert math.isfinite(result.confidence)

    def test_infinity_input_treated_as_missing(self):
        baseline = _healthy_baseline(power_mean=float("inf"))
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.score is not None
        assert math.isfinite(result.score)
        assert math.isfinite(result.confidence)


class TestStatusBands:
    def test_critical_band(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window(
            current_avg_std=5.0,
            power_factor_mean=0.1,
            power_mean=20000.0,
            phase_imbalance=0.5,
        )]
        result = compute_degradation_score(baseline, windows)
        assert result.status == "critical"

    def test_healthy_band(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)
        assert result.status == "healthy"


class TestTopReasons:
    def test_top_reasons_populated_when_drift_present(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window(current_avg_std=2.0, power_factor_mean=0.7)]
        result = compute_degradation_score(baseline, windows)

        assert len(result.top_reasons) > 0
        assert all(isinstance(r, str) for r in result.top_reasons)

    def test_no_reasons_when_healthy(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        assert result.top_reasons == ()


class TestContributionObservedBaseline:
    def test_available_signal_has_observed_and_baseline(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window(current_avg_std=0.8, power_factor_mean=0.90)]
        result = compute_degradation_score(baseline, windows)

        for c in result.contributions:
            if c.signal == "current_variability_drift" and c.available:
                assert c.observed_value is not None
                assert c.baseline_value is not None
                assert c.raw_drift is not None
                break

    def test_unavailable_signal_has_none_observed_baseline(self):
        baseline = BaselineInput(
            current_avg_std=0.5,
            power_factor_mean=0.95,
            power_mean=5000.0,
            quality_score=1.0,
        )
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        phase_c = [c for c in result.contributions if c.signal == "phase_imbalance_drift"][0]
        assert phase_c.available is False
        assert phase_c.observed_value is None
        assert phase_c.baseline_value is None
        assert phase_c.raw_drift is None

    def test_trend_worsening_has_none_observed_baseline(self):
        baseline = _healthy_baseline()
        windows = [_healthy_window()]
        result = compute_degradation_score(baseline, windows)

        trend = [c for c in result.contributions if c.signal == "trend_worsening"][0]
        assert trend.observed_value is None
        assert trend.baseline_value is None
