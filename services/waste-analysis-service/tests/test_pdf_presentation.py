import types

from jinja2 import Template

from src.pdf import builder
from src.pdf.formatting import duration_label


def test_duration_label_human_readable():
    assert duration_label(None) == "—"
    assert duration_label(1800) == "30 min"
    assert duration_label(8700) == "2 hr 25 min"


def test_waste_report_template_excludes_data_notes_standby_and_data_quality_presentations():
    template = builder._template()
    assert "Data Notes" not in template
    assert "No data quality warnings for this run." not in template
    assert "Data Quality" not in template
    assert "Standby Energy Loss" not in template
    assert "charts.standby" not in template


def test_waste_report_html_keeps_required_sections_without_removed_sections():
    html = Template(builder._template()).render(
        generated_at="2026-04-16 10:00",
        start_date="2026-04-01",
        end_date="2026-04-15",
        scope_label="All Devices",
        tariff_rate_used=10.0,
        currency="INR",
        total_waste_cost=100.0,
        total_idle_kwh=5.0,
        total_idle_label="2 hr 0 min",
        worst_device="D1",
        total_energy_kwh=40.0,
        total_energy_cost=400.0,
        pdf_idle_total_cost=20.0,
        pdf_offhours_total_duration_label="30 min",
        pdf_offhours_total_kwh=1.0,
        pdf_offhours_total_cost=10.0,
        pdf_overconsumption_total_duration_label="15 min",
        pdf_overconsumption_total_kwh=0.5,
        pdf_overconsumption_total_cost=5.0,
        device_summaries=[{"device_name": "D1"}],
        pdf_devices=[
            {
                "device_name": "D1",
                "idle_duration_label": "1 hr 0 min",
                "idle_energy_kwh": 2.0,
                "idle_cost": 20.0,
                "offhours_duration_label": "30 min",
                "offhours_energy_kwh": 1.0,
                "offhours_cost": 10.0,
                "offhours_skipped_reason": None,
                "offhours_pf_estimated": False,
                "overconsumption_duration_label": "15 min",
                "overconsumption_kwh": 0.5,
                "overconsumption_cost": 5.0,
                "overconsumption_skipped_reason": None,
                "overconsumption_pf_estimated": False,
                "total_energy_kwh": 10.0,
                "total_cost": 100.0,
                "calculation_method": "interval_power",
                "pf_estimated": False,
            }
        ],
        pdf_any_pf_estimated=False,
        pdf_omitted_devices=0,
        insights=["Off-hours energy waste in selected period: INR 10"],
        warnings=[],
    )
    assert "Idle Running Analysis" in html
    assert "Off-Hours Running Analysis" in html
    assert "Overconsumption Analysis" in html
    assert "Total Consumption by Device" in html
    assert "Key Insights" in html
    assert "Data Notes" not in html
    assert "No data quality warnings for this run." not in html
    assert "Data Quality" not in html
    assert "Standby Energy Loss" not in html
    assert "shared_energy_accounting" not in html
    assert "PF Estimated" not in html


def test_waste_report_html_renders_section_footer_totals() -> None:
    html = Template(builder._template()).render(
        generated_at="2026-04-16 10:00",
        start_date="2026-04-01",
        end_date="2026-04-15",
        scope_label="All Devices",
        tariff_rate_used=10.0,
        currency="INR",
        total_waste_cost=100.0,
        total_idle_kwh=5.0,
        total_idle_label="2 hr 0 min",
        total_energy_kwh=40.0,
        total_energy_cost=400.0,
        pdf_idle_total_cost=50.0,
        pdf_offhours_total_duration_label="1 hr 0 min",
        pdf_offhours_total_kwh=8.0,
        pdf_offhours_total_cost=80.0,
        pdf_overconsumption_total_duration_label="30 min",
        pdf_overconsumption_total_kwh=2.0,
        pdf_overconsumption_total_cost=20.0,
        worst_device="D1",
        device_summaries=[{"device_name": "D1"}],
        pdf_devices=[
            {
                "device_name": "D1",
                "idle_duration_label": "2 hr 0 min",
                "idle_energy_kwh": 5.0,
                "idle_cost": 50.0,
                "offhours_duration_label": "1 hr 0 min",
                "offhours_energy_kwh": 8.0,
                "offhours_cost": 80.0,
                "offhours_skipped_reason": None,
                "offhours_pf_estimated": False,
                "overconsumption_duration_label": "30 min",
                "overconsumption_kwh": 2.0,
                "overconsumption_cost": 20.0,
                "overconsumption_skipped_reason": None,
                "overconsumption_pf_estimated": False,
                "total_energy_kwh": 40.0,
                "total_cost": 400.0,
                "calculation_method": "shared_energy_accounting",
                "pf_estimated": False,
            }
        ],
        pdf_any_pf_estimated=False,
        pdf_omitted_devices=0,
        insights=[],
        warnings=[],
    )

    assert html.count(">Total<") >= 4
    assert "INR 50.0" in html
    assert "INR 80.0" in html
    assert "INR 20.0" in html
    assert "INR 400.0" in html


def test_generate_waste_pdf_chart_contract_excludes_standby(monkeypatch):
    payload = {
        "device_summaries": [
            {
                "device_name": "D1",
                "idle_cost": 1.0,
                "offhours_cost": 0.0,
                "overconsumption_cost": 0.0,
                "offhours_duration_sec": 0,
                "overconsumption_duration_sec": 0,
                "pf_estimated": False,
                "offhours_pf_estimated": False,
                "overconsumption_pf_estimated": False,
            }
        ],
        "currency": "INR",
        "start_date": "2026-04-01",
        "end_date": "2026-04-15",
        "scope_label": "All Devices",
        "tariff_rate_used": 10.0,
        "total_waste_cost": 1.0,
        "total_idle_kwh": 0.1,
        "total_idle_label": "1 min",
        "worst_device": "D1",
        "total_energy_kwh": 1.0,
        "total_energy_cost": 10.0,
        "insights": [],
        "warnings": [],
    }

    class _FakeHtml:
        def __init__(self, string: str):
            self.string = string

        def write_pdf(self) -> bytes:
            return b"%PDF-FAKE%"

    monkeypatch.setitem(__import__("sys").modules, "weasyprint", types.SimpleNamespace(HTML=_FakeHtml))
    out = builder.generate_waste_pdf(payload)
    assert out == b"%PDF-FAKE%"
