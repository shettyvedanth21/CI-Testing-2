from __future__ import annotations

from src.tasks.report_task import run_comparison_report, run_consumption_report


async def execute_report(report_id: str, report_type: str, params: dict) -> None:
    if report_type == "consumption":
        await run_consumption_report(report_id, params)
        return
    if report_type == "comparison":
        await run_comparison_report(report_id, params)
        return
    raise ValueError(f"Unsupported report type: {report_type}")
