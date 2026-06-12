from __future__ import annotations

import os
import sys

from jinja2 import Template

sys.path.insert(0, "/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/services/reporting-service")
sys.path.insert(1, "/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/services")

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from src.pdf.builder import get_consumption_report_template  # noqa: E402


def _base_payload() -> dict:
    return {
        "report_theme_class": "theme-energy",
        "report_id": "report-1",
        "generated_at": "2026-04-16 12:00",
        "device_label": "Machine A",
        "start_date": "2026-04-16",
        "end_date": "2026-04-16",
        "overall_quality": "high",
        "tariff_rate_used": 8.3,
        "currency": "INR",
        "peak_timestamp": "2026-04-16 10:00",
        "total_kwh": 12.34,
        "peak_demand_kw": 1.23,
        "load_factor_pct": 45.6,
        "average_load_kw": 0.91,
        "total_cost": 102.42,
        "tariff_fetched_at": "2026-04-16 09:00",
        "per_device": [
            {
                "device_name": "Machine A",
                "total_kwh": 12.34,
                "peak_demand_kw": 1.23,
                "load_factor_pct": 45.6,
                "quality": "high",
                "total_cost": 102.42,
                "method": "normalized_business_power",
            }
        ],
        "daily_series": [
            {"date": "2026-04-16", "kwh": 12.34, "cost": 102.42},
        ],
        "charts": {},
        "overtime_summary": None,
        "warnings": [],
        "insights": [],
        "hidden_overconsumption_insight": {
            "summary": {
                "selected_days": 2,
                "total_actual_energy_kwh": 20.0,
                "aggregate_p75_baseline_reference": 250.0,
                "total_baseline_energy_kwh": 18.2,
                "total_hidden_overconsumption_kwh": 1.8,
                "total_hidden_overconsumption_cost": 14.94,
                "tariff_rate_used": 8.3,
            },
            "daily_breakdown": [
                {
                    "date": "2026-04-15",
                    "actual_energy_kwh": 10.1,
                    "p75_power_baseline_w": 245.0,
                    "baseline_energy_kwh": 9.2,
                    "hidden_overconsumption_kwh": 0.9,
                    "hidden_overconsumption_cost": 7.47,
                    "sample_count": 120,
                    "covered_duration_hours": 24.0,
                },
                {
                    "date": "2026-04-16",
                    "actual_energy_kwh": 9.9,
                    "p75_power_baseline_w": 255.0,
                    "baseline_energy_kwh": 9.0,
                    "hidden_overconsumption_kwh": 0.9,
                    "hidden_overconsumption_cost": 7.47,
                    "sample_count": 118,
                    "covered_duration_hours": 24.0,
                },
            ],
            "device_breakdown": [
                {
                    "date": "2026-04-15",
                    "device_id": "DEVICE-1",
                    "device_name": "Machine 1",
                    "actual_energy_kwh": 5.1,
                    "p75_power_baseline_w": 145.0,
                    "baseline_energy_kwh": 4.2,
                    "difference_vs_baseline_kwh": 0.9,
                    "status": "Above Baseline",
                    "hidden_overconsumption_kwh": 0.9,
                    "hidden_overconsumption_cost": 7.47,
                    "sample_count": 60,
                    "covered_duration_hours": 24.0,
                },
                {
                    "date": "2026-04-16",
                    "device_id": "DEVICE-2",
                    "device_name": "Machine 2",
                    "actual_energy_kwh": 4.9,
                    "p75_power_baseline_w": 155.0,
                    "baseline_energy_kwh": 4.0,
                    "difference_vs_baseline_kwh": 0.9,
                    "status": "Above Baseline",
                    "hidden_overconsumption_kwh": 0.9,
                    "hidden_overconsumption_cost": 7.47,
                    "sample_count": 58,
                    "covered_duration_hours": 24.0,
                },
            ],
            "aggregation_rule": {
                "total_baseline_energy_kwh": "sum(daily_baseline_energy_kwh)",
            },
            "insight_text": None,
        },
    }


def _render(payload: dict) -> str:
    return Template(get_consumption_report_template()).render(**payload)


def test_consumption_report_template_renders_hidden_overconsumption_section_when_present() -> None:
    html = _render(_base_payload())

    assert "Hidden Overconsumption Insight (P75 Baseline)" in html
    assert "Total Hidden Overconsumption" in html
    assert "Hidden Overconsumption Cost" in html
    assert "Total Baseline Energy" in html
    assert "Aggregate P75 Baseline" in html
    assert "selected day" in html
    assert "Date" in html
    assert "Actual Energy (kWh)" in html
    assert "P75 Baseline Power (W)" in html
    assert "Baseline Energy (kWh)" in html
    assert "Hidden Overconsumption (kWh)" in html
    assert "Hidden Overconsumption by Device" in html
    assert "Machine-wise contribution to hidden overconsumption" in html


