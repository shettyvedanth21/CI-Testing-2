import asyncio
from decimal import Decimal

from src.ai.copilot_engine import CopilotEngine
from src.response.schema import Chart, ChartDataset


class _NonJsonModelClient:
    async def generate(self, messages, max_tokens=1000):
        return "this is not valid json"


def test_build_chart_from_rows_with_decimal_values():
    chart, reason, points = CopilotEngine._build_chart_from_rows(
        columns=["device_id", "device_name", "idle_cost"],
        rows=[
            ["COMPRESSOR-003", "Compressor 003", Decimal("13.7335")],
            ["COMPRESSOR-002", "Compressor 002", Decimal("11.1031")],
        ],
        chart_type="bar",
        title="Idle Cost Today by Machine",
        x_key="device_id",
        y_key="idle_cost",
    )

    assert reason is None
    assert points == 2
    assert chart is not None
    assert chart.datasets[0].data[0] > 0
    assert chart.datasets[0].data[1] > 0


def test_build_chart_uses_mapped_numeric_column_not_index_1():
    chart, reason, points = CopilotEngine._build_chart_from_rows(
        columns=["device_id", "device_name", "idle_cost"],
        rows=[
            ["COMPRESSOR-003", "Compressor 003", "13.7335"],
            ["COMPRESSOR-002", "Compressor 002", "11.1031"],
        ],
        chart_type="bar",
        title="Idle Cost Today by Machine",
        x_key="device_id",
        y_key="idle_cost",
    )

    assert reason is None
    assert points == 2
    assert chart is not None
    assert chart.datasets[0].data == [13.7335, 11.1031]


def test_build_chart_returns_none_for_non_numeric_series():
    chart, reason, points = CopilotEngine._build_chart_from_rows(
        columns=["device_id", "device_name"],
        rows=[
            ["COMPRESSOR-003", "Compressor 003"],
            ["COMPRESSOR-002", "Compressor 002"],
        ],
        chart_type="bar",
        title="Invalid",
        x_key="device_id",
        y_key="device_name",
    )

    assert chart is None
    assert points == 0
    assert reason is not None
    assert "no numeric data" in reason


def test_formatter_parse_failure_keeps_deterministic_chart():
    engine = CopilotEngine(_NonJsonModelClient())
    deterministic_chart = Chart(
        type="bar",
        title="Idle Cost Today by Machine",
        labels=["COMPRESSOR-003", "COMPRESSOR-002"],
        datasets=[ChartDataset(label="idle_cost", data=[13.7335, 11.1031])],
    )
    payload = {
        "columns": ["device_id", "idle_cost"],
        "rows": [["COMPRESSOR-003", Decimal("13.7335")], ["COMPRESSOR-002", Decimal("11.1031")]],
        "row_count": 2,
    }

    response = asyncio.run(
        engine._format_with_ai_or_fallback(
            message="What is today's total idle loss?",
            payload=payload,
            reasoning="Source: ai_factoryops; Window: today; Metric: idle cost.",
            chart_hint="bar",
            default_title="Idle Cost Today by Machine",
            default_followups=["What is today's total idle loss?"],
            tariff_rate=8.5,
            currency="INR",
            force_chart=deterministic_chart,
        )
    )

    assert response.chart is not None
    assert response.chart.datasets[0].data[0] == 13.7335
