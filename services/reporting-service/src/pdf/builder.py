import asyncio
from datetime import datetime

from jinja2 import Template
from zoneinfo import ZoneInfo

from src.pdf import charts
from src.utils.localization import format_platform_timestamp


def _render_pdf(html_content: str) -> bytes:
    try:
        from weasyprint import HTML as WeasyHTML
    except Exception as exc:
        raise RuntimeError(
            "WeasyPrint is required to render PDF reports. "
            "Install the native WeasyPrint dependencies before generating a report."
        ) from exc

    return WeasyHTML(string=html_content).write_pdf()


async def async_render_pdf(html_content: str) -> bytes:
    return await asyncio.to_thread(_render_pdf, html_content)


_FACTOR_UNIT_DISPLAY = {
    "kg_co2_per_kwh": "kg CO\u2082/kWh",
}


def _display_factor_unit(unit: str) -> str:
    return _FACTOR_UNIT_DISPLAY.get(unit, unit)


_FACTOR_SOURCE_DISPLAY = {
    "platform_default": "Platform Default",
    "tenant_default": "Organisation Default",
}


def _display_factor_source(source: str) -> str:
    if not source or source in ("unconfigured", "unknown"):
        return ""
    return _FACTOR_SOURCE_DISPLAY.get(source, "")


def _build_factor_source_display(source_name: str | None, factor_source: str | None) -> str:
    src_name = (source_name or "").strip()
    src_class = _display_factor_source(factor_source or "")
    parts = [p for p in (src_name, src_class) if p]
    return ", ".join(parts)


def _prepare_co2_for_template(data: dict) -> None:
    co2_overview = data.get("co2_overview")
    if not co2_overview or not isinstance(co2_overview, dict):
        return
    if not co2_overview.get("available"):
        return

    total = co2_overview.get("total_co2_kg")
    if isinstance(total, (int, float)):
        co2_overview["total_co2_kg"] = round(float(total), 2)

    off_shift = co2_overview.get("off_shift_co2_kg")
    if isinstance(off_shift, (int, float)):
        co2_overview["off_shift_co2_kg"] = round(float(off_shift), 2)

    factor = co2_overview.get("factor") or {}
    co2_overview["factor_unit_display"] = _display_factor_unit(factor.get("unit", ""))
    co2_overview["factor_source_display"] = _build_factor_source_display(
        factor.get("source"), co2_overview.get("factor_source")
    )

    per_device_co2 = co2_overview.get("per_device") or []
    per_device = data.get("per_device") or []
    if not per_device_co2 or not per_device:
        return

    co2_by_device_id = {
        entry.get("device_id"): entry.get("co2_kg")
        for entry in per_device_co2
        if entry.get("device_id") is not None
    }
    for device in per_device:
        device_id = device.get("device_id")
        if device_id in co2_by_device_id:
            raw = co2_by_device_id[device_id]
            device["co2_kg"] = round(float(raw), 2) if isinstance(raw, (int, float)) else None


