from datetime import datetime

from services.shared.telemetry_coverage import (
    build_device_coverage_result,
    build_window_coverage_result,
)


def test_seven_day_window_with_three_days_telemetry_is_partial_usable():
    coverage = build_window_coverage_result(
        selected_window_start=datetime(2026, 4, 1),
        selected_window_end=datetime(2026, 4, 8),
        covered_duration_hours=72.0,
    ).to_dict()

    assert coverage["level"] == "partial_coverage"
    assert coverage["coverage_pct"] == 42.86
    assert coverage["usable_for_business_decisions"] is True
    assert coverage["artifact_generation_allowed"] is True


def test_zero_telemetry_is_no_coverage_business_blocked():
    coverage = build_window_coverage_result(
        selected_window_start=datetime(2026, 4, 1),
        selected_window_end=datetime(2026, 4, 8),
        covered_duration_hours=0.0,
    ).to_dict()

    assert coverage["level"] == "no_coverage"
    assert coverage["usable_for_business_decisions"] is False
    assert coverage["terminal_status"] == "business_blocked"


def test_quality_gate_can_mark_device_coverage_insufficient_without_system_failure():
    coverage = build_device_coverage_result(
        selected_device_ids=["D1", "D2"],
        usable_device_ids=[],
        has_any_data=True,
        skipped_devices=[
            {"device_id": "D1", "reason": "INSUFFICIENT_DATA"},
            {"device_id": "D2", "reason": "LOW_QUALITY_DATA"},
        ],
        artifact_generation_allowed=False,
    ).to_dict()

    assert coverage["level"] == "insufficient_coverage"
    assert coverage["usable_for_business_decisions"] is False
    assert coverage["artifact_generation_allowed"] is False
    assert coverage["terminal_status"] == "business_blocked"
