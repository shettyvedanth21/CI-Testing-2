from __future__ import annotations

import sys
import types
from datetime import date
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

from app.services.anomaly.aggregator import aggregate_daily_counts, aggregate_weekly_counts
from app.services.anomaly.types import AnomalyCandidate, DailyCountResult


def _make_event(signal_field="current_avg", severity="mild", confidence=0.8,
                supply_related=False, startup_adjacent=False, mode_change=False,
                recurring=False):
    return AnomalyCandidate(
        signal_field=signal_field,
        severity=severity,
        confidence=confidence,
        supply_related=supply_related,
        startup_adjacent=startup_adjacent,
        mode_change=mode_change,
        recurring=recurring,
        merged_window_count=1,
    )


def test_daily_counts_happy_path():
    events = [
        _make_event(severity="mild"),
        _make_event(severity="strong"),
        _make_event(severity="severe"),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result is not None
    assert result.total_count == 3
    assert result.mild_count == 1
    assert result.strong_count == 1
    assert result.severe_count == 1
    assert result.supply_related_count == 0


def test_daily_supply_related_excluded_from_severity():
    events = [
        _make_event(severity="strong", supply_related=True),
        _make_event(severity="mild"),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.total_count == 2
    assert result.mild_count == 1
    assert result.strong_count == 0
    assert result.supply_related_count == 1


def test_daily_startup_adjacent_excluded_from_severity():
    events = [
        _make_event(severity="strong", startup_adjacent=True),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.total_count == 1
    assert result.strong_count == 0


def test_daily_mode_change_included_in_severity():
    events = [
        _make_event(severity="mild", mode_change=True),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.total_count == 1
    assert result.mild_count == 1


def test_daily_mode_change_severe_in_severity():
    events = [
        _make_event(severity="severe", mode_change=True),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.total_count == 1
    assert result.severe_count == 1


def test_daily_mode_change_strong_in_severity():
    events = [
        _make_event(severity="strong", mode_change=True),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.total_count == 1
    assert result.strong_count == 1


def test_daily_mode_change_mixed_with_non_mode():
    events = [
        _make_event(severity="mild", mode_change=True),
        _make_event(severity="strong"),
        _make_event(severity="severe", mode_change=True),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.total_count == 3
    assert result.mild_count == 1
    assert result.strong_count == 1
    assert result.severe_count == 1


def test_daily_mode_change_with_supply_related_supply_wins():
    events = [
        _make_event(severity="severe", mode_change=True, supply_related=True),
        _make_event(severity="mild", mode_change=True),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.total_count == 2
    assert result.severe_count == 0
    assert result.mild_count == 1
    assert result.supply_related_count == 1


def test_daily_top_signal():
    events = [
        _make_event(signal_field="current_avg"),
        _make_event(signal_field="current_avg"),
        _make_event(signal_field="power"),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.top_signal == "current_avg"


def test_daily_top_signal_tie_alphabetical():
    events = [
        _make_event(signal_field="power"),
        _make_event(signal_field="current_avg"),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.top_signal == "current_avg"


def test_daily_avg_confidence():
    events = [
        _make_event(confidence=0.5),
        _make_event(confidence=0.9),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.avg_confidence is not None
    assert abs(result.avg_confidence - 0.7) < 0.01


def test_daily_no_events():
    result = aggregate_daily_counts([], date(2026, 5, 21))

    assert result is None


def test_daily_only_supply_related():
    events = [
        _make_event(severity="strong", supply_related=True),
        _make_event(severity="mild", supply_related=True),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result.total_count == 2
    assert result.mild_count == 0
    assert result.strong_count == 0
    assert result.severe_count == 0
    assert result.supply_related_count == 2


def test_weekly_counts_happy_path():
    dailies = [
        DailyCountResult(date=date(2026, 5, 19), total_count=3, mild_count=2, strong_count=1, severe_count=0, supply_related_count=0),
        DailyCountResult(date=date(2026, 5, 20), total_count=5, mild_count=3, strong_count=1, severe_count=1, supply_related_count=0),
    ]
    result = aggregate_weekly_counts(dailies, date(2026, 5, 19))

    assert result is not None
    assert result.total_count == 8
    assert result.mild_count == 5
    assert result.strong_count == 2
    assert result.severe_count == 1


def test_weekly_counts_no_dailies():
    result = aggregate_weekly_counts([], date(2026, 5, 19))

    assert result is None


def test_weekly_week_over_week_change():
    dailies = [
        DailyCountResult(date=date(2026, 5, 19), total_count=5, mild_count=3, strong_count=1, severe_count=1, supply_related_count=0),
    ]
    result = aggregate_weekly_counts(dailies, date(2026, 5, 19), prior_week_total=3)

    assert result.week_over_week_change == 2


def test_weekly_week_over_week_negative():
    dailies = [
        DailyCountResult(date=date(2026, 5, 19), total_count=2, mild_count=1, strong_count=1, severe_count=0, supply_related_count=0),
    ]
    result = aggregate_weekly_counts(dailies, date(2026, 5, 19), prior_week_total=5)

    assert result.week_over_week_change == -3


def test_weekly_no_prior_week():
    dailies = [
        DailyCountResult(date=date(2026, 5, 19), total_count=5, mild_count=3, strong_count=1, severe_count=1, supply_related_count=0),
    ]
    result = aggregate_weekly_counts(dailies, date(2026, 5, 19))

    assert result.week_over_week_change is None


def test_merged_event_counts_as_one():
    event = AnomalyCandidate(
        signal_field="current_avg",
        severity="strong",
        confidence=0.9,
        merged_window_count=4,
    )
    result = aggregate_daily_counts([event], date(2026, 5, 21))

    assert result.total_count == 1
    assert result.strong_count == 1


def test_daily_signal_breakdown_populated():
    events = [
        _make_event(signal_field="current_avg", severity="mild"),
        _make_event(signal_field="current_avg", severity="strong"),
        _make_event(signal_field="power", severity="severe"),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result is not None
    assert len(result.signal_breakdown) == 2
    current_entry = [e for e in result.signal_breakdown if e.field_name == "current_avg"][0]
    assert current_entry.count == 2
    assert current_entry.mild == 1
    assert current_entry.strong == 1
    assert current_entry.severe == 0
    power_entry = [e for e in result.signal_breakdown if e.field_name == "power"][0]
    assert power_entry.count == 1
    assert power_entry.severe == 1


def test_daily_signal_breakdown_mode_change_in_severity():
    events = [
        _make_event(signal_field="current_avg", severity="strong", mode_change=True),
        _make_event(signal_field="current_avg", severity="mild"),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result is not None
    current_entry = [e for e in result.signal_breakdown if e.field_name == "current_avg"][0]
    assert current_entry.count == 2
    assert current_entry.strong == 1
    assert current_entry.mild == 1


def test_daily_signal_breakdown_supply_not_in_severity():
    events = [
        _make_event(signal_field="current_avg", severity="strong", supply_related=True),
        _make_event(signal_field="current_avg", severity="mild"),
    ]
    result = aggregate_daily_counts(events, date(2026, 5, 21))

    assert result is not None
    current_entry = [e for e in result.signal_breakdown if e.field_name == "current_avg"][0]
    assert current_entry.count == 2
    assert current_entry.strong == 0
    assert current_entry.mild == 1


def test_daily_signal_breakdown_empty_when_no_events():
    result = aggregate_daily_counts([], date(2026, 5, 21))
    assert result is None


def test_weekly_signal_breakdown_merged_from_dailies():
    from app.services.anomaly.types import DailyCountResult, SignalBreakdownEntry

    dailies = [
        DailyCountResult(
            date=date(2026, 5, 18),
            total_count=3, mild_count=1, strong_count=1, severe_count=1,
            supply_related_count=0, top_signal="current_avg", avg_confidence=0.8,
            signal_breakdown=(
                SignalBreakdownEntry(field_name="current_avg", count=2, mild=1, strong=1, severe=0),
                SignalBreakdownEntry(field_name="power", count=1, mild=0, strong=0, severe=1),
            ),
        ),
        DailyCountResult(
            date=date(2026, 5, 19),
            total_count=2, mild_count=0, strong_count=1, severe_count=1,
            supply_related_count=1, top_signal="power", avg_confidence=0.9,
            signal_breakdown=(
                SignalBreakdownEntry(field_name="current_avg", count=1, mild=0, strong=1, severe=0),
                SignalBreakdownEntry(field_name="power", count=1, mild=0, strong=0, severe=1),
            ),
        ),
    ]
    result = aggregate_weekly_counts(dailies, date(2026, 5, 18))

    assert result is not None
    assert len(result.signal_breakdown) == 2
    current_entry = [e for e in result.signal_breakdown if e.field_name == "current_avg"][0]
    assert current_entry.count == 3
    assert current_entry.mild == 1
    assert current_entry.strong == 2
    power_entry = [e for e in result.signal_breakdown if e.field_name == "power"][0]
    assert power_entry.count == 2
    assert power_entry.severe == 2


def test_weekly_signal_breakdown_empty_when_no_dailies():
    result = aggregate_weekly_counts([], date(2026, 5, 18))
    assert result is None