def test_consumption_report_template_shows_cost_columns_and_hides_method_column() -> None:
    html = _render(_base_payload())

    assert ">Cost<" in html
    assert "102.42" in html
    assert "normalized_business_power" not in html


def test_consumption_report_template_renders_hidden_overconsumption_daily_headers() -> None:
    html = _render(_base_payload())

    assert "Actual Energy (kWh)" in html
    assert "P75 Baseline Power (W)" in html
    assert "Baseline Energy (kWh)" in html
    assert "Difference vs Baseline (kWh)" in html
    assert "Status" in html
    assert "Hidden Overconsumption (kWh)" in html
    assert "Hidden Overconsumption Cost" in html


def test_consumption_report_template_uses_safe_cost_fallback_when_tariff_missing() -> None:
    payload = _base_payload()
    payload["currency"] = "INR"
    payload["hidden_overconsumption_insight"]["summary"]["total_hidden_overconsumption_cost"] = None
    payload["hidden_overconsumption_insight"]["summary"]["tariff_rate_used"] = None
    payload["hidden_overconsumption_insight"]["daily_breakdown"][0]["hidden_overconsumption_cost"] = None
    payload["hidden_overconsumption_insight"]["daily_breakdown"][1]["hidden_overconsumption_cost"] = None

    html = _render(payload)

    assert "Hidden Overconsumption Cost" in html
    assert ">N/A<" in html


def test_consumption_report_template_uses_print_safe_hidden_record_layout_instead_of_wide_table() -> None:
    html = _render(_base_payload())

    assert "hidden-record-card" in html
    assert "hidden-record-grid" in html
    assert '<th class="align-right">Hidden Overconsumption (kWh)</th>' not in html
    assert '<th class="align-right">Covered Duration (hours)</th>' not in html


def test_consumption_report_template_renders_device_hidden_overconsumption_fields() -> None:
    html = _render(_base_payload())

    assert "Device Name" in html
    assert "Device ID" in html
    assert "Sample Count" in html
    assert "Covered Duration (hours)" in html
    assert "Machine 1" in html
    assert "DEVICE-1" in html


def test_consumption_report_template_falls_back_to_device_id_when_device_name_missing() -> None:
    payload = _base_payload()
    payload["hidden_overconsumption_insight"]["device_breakdown"][0]["device_name"] = None

    html = _render(payload)

    assert "Device Name" in html
    assert "DEVICE-1" in html


def test_consumption_report_template_shows_safe_message_when_device_breakdown_missing() -> None:
    payload = _base_payload()
    payload["hidden_overconsumption_insight"]["device_breakdown"] = []

    html = _render(payload)

    assert "Device-wise hidden overconsumption breakdown is unavailable for this selection." in html


def test_consumption_report_template_renders_baseline_status_for_below_and_above_days() -> None:
    payload = _base_payload()
    payload["hidden_overconsumption_insight"]["daily_breakdown"] = [
        {
            "date": "2026-04-15",
            "actual_energy_kwh": 1.357,
            "p75_power_baseline_w": 250.0,
            "baseline_energy_kwh": 1.9596,
            "hidden_overconsumption_kwh": 0.0,
            "hidden_overconsumption_cost": 0.0,
            "sample_count": 65,
            "covered_duration_hours": 7.4167,
        },
        {
            "date": "2026-04-16",
            "actual_energy_kwh": 2.2,
            "p75_power_baseline_w": 250.0,
            "baseline_energy_kwh": 2.0,
            "hidden_overconsumption_kwh": 0.2,
            "hidden_overconsumption_cost": 1.66,
            "sample_count": 65,
            "covered_duration_hours": 7.4167,
        },
    ]

    html = _render(payload)

    assert "Below Baseline" in html
    assert "Above Baseline" in html
    assert "+0.2000" in html
    assert "-0.6026" in html
    assert ">0.0<" in html


def test_consumption_report_template_shows_insufficient_telemetry_message_when_no_usable_rows() -> None:
    payload = _base_payload()
    payload["hidden_overconsumption_insight"]["daily_breakdown"] = [
        {
            "date": "2026-04-16",
            "actual_energy_kwh": 0.0,
            "p75_power_baseline_w": None,
            "baseline_energy_kwh": None,
            "hidden_overconsumption_kwh": 0.0,
            "hidden_overconsumption_cost": 0.0,
            "sample_count": 0,
            "covered_duration_hours": 0.0,
        }
    ]
    payload["hidden_overconsumption_insight"]["device_breakdown"] = []

    html = _render(payload)

    assert "Hidden overconsumption insight is unavailable for this selection due to insufficient telemetry." in html
    assert "Device-wise hidden overconsumption breakdown is unavailable for this selection." in html
    assert "P75 Baseline Power" not in html


def test_consumption_report_template_keeps_existing_sections() -> None:
    html = _render(_base_payload())

    assert "Executive Summary" in html
    assert "Commercial Context" not in html
    assert "Cost and Data Notes" not in html
    assert "professional energy report generated" in html
    assert "Aggregation Rule" not in html