def generate_consumption_pdf(data: dict) -> bytes:
    daily_series = data.get("daily_series", [])
    per_device = data.get("per_device", [])

    charts_dict = {}
    if daily_series:
        charts_dict["daily_energy"] = charts.daily_energy_bar_chart(daily_series)
    if per_device:
        charts_dict["device_share"] = charts.device_share_donut(per_device)

    data["charts"] = charts_dict
    data["report_theme_class"] = "theme-energy"
    data["generated_at"] = format_platform_timestamp(datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")))
    data["peak_timestamp"] = format_platform_timestamp(data.get("peak_timestamp"))
    data["tariff_fetched_at"] = format_platform_timestamp(data.get("tariff_fetched_at"))
    _prepare_co2_for_template(data)

    html_content = Template(get_consumption_report_template()).render(**data)
    return _render_pdf(html_content)


async def async_generate_consumption_pdf(data: dict) -> bytes:
    daily_series = data.get("daily_series", [])
    per_device = data.get("per_device", [])

    charts_dict = {}
    if daily_series:
        charts_dict["daily_energy"] = await asyncio.to_thread(charts.daily_energy_bar_chart, daily_series)
    if per_device:
        charts_dict["device_share"] = await asyncio.to_thread(charts.device_share_donut, per_device)

    data["charts"] = charts_dict
    data["report_theme_class"] = "theme-energy"
    data["generated_at"] = format_platform_timestamp(datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")))
    data["peak_timestamp"] = format_platform_timestamp(data.get("peak_timestamp"))
    data["tariff_fetched_at"] = format_platform_timestamp(data.get("tariff_fetched_at"))
    _prepare_co2_for_template(data)

    html_content = Template(get_consumption_report_template()).render(**data)
    return await async_render_pdf(html_content)


def generate_comparison_pdf(data: dict) -> bytes:
    comparison = data.get("comparison", {})
    metrics = comparison.get("metrics", {})

    if metrics:
        data["comparison_chart"] = charts.comparison_bar_chart(metrics)

    data["report_theme_class"] = "theme-comparison"
    data["generated_at"] = format_platform_timestamp(datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")))

    html_content = Template(get_comparison_report_template()).render(**data)
    return _render_pdf(html_content)


async def async_generate_comparison_pdf(data: dict) -> bytes:
    comparison = data.get("comparison", {})
    metrics = comparison.get("metrics", {})

    if metrics:
        data["comparison_chart"] = await asyncio.to_thread(charts.comparison_bar_chart, metrics)

    data["report_theme_class"] = "theme-comparison"
    data["generated_at"] = format_platform_timestamp(datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")))

    html_content = Template(get_comparison_report_template()).render(**data)
    return await async_render_pdf(html_content)


def _report_styles() -> str:
    return """
    <style>
        @page {
            size: A4;
            margin: 1.35cm;
        }

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
            font-size: 10px;
            line-height: 1.38;
            color: #172033;
            background: #ffffff;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
        }

        .theme-energy {
            --accent: #2563eb;
            --accent-soft: #dbeafe;
            --accent-2: #0f766e;
            --accent-ink: #12316b;
            --tint: #f4f8ff;
        }

        .theme-comparison {
            --accent: #0f766e;
            --accent-soft: #dff7f1;
            --accent-2: #2563eb;
            --accent-ink: #11423c;
            --tint: #f2fbf8;
        }

        .page {
            background: #ffffff;
            overflow: visible;
        }

        .hero {
            padding: 13px 16px;
            color: #ffffff;
            background:
                radial-gradient(circle at top right, rgba(255, 255, 255, 0.16), transparent 34%),
                linear-gradient(140deg, #0f172a 0%, var(--accent-ink) 44%, var(--accent) 100%);
            border-radius: 12px;
            margin-bottom: 14px;
        }

        .hero-topbar {
            display: table;
            width: 100%;
            table-layout: fixed;
            margin-bottom: 8px;
        }

        .hero-brand,
        .hero-stamp {
            display: table-cell;
            vertical-align: top;
        }

        .hero-brand {
            width: 68%;
        }

        .hero-stamp {
            width: 32%;
            text-align: right;
        }

        .brand-kicker {
            font-size: 7px;
            text-transform: uppercase;
            letter-spacing: 1.7px;
            color: rgba(255, 255, 255, 0.72);
            margin-bottom: 3px;
        }

        .brand-name {
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }

        .hero-stamp > div {
            display: inline-block;
            min-width: 104px;
            padding: 6px 8px;
            border-radius: 9px;
            text-align: left;
            background: rgba(255, 255, 255, 0.11);
            border: 1px solid rgba(255, 255, 255, 0.16);
        }

        .stamp-label {
            font-size: 6.8px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: rgba(255, 255, 255, 0.72);
            margin-bottom: 2px;
        }

        .stamp-value {
            font-size: 8.2px;
            font-weight: 600;
            line-height: 1.25;
        }

        .hero h1 {
            margin: 0;
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.2px;
        }

        .hero-subtitle {
            margin-top: 5px;
            max-width: 100%;
            font-size: 8.8px;
            line-height: 1.35;
            color: rgba(255, 255, 255, 0.88);
        }

        .hero-caption {
            margin-top: 7px;
            font-size: 8px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: rgba(255, 255, 255, 0.72);
        }

        .hero-lines {
            margin-top: 5px;
            font-size: 8.1px;
            line-height: 1.32;
            color: rgba(255, 255, 255, 0.9);
        }

        .hero-lines div {
            margin-top: 1px;
            word-break: break-word;
        }

        .hero-meta {
            display: table;
            width: 100%;
            table-layout: fixed;
            margin-top: 8px;
            border-collapse: separate;
            border-spacing: 6px 0;
        }

        .meta-chip {
            display: table-cell;
            width: 25%;
            vertical-align: top;
        }

        .meta-chip > div {
            min-height: 38px;
            padding: 6px 7px;
            border-radius: 9px;
            background: rgba(255, 255, 255, 0.11);
            border: 1px solid rgba(255, 255, 255, 0.14);
        }

        .meta-label {
            display: block;
            font-size: 6.8px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: rgba(255, 255, 255, 0.72);
            margin-bottom: 2px;
        }

        .meta-value {
            display: block;
            font-size: 8.1px;
            font-weight: 600;
            color: #ffffff;
            word-break: break-word;
            line-height: 1.28;
        }

        .content {
            padding: 0;
        }

        .section {
            margin-bottom: 11px;
            break-inside: avoid;
            page-break-inside: avoid;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            background: #ffffff;
            padding: 10px;
        }

        .section-title-row {
            display: table;
            width: 100%;
            margin-bottom: 7px;
        }

        .section-title-row h2 {
            display: table-cell;
            width: 70%;
            margin: 0;
            font-size: 12.5px;
            letter-spacing: -0.2px;
            color: #0f172a;
        }

        .section-subtitle {
            display: table-cell;
            width: 30%;
            text-align: right;
            color: #64748b;
            font-size: 7.6px;
            vertical-align: bottom;
            letter-spacing: 0.3px;
        }

        .section-kicker {
            display: inline-block;
            margin-bottom: 5px;
            font-size: 7px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.2px;
            color: var(--accent);
        }

        .section-intro {
            margin: 0 0 9px;
            font-size: 8.5px;
            color: #5b6678;
        }

        .kpi-grid {
            display: table;
            width: 100%;
            table-layout: fixed;
            border-spacing: 7px 0;
        }

        .kpi-card {
            display: table-cell;
            width: 25%;
            padding: 8px;
            border-radius: 8px;
            background: #f8fafc;
            border: 1px solid #d9e5f4;
            vertical-align: top;
        }

        .kpi-label {
            font-size: 6.8px;
            text-transform: uppercase;
            letter-spacing: 0.9px;
            color: #64748b;
            margin-bottom: 5px;
        }

        .kpi-value {
            font-size: 14.5px;
            font-weight: 700;
            color: var(--accent-ink);
            line-height: 1.18;
            letter-spacing: -0.2px;
            word-break: break-word;
        }

        .kpi-note {
            margin-top: 5px;
            font-size: 7.3px;
            color: #64748b;
            line-height: 1.3;
        }

        .summary-strip {
            display: table;
            width: 100%;
            table-layout: fixed;
            border-spacing: 7px 0;
            margin-top: 7px;
        }

        .summary-item {
            display: table-cell;
            width: 25%;
            padding: 8px;
            border-radius: 8px;
            background: #f8fbff;
            border: 1px solid #e1eaf5;
            color: #334155;
            font-size: 7.8px;
            vertical-align: top;
            word-break: break-word;
        }

        .summary-item strong {
            display: block;
            font-size: 8.2px;
            color: #0f172a;
            margin-bottom: 3px;
        }

        .two-col {
            display: table;
            width: 100%;
            table-layout: fixed;
            border-spacing: 7px 0;
        }

        .two-col-cell {
            display: table-cell;
            width: 50%;
            vertical-align: top;
        }

        .spotlight-card,
        .callout-card {
            min-height: 100%;
            padding: 10px;
            border-radius: 10px;
            border: 1px solid #dce6f3;
            background: #ffffff;
        }

        .spotlight-card {
            background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
        }

        .spotlight-label {
            font-size: 7px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--accent);
            margin-bottom: 5px;
        }

        .spotlight-value {
            font-size: 15px;
            font-weight: 700;
            line-height: 1.12;
            color: #0f172a;
            word-break: break-word;
        }

        .spotlight-note {
            margin-top: 6px;
            font-size: 7.8px;
            color: #5d6778;
        }

        .callout-card h3 {
            margin: 0 0 6px;
            font-size: 10.5px;
            color: #0f172a;
        }

        .callout-card p {
            margin: 0;
            color: #536072;
            font-size: 8.2px;
            line-height: 1.4;
        }

        .badge {
            display: inline-block;
            padding: 3px 7px;
            border-radius: 999px;
            font-size: 7px;
            font-weight: 700;
            letter-spacing: 0.6px;
            text-transform: uppercase;
        }

        .badge-good {
            background: #dcfce7;
            color: #166534;
        }

        .badge-medium {
            background: #fef3c7;
            color: #92400e;
        }

        .badge-low {
            background: #fee2e2;
            color: #991b1b;
        }

        .badge-muted {
            background: #e2e8f0;
            color: #334155;
        }

        .badge-danger {
            background: #fee2e2;
            color: #991b1b;
        }

        .badge-success-soft {
            background: #dcfce7;
            color: #166534;
        }

        .notice {
            margin-top: 8px;
            padding: 8px 9px;
            border-radius: 8px;
            background: #eff6ff;
            border: 1px solid #d2e4ff;
            color: #1e3a8a;
            font-size: 8px;
        }

        .warning {
            margin-top: 8px;
            padding: 8px 9px;
            border-radius: 8px;
            background: #fff7ed;
            border: 1px solid #fed7aa;
            color: #9a3412;
            font-size: 8px;
        }

        .error {
            margin-top: 8px;
            padding: 8px 9px;
            border-radius: 8px;
            background: #fef2f2;
            border: 1px solid #fecaca;
            color: #991b1b;
            font-size: 8px;
        }

        .chart-row {
            display: table;
            width: 100%;
            table-layout: fixed;
            border-spacing: 7px 0;
        }

        .chart-col {
            display: table-cell;
            width: 50%;
            vertical-align: top;
        }

        .chart-card {
            padding: 8px;
            border-radius: 10px;
            border: 1px solid #dce6f3;
            background: #ffffff;
            text-align: center;
        }

        .chart-title {
            margin-bottom: 6px;
            text-align: left;
            font-size: 8.8px;
            font-weight: 700;
            color: #172033;
        }

        .chart-card img {
            width: 100%;
            max-width: 100%;
            max-height: 190px;
            object-fit: contain;
        }

        .table-wrap {
            overflow: hidden;
            border-radius: 10px;
            border: 1px solid #dce6f3;
            background: #ffffff;
        }

        .hidden-record-list {
            margin-top: 10px;
        }

        .hidden-record-card {
            margin-top: 8px;
            border: 1px solid #dce6f3;
            border-radius: 10px;
            background: #ffffff;
            overflow: hidden;
            break-inside: avoid;
            page-break-inside: avoid;
        }

        .hidden-record-card:first-child {
            margin-top: 0;
        }

        .hidden-record-grid {
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
        }

        .hidden-record-grid td {
            width: 20%;
            padding: 10px 9px;
            border-bottom: 1px solid #e7edf5;
            border-right: 1px solid #e7edf5;
            vertical-align: top;
        }

        .hidden-record-grid tr:last-child td {
            border-bottom: none;
        }

        .hidden-record-grid td:last-child {
            border-right: none;
        }

        .hidden-record-label {
            display: block;
            font-size: 7.6px;
            text-transform: uppercase;
            letter-spacing: 0.9px;
            color: #64748b;
            margin-bottom: 5px;
        }

        .hidden-record-value {
            display: block;
            font-size: 9px;
            font-weight: 600;
            color: #172033;
            line-height: 1.45;
            word-break: break-word;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        thead th {
            background: #f7faff;
            color: #4a5870;
            font-size: 7.8px;
            text-transform: uppercase;
            letter-spacing: 1px;
            border-bottom: 1px solid #dce6f3;
        }

        th,
        td {
            padding: 6px 7px;
            border-bottom: 1px solid #e7edf5;
            text-align: left;
            font-size: 8px;
            vertical-align: top;
            word-break: break-word;
        }

        tbody tr:nth-child(even) {
            background: #fbfdff;
        }

        .right,
        .align-right,
        .numeric,
        .financial {
            text-align: right;
            white-space: nowrap;
        }

        .muted {
            color: #64748b;
        }

        .overtime-table .overtime-row {
            background: #fff1f2;
        }

        .overtime-table .overtime-row td {
            color: #7f1d1d;
        }

        .overtime-table .zero-row td {
            color: #334155;
        }

        .overtime-summary-card {
            width: 33.333%;
        }

        .overtime-note {
            margin-top: 8px;
            font-size: 9px;
            color: #7f1d1d;
        }

        .insight-list {
            margin: 0;
            padding: 0;
            list-style: none;
        }

        .insight-list li {
            margin-top: 6px;
            padding: 8px 9px;
            border-radius: 8px;
            background: #f8fbff;
            border: 1px solid #dbe7f7;
            color: #0f172a;
            font-size: 8px;
        }

        .footer {
            margin-top: 8px;
            padding: 8px 4px 0;
            border-top: 1px solid #e7edf5;
            text-align: center;
            font-size: 7.5px;
            color: #738095;
        }

        .footer strong {
            color: #0f172a;
        }

        .page-break {
            page-break-before: always;
        }
    </style>
    """


def get_consumption_report_template():
    return """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Energy Consumption Report</title>
    """ + _report_styles() + """
</head>
<body class="{{ report_theme_class }}">
    <div class="page">
        <div class="hero">
            <h1>Energy Consumption Report</h1>
            <div class="hero-lines">
                <div>Generated: {{ generated_at }}</div>
                <div>Period: {{ start_date }} to {{ end_date }} | Scope: {{ device_label }}</div>
                <div>Tariff Used: {% if tariff_rate_used is not none %}{{ currency }} {{ tariff_rate_used }}/kWh{% else %}Not configured{% endif %}</div>
            </div>
        </div>

        <div class="content">
            <div class="section">
                <div class="section-kicker">Executive Overview</div>
                <div class="section-title-row">
                    <h2>Executive Summary</h2>
                    <div class="section-subtitle">
                        {% if overall_quality != "high" %}Estimated telemetry detected{% else %}High-confidence telemetry{% endif %}
                    </div>
                </div>
                <p class="section-intro">This page highlights total consumption, demand pressure, financial exposure, and the quality of the telemetry used to build the report.</p>
                <div class="kpi-grid">
                    <div class="kpi-card">
                        <div class="kpi-label">Total Energy</div>
                        <div class="kpi-value">{{ total_kwh }} kWh</div>
                        <div class="kpi-note">Across selected devices</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Peak Demand</div>
                        <div class="kpi-value">{% if peak_demand_kw is not none %}{{ peak_demand_kw }} kW{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if peak_timestamp and peak_timestamp != "N/A" %}{{ peak_timestamp }}{% else %}Peak timestamp unavailable{% endif %}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Load Factor</div>
                        <div class="kpi-value">{% if load_factor_pct is not none %}{{ load_factor_pct }}%{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if average_load_kw is not none %}Avg load {{ average_load_kw }} kW{% else %}Average load unavailable{% endif %}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Total Cost</div>
                        <div class="kpi-value">{% if total_cost is not none %}{{ currency }} {{ total_cost }}{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if tariff_rate_used is not none %}{{ currency }} {{ tariff_rate_used }} / kWh{% else %}Cost estimation skipped{% endif %}</div>
                    </div>
                </div>

                {% if co2_overview and co2_overview.available %}
                <div class="kpi-grid" style="margin-top: 10px;">
                    <div class="kpi-card" style="width: 50%;">
                        <div class="kpi-label">Total CO₂</div>
                        <div class="kpi-value">{{ co2_overview.total_co2_kg }} kg</div>
                        <div class="kpi-note">{% if co2_overview.factor %}{{ co2_overview.factor.value }} {{ co2_overview.factor_unit_display }}{% else %}Factor metadata unavailable{% endif %}</div>
                    </div>
                    <div class="kpi-card" style="width: 50%;">
                        <div class="kpi-label">Off-Shift CO₂</div>
                        <div class="kpi-value">{% if co2_overview.off_shift_available and co2_overview.off_shift_co2_kg is not none %}{{ co2_overview.off_shift_co2_kg }} kg{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if co2_overview.off_shift_available %}CO₂ from energy outside configured shift hours{% else %}Shift configuration not available{% endif %}</div>
                    </div>
                </div>
                <div class="notice" style="margin-top: 6px;">CO₂ estimated using emission factor {{ co2_overview.factor.value }} {{ co2_overview.factor_unit_display }}{% if co2_overview.factor_source_display %} ({{ co2_overview.factor_source_display }}){% endif %}.</div>
                {% elif co2_overview and not co2_overview.available %}
                <div class="notice" style="margin-top: 10px;">CO₂ emissions estimation is unavailable because an emission factor has not been configured for this organisation.</div>
                {% endif %}

                <div class="summary-strip">
                    <div class="summary-item"><strong>Average Load</strong>{% if average_load_kw is not none %}{{ average_load_kw }} kW{% else %}N/A{% endif %}</div>
                    <div class="summary-item"><strong>Devices</strong>{{ per_device|length }} device{% if per_device|length != 1 %}s{% endif %}</div>
                    <div class="summary-item"><strong>Tariff Snapshot</strong>{% if tariff_fetched_at and tariff_fetched_at != "N/A" %}{{ tariff_fetched_at }}{% else %}Not recorded{% endif %}</div>
                </div>

                <div class="spotlight-card" style="margin-top: 10px;">
                    <div class="spotlight-label">Primary Cost Driver</div>
                    <div class="spotlight-value">{% if total_cost is not none %}{{ currency }} {{ total_cost }}{% else %}Tariff missing{% endif %}</div>
                    <div class="spotlight-note">
                        {% if tariff_rate_used is not none %}
                        Cost estimate based on {{ currency }} {{ tariff_rate_used }} per kWh for the selected period.
                        {% else %}
                        Configure a tariff to unlock financial calculations in future reports.
                        {% endif %}
                    </div>
                </div>

                {% if overall_quality != "high" %}
                <div class="notice">Some values are estimated because telemetry coverage was incomplete. The detailed sections below call out the affected devices and days.</div>
                {% endif %}
                {% if peak_timestamp and peak_timestamp != "N/A" %}
                <div class="notice">Peak demand timestamp: {{ peak_timestamp }}</div>
                {% endif %}
            </div>

            <div class="section">
                <div class="section-kicker">Performance Shape</div>
                <div class="section-title-row">
                    <h2>Trend and Energy Share</h2>
                    <div class="section-subtitle">Daily pattern and device distribution</div>
                </div>
                <p class="section-intro">These charts show how energy moved across the reporting period and how the selected devices contributed to the total load.</p>
                <div class="chart-row">
                    <div class="chart-col">
                        <div class="chart-card">
                            <div class="chart-title">Daily Energy Pattern</div>
                            {% if charts.daily_energy %}
                            <img src="{{ charts.daily_energy }}" alt="Daily Energy Chart" />
                            {% else %}
                            <div class="muted">No daily series available.</div>
                            {% endif %}
                        </div>
                    </div>
                    <div class="chart-col">
                        <div class="chart-card">
                            <div class="chart-title">Device Consumption Share</div>
                            {% if charts.device_share %}
                            <img src="{{ charts.device_share }}" alt="Device Energy Share" />
                            {% else %}
                            <div class="muted">No device breakdown available.</div>
                            {% endif %}
                        </div>
                    </div>
                </div>
            </div>

            {% if per_device %}
            <div class="section">
                <div class="section-kicker">Operational Detail</div>
                <div class="section-title-row">
                    <h2>Device Breakdown</h2>
                    <div class="section-subtitle">Per-device energy, demand, cost, and confidence</div>
                </div>
                <p class="section-intro">Review device totals, peak demand, cost, and quality grading to validate the result set before acting on anomalies.</p>
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>Device</th>
                                <th>Total kWh</th>
                                <th>Peak kW</th>
                                <th>Load Factor</th>
                                <th>Quality</th>
                                {% if co2_overview and co2_overview.available %}
                                <th>CO₂ (kg)</th>
                                {% endif %}
                                <th class="right">Cost</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for d in per_device %}
                            <tr>
                                <td>{{ d.device_name }}</td>
                                <td class="numeric">{{ d.total_kwh if d.total_kwh is not none else "N/A" }}</td>
                                <td class="numeric">{{ d.peak_demand_kw if d.peak_demand_kw is not none else "N/A" }}</td>
                                <td class="numeric">{{ d.load_factor_pct if d.load_factor_pct is not none else "N/A" }}</td>
                                <td>
                                    <span class="badge {% if d.quality == 'high' %}badge-good{% elif d.quality == 'medium' %}badge-medium{% elif d.quality == 'low' %}badge-low{% else %}badge-muted{% endif %}">
                                        {{ d.quality }}
                                    </span>
                                </td>
                                {% if co2_overview and co2_overview.available %}
                                <td class="numeric">{{ d.co2_kg if d.co2_kg is not none else "N/A" }}</td>
                                {% endif %}
                                <td class="financial">{% if d.total_cost is not none %}{{ currency }} {{ d.total_cost }}{% else %}N/A{% endif %}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            {% endif %}

            {% if daily_series %}
            <div class="section">
                <div class="section-kicker">Day-Level View</div>
                <div class="section-title-row">
                    <h2>Daily Energy Breakdown</h2>
                    <div class="section-subtitle">Aggregated by day across all devices</div>
                </div>
                <p class="section-intro">Daily totals help isolate abnormal demand days, energy spikes, and operational drift across the selected range.</p>
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th class="right">Energy (kWh)</th>
                                <th class="right">Cost</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for day in daily_series %}
                            <tr>
                                <td>{{ day.date }}</td>
                                <td class="financial">{{ day.kwh }}</td>
                                <td class="financial">{% if day.cost is not none %}{{ currency }} {{ day.cost }}{% else %}N/A{% endif %}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            {% endif %}

            {% if overtime_summary %}
            <div class="section">
                <div class="section-kicker">Schedule Exposure</div>
                <div class="section-title-row">
                    <h2>Overtime Breakdown</h2>
                    <div class="section-subtitle">Same as off-hours running: all running outside configured shift hours</div>
                </div>
                <p class="section-intro">Overtime is the same operational metric as off-hours running in waste analysis: all running energy outside approved shift windows for the selected period.</p>
                <div class="kpi-grid">
                    <div class="kpi-card">
                        <div class="kpi-label">Total Overtime</div>
                        <div class="kpi-value">{% if overtime_summary.total_minutes is not none %}{{ overtime_summary.total_minutes }} min{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if overtime_summary.total_hours is not none %}{{ overtime_summary.total_hours }} hours{% else %}Duration unavailable{% endif %}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Overtime Energy</div>
                        <div class="kpi-value">{% if overtime_summary.total_kwh is not none %}{{ overtime_summary.total_kwh }} kWh{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">Energy consumed outside shift hours</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Overtime Cost</div>
                        <div class="kpi-value">{% if overtime_summary.total_cost is not none %}{{ overtime_summary.currency }} {{ overtime_summary.total_cost }}{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if overtime_summary.tariff_rate_used is not none %}{{ overtime_summary.currency }} {{ overtime_summary.tariff_rate_used }} / kWh{% else %}Tariff not configured{% endif %}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Configured Devices</div>
                        <div class="kpi-value">{{ overtime_summary.configured_devices }} / {{ overtime_summary.device_count }}</div>
                        <div class="kpi-note">{% if overtime_summary.devices_without_shift > 0 %}{{ overtime_summary.devices_without_shift }} device(s) excluded{% else %}All devices had active shifts{% endif %}</div>
                    </div>
                </div>

                <div class="summary-strip">
                    <div class="summary-item"><strong>Shift Coverage</strong>{{ overtime_summary.configured_devices }} device{% if overtime_summary.configured_devices != 1 %}s{% endif %} configured</div>
                    <div class="summary-item"><strong>Charge Basis</strong>Outside configured shift hours</div>
                    <div class="summary-item"><strong>Tariff Snapshot</strong>{% if overtime_summary.tariff_rate_used is not none %}{{ overtime_summary.currency }} {{ overtime_summary.tariff_rate_used }} / kWh{% else %}Not configured{% endif %}</div>
                    <div class="summary-item"><strong>Overtime Cost</strong>{% if overtime_summary.total_cost is not none %}{{ overtime_summary.currency }} {{ overtime_summary.total_cost }}{% else %}N/A{% endif %}</div>
                </div>

                <div class="notice">Each overtime proof row below shows the exact outside-shift window in platform local time so teams can audit when running happened, not just how much was counted.</div>

                {% if overtime_summary.device_summary %}
                <div class="table-wrap overtime-table" style="margin-top: 10px;">
                    <table>
                        <thead>
                            <tr>
                                <th>Device</th>
                                <th>Shift Status</th>
                                <th class="align-right">Minutes</th>
                                <th class="align-right">Hours</th>
                                <th class="align-right">kWh</th>
                                <th class="align-right">Cost</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for item in overtime_summary.device_summary %}
                            <tr class="{% if item.configured %}zero-row{% else %}overtime-row{% endif %}">
                                <td>{{ item.device_name }}</td>
                                <td>
                                    <span class="badge {% if item.configured %}badge-success-soft{% else %}badge-danger{% endif %}">
                                        {% if item.configured %}Configured{% else %}Missing shift{% endif %}
                                    </span>
                                </td>
                                <td class="align-right">{{ item.total_overtime_minutes }}</td>
                                <td class="align-right">{{ item.total_overtime_hours }}</td>
                                <td class="align-right">{{ item.total_overtime_kwh }}</td>
                                <td class="financial">{% if item.total_overtime_cost is not none %}{{ item.currency }} {{ item.total_overtime_cost }}{% else %}N/A{% endif %}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% endif %}

                {% if overtime_summary.rows %}
                <div class="table-wrap overtime-table" style="margin-top: 10px;">
                    <table>
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Device</th>
                                <th>Shift Status</th>
                                <th>From</th>
                                <th>To</th>
                                <th class="align-right">Minutes</th>
                                <th class="align-right">Hours</th>
                                <th class="align-right">kWh</th>
                                <th class="align-right">Cost</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for row in overtime_summary.rows %}
                            <tr class="{% if row.overtime_minutes and row.overtime_minutes > 0 %}overtime-row{% else %}zero-row{% endif %}">
                                <td>{{ row.date }}</td>
                                <td>{{ row.device_name }}</td>
                                <td>
                                    <span class="badge {% if row.overtime_minutes and row.overtime_minutes > 0 %}badge-danger{% else %}badge-success-soft{% endif %}">
                                        {{ row.shift_status if row.shift_status else ("Overtime" if row.overtime_minutes and row.overtime_minutes > 0 else "Within shift") }}
                                    </span>
                                </td>
                                <td>{{ row.window_start if row.window_start else "N/A" }}</td>
                                <td>{{ row.window_end if row.window_end else "N/A" }}</td>
                                <td class="align-right">{{ row.overtime_minutes }}</td>
                                <td class="align-right">{{ row.overtime_hours }}</td>
                                <td class="align-right">{{ row.overtime_kwh }}</td>
                                <td class="financial">{% if row.overtime_cost is not none %}{{ overtime_summary.currency }} {{ row.overtime_cost }}{% else %}N/A{% endif %}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="notice">No overtime was detected for the selected range.</div>
                {% endif %}

                {% if overtime_summary.devices_without_shift > 0 %}
                <div class="warning">{{ overtime_summary.devices_without_shift }} device(s) had no active shift configuration and were excluded from overtime charging.</div>
                {% endif %}
            </div>
            {% endif %}

            {% if hidden_overconsumption_insight %}
            <div class="section">
                <div class="section-kicker">Advanced Insight</div>
                <div class="section-title-row">
                    <h2>Hidden Overconsumption Insight (P75 Baseline)</h2>
                    <div class="section-subtitle">Energy above daily baseline expectation</div>
                </div>
                <div class="kpi-grid">
                    <div class="kpi-card">
                        <div class="kpi-label">Total Hidden Overconsumption</div>
                        <div class="kpi-value">{% if hidden_overconsumption_insight.summary and hidden_overconsumption_insight.summary.total_hidden_overconsumption_kwh is not none %}{{ hidden_overconsumption_insight.summary.total_hidden_overconsumption_kwh }} kWh{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">Only counted above baseline</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Hidden Overconsumption Cost</div>
                        <div class="kpi-value">{% if hidden_overconsumption_insight.summary and hidden_overconsumption_insight.summary.total_hidden_overconsumption_cost is not none %}{{ currency }} {{ hidden_overconsumption_insight.summary.total_hidden_overconsumption_cost }}{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if hidden_overconsumption_insight.summary and hidden_overconsumption_insight.summary.tariff_rate_used is not none %}{{ currency }} {{ hidden_overconsumption_insight.summary.tariff_rate_used }} / kWh{% else %}Tariff unavailable{% endif %}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Total Baseline Energy</div>
                        <div class="kpi-value">{% if hidden_overconsumption_insight.summary and hidden_overconsumption_insight.summary.total_baseline_energy_kwh is not none %}{{ hidden_overconsumption_insight.summary.total_baseline_energy_kwh }} kWh{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">Expected from daily P75 baseline</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Aggregate P75 Baseline</div>
                        <div class="kpi-value">{% if hidden_overconsumption_insight.summary and hidden_overconsumption_insight.summary.aggregate_p75_baseline_reference is not none %}{{ hidden_overconsumption_insight.summary.aggregate_p75_baseline_reference }} W{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if hidden_overconsumption_insight.summary and hidden_overconsumption_insight.summary.selected_days is not none %}{{ hidden_overconsumption_insight.summary.selected_days }} selected day{% if hidden_overconsumption_insight.summary.selected_days != 1 %}s{% endif %}{% else %}Selected days unavailable{% endif %}</div>
                    </div>
                </div>

                {% set hidden_usable = namespace(count=0) %}
                {% for row in hidden_overconsumption_insight.daily_breakdown or [] %}
                    {% if row.p75_power_baseline_w is not none and row.baseline_energy_kwh is not none %}
                        {% set hidden_usable.count = hidden_usable.count + 1 %}
                    {% endif %}
                {% endfor %}

                {% if hidden_usable.count > 0 %}
                <div class="hidden-record-list">
                    {% for row in hidden_overconsumption_insight.daily_breakdown or [] %}
                    {% if row.p75_power_baseline_w is not none and row.baseline_energy_kwh is not none %}
                    {% set diff_kwh = row.actual_energy_kwh - row.baseline_energy_kwh %}
                    <div class="hidden-record-card">
                        <table class="hidden-record-grid">
                            <tbody>
                                <tr>
                                    <td>
                                        <span class="hidden-record-label">Date</span>
                                        <span class="hidden-record-value">{{ row.date }}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Actual Energy (kWh)</span>
                                        <span class="hidden-record-value">{% if row.actual_energy_kwh is not none %}{{ row.actual_energy_kwh }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">P75 Baseline Power (W)</span>
                                        <span class="hidden-record-value">{{ row.p75_power_baseline_w }} W</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Baseline Energy (kWh)</span>
                                        <span class="hidden-record-value">{{ row.baseline_energy_kwh }}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Difference vs Baseline (kWh)</span>
                                        <span class="hidden-record-value">{% if diff_kwh > 0 %}+{% endif %}{{ "%.4f"|format(diff_kwh) }}</span>
                                    </td>
                                </tr>
                                <tr>
                                    <td>
                                        <span class="hidden-record-label">Status</span>
                                        <span class="hidden-record-value">
                                            {% if diff_kwh > 0 %}
                                            <span class="badge badge-danger">Above Baseline</span>
                                            {% elif diff_kwh < 0 %}
                                            <span class="badge badge-success-soft">Below Baseline</span>
                                            {% else %}
                                            <span class="badge badge-muted">Within Baseline</span>
                                            {% endif %}
                                        </span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Hidden Overconsumption (kWh)</span>
                                        <span class="hidden-record-value">{% if row.hidden_overconsumption_kwh is not none %}{{ row.hidden_overconsumption_kwh }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Hidden Overconsumption Cost</span>
                                        <span class="hidden-record-value">{% if row.hidden_overconsumption_cost is not none %}{{ currency }} {{ row.hidden_overconsumption_cost }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Sample Count</span>
                                        <span class="hidden-record-value">{% if row.sample_count is not none %}{{ row.sample_count }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Covered Duration (hours)</span>
                                        <span class="hidden-record-value">{% if row.covered_duration_hours is not none %}{{ row.covered_duration_hours }}{% else %}N/A{% endif %}</span>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    {% endif %}
                    {% endfor %}
                </div>
                {% else %}
                <div class="notice">Hidden overconsumption insight is unavailable for this selection due to insufficient telemetry.</div>
                {% endif %}
            </div>
            {% endif %}

            {% if hidden_overconsumption_insight %}
            <div class="section">
                <div class="section-kicker">Machine Detail</div>
                <div class="section-title-row">
                    <h2>Hidden Overconsumption by Device</h2>
                    <div class="section-subtitle">Machine-wise contribution to hidden overconsumption</div>
                </div>

                {% set hidden_device_usable = namespace(count=0) %}
                {% for row in hidden_overconsumption_insight.device_breakdown or [] %}
                    {% if row.p75_power_baseline_w is not none and row.baseline_energy_kwh is not none %}
                        {% set hidden_device_usable.count = hidden_device_usable.count + 1 %}
                    {% endif %}
                {% endfor %}

                {% if hidden_device_usable.count > 0 %}
                <div class="hidden-record-list">
                    {% for row in hidden_overconsumption_insight.device_breakdown or [] %}
                    {% if row.p75_power_baseline_w is not none and row.baseline_energy_kwh is not none %}
                    {% set device_label = row.device_name if row.device_name else row.device_id %}
                    <div class="hidden-record-card">
                        <table class="hidden-record-grid">
                            <tbody>
                                <tr>
                                    <td>
                                        <span class="hidden-record-label">Date</span>
                                        <span class="hidden-record-value">{{ row.date }}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Device Name</span>
                                        <span class="hidden-record-value">{{ device_label if device_label else "N/A" }}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Device ID</span>
                                        <span class="hidden-record-value">{{ row.device_id if row.device_id else "N/A" }}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Actual Energy (kWh)</span>
                                        <span class="hidden-record-value">{% if row.actual_energy_kwh is not none %}{{ row.actual_energy_kwh }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">P75 Baseline Power (W)</span>
                                        <span class="hidden-record-value">{{ row.p75_power_baseline_w }} W</span>
                                    </td>
                                </tr>
                                <tr>
                                    <td>
                                        <span class="hidden-record-label">Baseline Energy (kWh)</span>
                                        <span class="hidden-record-value">{% if row.baseline_energy_kwh is not none %}{{ row.baseline_energy_kwh }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Difference vs Baseline (kWh)</span>
                                        <span class="hidden-record-value">{% if row.difference_vs_baseline_kwh is not none %}{% if row.difference_vs_baseline_kwh > 0 %}+{% endif %}{{ "%.4f"|format(row.difference_vs_baseline_kwh) }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Status</span>
                                        <span class="hidden-record-value">
                                            {% if row.status == "Above Baseline" %}
                                            <span class="badge badge-danger">Above Baseline</span>
                                            {% elif row.status == "Below Baseline" %}
                                            <span class="badge badge-success-soft">Below Baseline</span>
                                            {% elif row.status == "Within Baseline" %}
                                            <span class="badge badge-muted">Within Baseline</span>
                                            {% else %}
                                            <span class="badge badge-muted">Unavailable</span>
                                            {% endif %}
                                        </span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Hidden Overconsumption (kWh)</span>
                                        <span class="hidden-record-value">{% if row.hidden_overconsumption_kwh is not none %}{{ row.hidden_overconsumption_kwh }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Hidden Overconsumption Cost</span>
                                        <span class="hidden-record-value">{% if row.hidden_overconsumption_cost is not none %}{{ currency }} {{ row.hidden_overconsumption_cost }}{% else %}N/A{% endif %}</span>
                                    </td>
                                </tr>
                                <tr>
                                    <td>
                                        <span class="hidden-record-label">Sample Count</span>
                                        <span class="hidden-record-value">{% if row.sample_count is not none %}{{ row.sample_count }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td>
                                        <span class="hidden-record-label">Covered Duration (hours)</span>
                                        <span class="hidden-record-value">{% if row.covered_duration_hours is not none %}{{ row.covered_duration_hours }}{% else %}N/A{% endif %}</span>
                                    </td>
                                    <td colspan="3"></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    {% endif %}
                    {% endfor %}
                </div>
                {% else %}
                <div class="notice">Device-wise hidden overconsumption breakdown is unavailable for this selection.</div>
                {% endif %}
            </div>
            {% endif %}

            {% if insights %}
            <div class="section">
                <div class="section-kicker">Decision Support</div>
                <div class="section-title-row">
                    <h2>Key Insights</h2>
                    <div class="section-subtitle">Highlights for quick review</div>
                </div>
                <p class="section-intro">Use these prioritized observations to guide investigations, follow-up reviews, and operational actions.</p>
                <ul class="insight-list">
                    {% for insight in insights %}
                    <li>{{ loop.index }}. {{ insight }}</li>
                    {% endfor %}
                </ul>
            </div>
            {% endif %}

            <div class="footer">
                <strong>Shivex</strong> professional energy report generated for {{ device_label }}.
            </div>
        </div>
    </div>
</body>
</html>
"""


def get_comparison_report_template():
    return """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Energy Comparison Report</title>
    """ + _report_styles() + """
</head>
<body class="{{ report_theme_class }}">
    <div class="page">
        <div class="hero">
            <div class="hero-topbar">
                <div class="hero-brand">
                    <div class="brand-kicker">Shivex Energy Intelligence</div>
                    <div class="brand-name">Comparison Report</div>
                </div>
                <div class="hero-stamp">
                    <div>
                        <div class="stamp-label">Generated</div>
                        <div class="stamp-value">{{ generated_at }}</div>
                    </div>
                </div>
            </div>
            <h1>Energy Comparison Report</h1>
            <div class="hero-subtitle">
                A decision-ready comparison of energy usage, demand, and efficiency outcomes across two devices in the same reporting window.
            </div>
            <div class="hero-caption">Comparing: {{ device_a_name }} vs {{ device_b_name }} | Period: {{ start_date }} to {{ end_date }}</div>
            <div class="hero-meta">
                <div class="meta-chip"><div><span class="meta-label">Report ID</span><span class="meta-value">{{ report_id }}</span></div></div>
                <div class="meta-chip"><div><span class="meta-label">Winner</span><span class="meta-value">{% if winner %}{{ winner }}{% else %}Pending{% endif %}</span></div></div>
                <div class="meta-chip"><div><span class="meta-label">Scope</span><span class="meta-value">{{ device_a_name }} vs {{ device_b_name }}</span></div></div>
                <div class="meta-chip"><div><span class="meta-label">Insight Count</span><span class="meta-value">{{ insights|length }}</span></div></div>
            </div>
        </div>

        <div class="content">
            <div class="section">
                <div class="section-kicker">Executive Overview</div>
                <div class="section-title-row">
                    <h2>Executive Summary</h2>
                    <div class="section-subtitle">High-level comparison at a glance</div>
                </div>
                <p class="section-intro">This summary highlights the spread between the two devices and frames the operational decision with the clearest top-line metrics first.</p>
                <div class="kpi-grid">
                    <div class="kpi-card">
                        <div class="kpi-label">Energy Difference</div>
                        <div class="kpi-value">{% if comparison.energy_comparison %}{{ comparison.energy_comparison.difference_kwh }} kWh{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if comparison.energy_comparison %}{{ comparison.energy_comparison.difference_percent }}% spread{% else %}Energy comparison unavailable{% endif %}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Demand Difference</div>
                        <div class="kpi-value">{% if comparison.demand_comparison %}{{ comparison.demand_comparison.difference_kw }} kW{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">{% if comparison.demand_comparison %}{{ comparison.demand_comparison.difference_percent }}% spread{% else %}Demand comparison unavailable{% endif %}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Higher Consumer</div>
                        <div class="kpi-value">{% if comparison.energy_comparison %}{{ comparison.energy_comparison.higher_consumer }}{% else %}N/A{% endif %}</div>
                        <div class="kpi-note">Based on total energy usage</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">More Efficient</div>
                        <div class="kpi-value">{% if winner %}{{ winner }}{% else %}Pending{% endif %}</div>
                        <div class="kpi-note">Current analysis outcome</div>
                    </div>
                </div>

                <div class="two-col" style="margin-top: 10px;">
                    <div class="two-col-cell">
                        <div class="spotlight-card">
                            <div class="spotlight-label">Recommended Winner</div>
                            <div class="spotlight-value">{% if winner %}{{ winner }}{% else %}Pending{% endif %}</div>
                            <div class="spotlight-note">Selected from the current analysis based on the available comparison metrics.</div>
                        </div>
                    </div>
                    <div class="two-col-cell">
                        <div class="callout-card">
                            <h3>How To Use This Comparison</h3>
                            <p>Read the summary cards first, then validate the chart and numeric tables to confirm whether the decision aligns with your operational context.</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="section">
                <div class="section-kicker">Visual Comparison</div>
                <div class="section-title-row">
                    <h2>Comparison Chart</h2>
                    <div class="section-subtitle">Visual summary of the metrics used</div>
                </div>
                <p class="section-intro">The visual comparison makes it easy to spot which device is carrying more energy or demand load without scanning the tables first.</p>
                <div class="chart-card">
                    <div class="chart-title">Comparison Overview</div>
                    {% if comparison_chart %}
                    <img src="{{ comparison_chart }}" alt="Comparison chart" />
                    {% else %}
                    <div class="muted">No comparison chart available.</div>
                    {% endif %}
                </div>
            </div>

            <div class="section">
                <div class="section-kicker">Numeric Detail</div>
                <div class="section-title-row">
                    <h2>Energy and Demand Details</h2>
                    <div class="section-subtitle">Device-by-device numeric breakdown</div>
                </div>
                <p class="section-intro">These tables preserve the raw comparison values behind the headline call so reviewers can verify the final recommendation.</p>
                <div class="chart-row">
                    <div class="chart-col">
                        <div class="table-wrap">
                            <table>
                                <thead><tr><th>Energy Comparison</th><th class="align-right">kWh</th></tr></thead>
                                <tbody>
                                    <tr><td>{{ device_a_name }}</td><td class="financial">{% if comparison.energy_comparison %}{{ comparison.energy_comparison.device_a_kwh }}{% else %}N/A{% endif %}</td></tr>
                                    <tr><td>{{ device_b_name }}</td><td class="financial">{% if comparison.energy_comparison %}{{ comparison.energy_comparison.device_b_kwh }}{% else %}N/A{% endif %}</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                    <div class="chart-col">
                        <div class="table-wrap">
                            <table>
                                <thead><tr><th>Demand Comparison</th><th class="align-right">kW</th></tr></thead>
                                <tbody>
                                    <tr><td>{{ device_a_name }}</td><td class="financial">{% if comparison.demand_comparison %}{{ comparison.demand_comparison.device_a_peak_kw }}{% else %}N/A{% endif %}</td></tr>
                                    <tr><td>{{ device_b_name }}</td><td class="financial">{% if comparison.demand_comparison %}{{ comparison.demand_comparison.device_b_peak_kw }}{% else %}N/A{% endif %}</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                {% if comparison.energy_comparison %}
                <div class="notice">Energy difference: {{ comparison.energy_comparison.difference_kwh }} kWh ({{ comparison.energy_comparison.difference_percent }}%). Higher consumer: {{ comparison.energy_comparison.higher_consumer }}.</div>
                {% endif %}
                {% if comparison.demand_comparison %}
                <div class="notice">Demand difference: {{ comparison.demand_comparison.difference_kw }} kW ({{ comparison.demand_comparison.difference_percent }}%). Higher demand: {{ comparison.demand_comparison.higher_demand }}.</div>
                {% endif %}
            </div>

            {% if insights %}
            <div class="section">
                <div class="section-kicker">Decision Support</div>
                <div class="section-title-row">
                    <h2>Key Insights</h2>
                    <div class="section-subtitle">Interpretive takeaways for the reader</div>
                </div>
                <p class="section-intro">These insights summarize the comparison in human terms so reviewers can quickly align on the operational takeaway.</p>
                <ul class="insight-list">
                    {% for insight in insights %}
                    <li>{{ loop.index }}. {{ insight }}</li>
                    {% endfor %}
                </ul>
            </div>
            {% endif %}

            {% if winner %}
            <div class="section">
                <div class="section-kicker">Decision Summary</div>
                <div class="section-title-row">
                    <h2>Winner</h2>
                    <div class="section-subtitle">Decision summary</div>
                </div>
                <div class="notice"><strong>{{ winner }}</strong> is the more efficient choice based on the analysis.</div>
            </div>
            {% endif %}

            <div class="footer">
                <strong>Shivex</strong> professional comparison report for {{ device_a_name }} and {{ device_b_name }}.
            </div>
        </div>
    </div>
</body>
</html>
"""


pdf_builder = type(
    "PDFBuilder",
    (),
    {
        "generate_consumption_pdf": staticmethod(generate_consumption_pdf),
        "generate_comparison_pdf": staticmethod(generate_comparison_pdf),
        "async_generate_consumption_pdf": staticmethod(async_generate_consumption_pdf),
        "async_generate_comparison_pdf": staticmethod(async_generate_comparison_pdf),
    },
)()
