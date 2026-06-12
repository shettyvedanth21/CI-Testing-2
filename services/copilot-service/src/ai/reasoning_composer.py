from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.response.schema import ReasoningSections


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _fmt_number(value: Any, digits: int = 3) -> str:
    num = _to_float(value)
    if num is None:
        return "0"
    return f"{num:.{digits}f}".rstrip("0").rstrip(".")


def _safe(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, Decimal):
        return _fmt_number(value, 4)
    return str(value)


def _idx(columns: list[str], name: str) -> int | None:
    wanted = name.strip().lower()
    for i, c in enumerate(columns):
        if str(c).strip().lower() == wanted:
            return i
    return None


@dataclass
class ComposedReasoning:
    answer: str
    sections: ReasoningSections

    @property
    def text(self) -> str:
        return (
            f"What happened: {self.sections.what_happened}\n"
            f"Why it matters: {self.sections.why_it_matters}\n"
            f"How calculated: {self.sections.how_calculated}"
        )


class ReasoningComposer:
    @staticmethod
    def for_query_result(
        intent: str,
        message: str,
        columns: list[str],
        rows: list[list[Any]],
        chart_omission_reason: str | None = None,
    ) -> ComposedReasoning:
        count = len(rows)
        if count == 0:
            sections = ReasoningSections(
                what_happened="No matching records were found for this period.",
                why_it_matters="There is nothing actionable from this time window.",
                how_calculated="Checked the current FactoryOPS data for your selected period.",
            )
            return ComposedReasoning(answer="No data found for this period.", sections=sections)

        if intent == "factory_summary":
            return ReasoningComposer._factory_summary(columns, rows, chart_omission_reason)
        if intent == "idle_waste":
            return ReasoningComposer._idle_waste(columns, rows, chart_omission_reason)
        if intent == "top_energy_today":
            return ReasoningComposer._top_energy(columns, rows, chart_omission_reason)
        if intent == "health_scores":
            return ReasoningComposer._health_scores(columns, rows, chart_omission_reason)

        top = rows[0]
        lead = _safe(top[0]) if top else "the leading machine"
        sections = ReasoningSections(
            what_happened=f"{count} records matched your question. Leading result: {lead}.",
            why_it_matters="This helps you quickly focus on the most important machine or event first.",
            how_calculated="Compared available records from FactoryOPS for the requested period and ranked the results.",
        )
        if chart_omission_reason:
            sections.how_calculated = f"{sections.how_calculated} Chart was skipped: {chart_omission_reason}."
        return ComposedReasoning(
            answer=f"I found {count} matching records and highlighted the top result for quick action.",
            sections=sections,
        )

    @staticmethod
    def for_blocked_query() -> ComposedReasoning:
        sections = ReasoningSections(
            what_happened="This request could not be run safely.",
            why_it_matters="Blocking unsafe query patterns protects your production data and service stability.",
            how_calculated="Applied the SELECT-only safety policy and rejected restricted SQL operations.",
        )
        return ComposedReasoning(
            answer="I couldn’t run that safely. Try asking in a simpler way focused on reports, trends, or machine comparisons.",
            sections=sections,
        )

    @staticmethod
    def for_unsupported_module() -> ComposedReasoning:
        sections = ReasoningSections(
            what_happened="This feature is not available in the current FactoryOPS modules.",
            why_it_matters="I can still help you with energy, idle cost, alerts, and machine performance insights.",
            how_calculated="Checked supported data paths and mapped your request to unavailable scope.",
        )
        return ComposedReasoning(
            answer="That module is not yet available in FactoryOPS.",
            sections=sections,
        )

    @staticmethod
    def _factory_summary(columns: list[str], rows: list[list[Any]], chart_omission_reason: str | None) -> ComposedReasoning:
        name_i = _idx(columns, "device_name")
        kwh_i = _idx(columns, "idle_kwh_today")
        status_i = _idx(columns, "legacy_status")
        top_name = _safe(rows[0][name_i]) if name_i is not None else "Top machine"
        top_kwh = _fmt_number(rows[0][kwh_i], 3) if kwh_i is not None else "0"
        active = 0
        for row in rows:
            if status_i is not None and _safe(row[status_i]).lower() == "active":
                active += 1
        sections = ReasoningSections(
            what_happened=f"Today, {top_name} has the highest idle energy at {top_kwh} kWh.",
            why_it_matters=f"{active}/{len(rows)} machines are active. Reducing idle energy on top contributors can cut avoidable cost.",
            how_calculated="Used today’s device status and idle energy records, then ranked machines by idle kWh.",
        )
        if chart_omission_reason:
            sections.how_calculated = f"{sections.how_calculated} Chart was skipped: {chart_omission_reason}."
        return ComposedReasoning(answer=sections.what_happened, sections=sections)

    @staticmethod
    def _idle_waste(columns: list[str], rows: list[list[Any]], chart_omission_reason: str | None) -> ComposedReasoning:
        name_i = _idx(columns, "device_name")
        cost_i = _idx(columns, "idle_cost")
        curr_i = _idx(columns, "currency")
        top_name = _safe(rows[0][name_i]) if name_i is not None else "Top machine"
        top_cost = _fmt_number(rows[0][cost_i], 2) if cost_i is not None else "0"
        currency = _safe(rows[0][curr_i]) if curr_i is not None else "INR"
        positive = 0
        total = 0.0
        for row in rows:
            if cost_i is None:
                continue
            value = _to_float(row[cost_i]) or 0.0
            if value > 0:
                positive += 1
            total += value
        sections = ReasoningSections(
            what_happened=f"Today, {top_name} has the highest idle cost at {currency} {top_cost}.",
            why_it_matters=f"{positive} machine(s) are currently adding idle cost, totaling about {currency} {_fmt_number(total, 2)}.",
            how_calculated="Used today’s idle duration and idle energy cost records, then ranked machines by idle cost.",
        )
        if chart_omission_reason:
            sections.how_calculated = f"{sections.how_calculated} Chart was skipped: {chart_omission_reason}."
        return ComposedReasoning(answer=sections.what_happened, sections=sections)

    @staticmethod
    def _top_energy(columns: list[str], rows: list[list[Any]], chart_omission_reason: str | None) -> ComposedReasoning:
        machine_i = _idx(columns, "Machine")
        kwh_i = _idx(columns, "kWh")
        cost_i = _idx(columns, "Cost INR")
        top_machine = _safe(rows[0][machine_i]) if machine_i is not None else "Top machine"
        top_kwh = _fmt_number(rows[0][kwh_i], 3) if kwh_i is not None else "0"
        top_cost = _fmt_number(rows[0][cost_i], 2) if cost_i is not None else "0"
        sections = ReasoningSections(
            what_happened=f"Today, {top_machine} consumed the most energy at {top_kwh} kWh (about INR {top_cost}).",
            why_it_matters="This identifies where immediate energy optimization gives the fastest savings.",
            how_calculated="Compared today’s machine-wise telemetry energy and ranked by total kWh.",
        )
        if chart_omission_reason:
            sections.how_calculated = f"{sections.how_calculated} Chart was skipped: {chart_omission_reason}."
        return ComposedReasoning(answer=sections.what_happened, sections=sections)

    @staticmethod
    def _health_scores(columns: list[str], rows: list[list[Any]], chart_omission_reason: str | None) -> ComposedReasoning:
        name_i = _idx(columns, "device_name")
        eff_i = _idx(columns, "efficiency_pct")
        health_i = _idx(columns, "health_score")
        top_machine = _safe(rows[0][name_i]) if name_i is not None else "Top machine"
        efficiency = _fmt_number(rows[0][eff_i], 2) if eff_i is not None else "0"
        health_score = _fmt_number(rows[0][health_i], 2) if health_i is not None else "0"
        sections = ReasoningSections(
            what_happened=f"{top_machine} has the lowest efficiency today at {efficiency}%.",
            why_it_matters="This identifies the machine most in need of operational attention first.",
            how_calculated=(
                "Compared today’s live device efficiency values and ranked machines from lowest to highest. "
                "When uptime percentage was unavailable, the current health score was used as the efficiency proxy."
            ),
        )
        if chart_omission_reason:
            sections.how_calculated = f"{sections.how_calculated} Chart was skipped: {chart_omission_reason}."
        return ComposedReasoning(
            answer=f"{top_machine} has the lowest efficiency today at {efficiency}% (health score {health_score}).",
            sections=sections,
        )
