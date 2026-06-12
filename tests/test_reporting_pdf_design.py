from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


REPORTING_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "reporting-service"
if str(REPORTING_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(REPORTING_SERVICE_ROOT))

pytest.importorskip("jinja2")

from src.pdf import builder as pdf_builder


def _consumption_payload() -> dict[str, object]:
    return {
        "report_id": "RPT-ENERGY-1",
        "device_label": "All Machines",
        "start_date": "2026-04-01",
        "end_date": "2026-04-07",
        "total_kwh": 142.4,
        "peak_demand_kw": 21.8,
        "peak_timestamp": "2026-04-05 14:10 IST",
        "average_load_kw": 5.6,
        "load_factor_pct": 42.0,
        "load_factor_band": "moderate",
        "total_cost": 1384.2,
        "currency": "INR",
        "tariff_rate_used": 9.72,
        "daily_series": [
            {"date": "2026-04-01", "kwh": 18.4},
            {"date": "2026-04-02", "kwh": 21.7},
        ],
        "per_device": [
            {
                "device_id": "CITT-01",
                "device_name": "Machine A",
                "total_kwh": 88.0,
                "peak_demand_kw": 13.1,
                "load_factor_pct": 44.3,
                "quality": "high",
                "method": "metered",
            },
            {
                "device_id": "CITT-02",
                "device_name": "Machine B",
                "total_kwh": 54.4,
                "peak_demand_kw": 8.7,
                "load_factor_pct": 37.2,
                "quality": "medium",
                "method": "metered",
            },
        ],
        "overtime_summary": {
            "configured_devices": 2,
            "devices_without_shift": 0,
            "total_minutes": 75.0,
            "total_hours": 1.25,
            "total_kwh": 4.1,
            "total_cost": 39.85,
            "currency": "INR",
            "tariff_rate_used": 9.72,
            "device_count": 2,
            "rows": [
                {
                    "date": "2026-04-02",
                    "device_name": "Machine A",
                    "window_start": "02 Apr 2026, 06:10 PM",
                    "window_end": "02 Apr 2026, 06:40 PM",
                    "overtime_minutes": 30.0,
                    "overtime_hours": 0.5,
                    "overtime_kwh": 1.6,
                    "overtime_cost": 15.55,
                    "shift_status": "Overtime",
                }
            ],
            "device_summary": [
                {
                    "device_name": "Machine A",
                    "configured": True,
                    "shift_count": 1,
                    "total_overtime_minutes": 30.0,
                    "total_overtime_hours": 0.5,
                    "total_overtime_kwh": 1.6,
                    "total_overtime_cost": 15.55,
                    "currency": "INR",
                },
                {
                    "device_name": "Machine B",
                    "configured": True,
                    "shift_count": 1,
                    "total_overtime_minutes": 45.0,
                    "total_overtime_hours": 0.75,
                    "total_overtime_kwh": 2.5,
                    "total_overtime_cost": 24.3,
                    "currency": "INR",
                },
            ],
        },
        "overtime_rows": [],
        "overtime_device_summary": [],
        "insights": [
            "Machine A accounted for the majority of the week's demand spike.",
            "Cost remained concentrated in two high-load operating windows.",
        ],
        "warnings": ["Machine B had one partially estimated telemetry interval."],
        "overall_quality": "medium",
        "tariff_fetched_at": "2026-04-01 09:00 IST",
        "generated_at": "2026-04-07 18:45 IST",
    }


def _comparison_payload() -> dict[str, object]:
    return {
        "report_id": "RPT-COMP-1",
        "device_a_name": "Machine A",
        "device_b_name": "Machine B",
        "start_date": "2026-04-01",
        "end_date": "2026-04-07",
        "winner": "Machine B",
        "comparison": {
            "energy_comparison": {
                "device_a_kwh": 88.0,
                "device_b_kwh": 75.4,
                "difference_kwh": 12.6,
                "difference_percent": 14.32,
                "higher_consumer": "Machine A",
            },
            "demand_comparison": {
                "device_a_peak_kw": 13.1,
                "device_b_peak_kw": 10.4,
                "difference_kw": 2.7,
                "difference_percent": 20.61,
                "higher_demand": "Machine A",
            },
            "metrics": {"Energy": 12.6, "Demand": 2.7},
        },
        "insights": [
            "Machine B delivered the lower energy profile over the selected period.",
            "Demand variance is concentrated on Machine A.",
        ],
    }


def test_pdf_templates_keep_print_layout_contract() -> None:
    consumption_template = pdf_builder.get_consumption_report_template()
    comparison_template = pdf_builder.get_comparison_report_template()

    for template in (consumption_template, comparison_template):
        assert "@page" in template
        assert "size: A4" in template
        assert ".align-right" in template
        assert ".financial" in template
        assert "Shivex Energy Intelligence" in template


def test_consumption_pdf_uses_professional_shivex_layout(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_render(html_content: str) -> bytes:
        captured["html"] = html_content
        return b"PDF"

    monkeypatch.setattr(pdf_builder, "_render_pdf", fake_render)
    monkeypatch.setattr(pdf_builder.charts, "daily_energy_bar_chart", lambda *_: None)
    monkeypatch.setattr(pdf_builder.charts, "device_share_donut", lambda *_: None)

    pdf_bytes = pdf_builder.generate_consumption_pdf(_consumption_payload())

    html = captured["html"]
    assert pdf_bytes == b"PDF"
    assert "Shivex Energy Intelligence" in html
    assert "Primary Cost Driver" in html
    assert "Quality Band" not in html
    assert "Reading This Report" not in html
    assert "professional energy report generated" in html
    assert "class=\"financial\"" in html
    assert "section-kicker" in html
    assert "<th>From</th>" in html
    assert "<th>To</th>" in html
    assert "Each overtime proof row below shows the exact outside-shift window" in html
    assert "02 Apr 2026, 06:10 PM" in html


def test_comparison_pdf_uses_shared_polished_layout(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_render(html_content: str) -> bytes:
        captured["html"] = html_content
        return b"PDF"

    monkeypatch.setattr(pdf_builder, "_render_pdf", fake_render)
    monkeypatch.setattr(pdf_builder.charts, "comparison_bar_chart", lambda *_: None)

    pdf_bytes = pdf_builder.generate_comparison_pdf(_comparison_payload())

    html = captured["html"]
    assert pdf_bytes == b"PDF"
    assert "Shivex Energy Intelligence" in html
    assert "Recommended Winner" in html
    assert "How To Use This Comparison" in html
    assert "Comparison Overview" in html
    assert "professional comparison report" in html


def test_runtime_pdf_generation_smoke_when_weasyprint_available(monkeypatch) -> None:
    try:
        importlib.import_module("weasyprint")
    except Exception as exc:  # pragma: no cover - environment-specific native dependency gate
        pytest.skip(f"WeasyPrint runtime unavailable: {exc}")

    monkeypatch.setattr(pdf_builder.charts, "daily_energy_bar_chart", lambda *_: None)
    monkeypatch.setattr(pdf_builder.charts, "device_share_donut", lambda *_: None)
    monkeypatch.setattr(pdf_builder.charts, "comparison_bar_chart", lambda *_: None)

    energy_pdf = pdf_builder.generate_consumption_pdf(_consumption_payload())
    comparison_pdf = pdf_builder.generate_comparison_pdf(_comparison_payload())

    assert energy_pdf.startswith(b"%PDF")
    assert comparison_pdf.startswith(b"%PDF")
    assert len(energy_pdf) > 1000
    assert len(comparison_pdf) > 1000
