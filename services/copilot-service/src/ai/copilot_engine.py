import json
import logging
import re
from collections import Counter
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from src.ai.model_client import AIUnavailableError, ModelClient
from src.ai.prompt_templates import FORMATTER_SYSTEM_PROMPT, SQL_SYSTEM_PROMPT
from src.ai.reasoning_composer import ReasoningComposer
from src.config import settings
from src.database import get_db_session, get_readonly_db_session
from src.db.query_engine import QueryEngine, QueryResult
from src.db.schema_loader import get_schema_context
from src.integrations.data_service_client import DataServiceClient
from src.intent.router import classify_intent
from src.response.schema import Chart, ChartDataset, CopilotResponse, CuratedContext, DataTable, PageLink, ReasoningSections
from src.templates.curated_catalog import CuratedQuestion, get_curated_question_by_id, get_follow_up_texts, match_curated_question


DEVICE_ID_PATTERN = re.compile(r"\b(?=[A-Z0-9_-]*\d)[A-Z0-9_-]{3,}\b")
logger = logging.getLogger(__name__)
COPILOT_METRICS = Counter()
DEFAULT_CURATED_FOLLOWUP_IDS = ("factory_summary", "top_energy_today", "total_idle_loss_today")


class CopilotEngine:
    def __init__(self, model_client: ModelClient | None):
        self.model_client = model_client
        self.query_engine = QueryEngine()
        self.data_service = DataServiceClient()

    async def process_question(
        self,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None = None,
    ) -> CopilotResponse:
        curated_question = match_curated_question(message)
        if curated_question is not None:
            return await self._run_curated_question(
                question=curated_question,
                message=message,
                history=history,
                tariff_rate=tariff_rate,
                currency=currency,
                tenant_id=tenant_id,
                curated_context=curated_context,
            )

        intent = classify_intent(message, history)

        if intent.intent == "unsupported":
            if not self._can_use_ai_sql():
                return self._approved_questions_only_response()
            unsupported = ReasoningComposer.for_unsupported_module()
            return CopilotResponse(
                answer=unsupported.answer,
                reasoning=unsupported.text,
                reasoning_sections=unsupported.sections,
                follow_up_suggestions=self._followup_texts(DEFAULT_CURATED_FOLLOWUP_IDS),
                error_code="MODULE_NOT_AVAILABLE",
            )

        if intent.intent == "telemetry_trend":
            trend_question = get_curated_question_by_id("device_power_trend_last_7_days")
            return await self._run_curated_question(
                question=trend_question,
                message=message,
                history=history,
                tariff_rate=tariff_rate,
                currency=currency,
                tenant_id=tenant_id,
                curated_context=curated_context,
            )

        if not self._can_use_ai_sql():
            return self._approved_questions_only_response()

        return await self._run_ai_sql(
            message=message,
            history=history,
            tariff_rate=tariff_rate,
            currency=currency,
            tenant_id=tenant_id,
        )

    async def _run_curated_question(
        self,
        question: CuratedQuestion | None,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        if question is None:
            return self._blocked_response(DEFAULT_CURATED_FOLLOWUP_IDS)

        handlers = {
            "factory_summary": self._answer_factory_summary,
            "top_energy_today": self._answer_top_energy_today,
            "health_scores": self._answer_health_scores,
            "total_energy_cost_today": self._answer_total_energy_cost_today,
            "highest_loss_machine_today": self._answer_highest_loss_machine_today,
            "total_idle_loss_today": self._answer_total_idle_loss_today,
            "top_5_energy_machines_today": self._answer_top_5_energy_machines_today,
            "top_5_idle_cost_machines_today": self._answer_top_5_idle_cost_machines_today,
            "device_power_trend_last_7_days": self._answer_device_power_trend_last_7_days,
            "alerts_triggered_count_today": self._answer_alerts_triggered_count_today,
            "top_plant_energy_today": self._answer_top_plant_energy_today,
            "top_plant_idle_loss_today": self._answer_top_plant_idle_loss_today,
            "runtime_summary_today": self._answer_runtime_summary_today,
        }
        handler = handlers.get(question.handler)
        if handler is None:
            return self._blocked_response(DEFAULT_CURATED_FOLLOWUP_IDS)
        return await handler(
            question=question,
            message=message,
            history=history,
            tariff_rate=tariff_rate,
            currency=currency,
            tenant_id=tenant_id,
            curated_context=curated_context,
        )

    async def _execute_curated_query(
        self,
        sql: str,
        tenant_id: str,
        params: dict[str, Any] | None = None,
    ) -> QueryResult:
        valid, reason = self.query_engine.validate_sql(sql)
        if not valid:
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_BLOCKED", reason=reason)

        try:
            safe_sql = self.query_engine._inject_tenant_filter(sql)
        except ValueError as exc:
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_BLOCKED", reason=str(exc))

        query_params: dict[str, Any] = dict(params or {})
        if ":tenant_id" in safe_sql:
            query_params["tenant_id"] = tenant_id

        async with get_readonly_db_session() as db:
            result = await db.execute(text(safe_sql), query_params)
            rows = result.fetchmany(settings.max_query_rows)
            columns = list(result.keys())
            return QueryResult(columns=columns, rows=[list(r) for r in rows], row_count=len(rows))

    @staticmethod
    def _today_local_date() -> str:
        tz_name = settings.factory_timezone or "Asia/Kolkata"
        try:
            local_tz = ZoneInfo(tz_name)
        except Exception:
            local_tz = ZoneInfo("Asia/Kolkata")
        return datetime.now(local_tz).date().isoformat()

    async def _answer_factory_summary(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        local_day = self._today_local_date()
        sql = (
            "SELECT d.device_id, d.device_name, "
            "COALESCE(ls.runtime_status, d.legacy_status, 'unknown') AS runtime_status, "
            "d.last_seen_timestamp, "
            "CASE WHEN ls.day_bucket = :local_day THEN COALESCE(ls.today_energy_kwh, 0) ELSE 0 END AS today_energy_kwh, "
            "CASE WHEN ls.day_bucket = :local_day THEN COALESCE(ls.today_idle_kwh, 0) ELSE 0 END AS today_idle_kwh, "
            "CASE WHEN ls.day_bucket = :local_day THEN COALESCE(ls.today_loss_kwh, 0) ELSE 0 END AS today_loss_kwh, "
            "CASE WHEN ls.day_bucket = :local_day THEN COALESCE(ls.today_loss_cost_inr, 0) ELSE 0 END AS today_loss_cost_inr, "
            "CASE WHEN ls.day_bucket = :local_day THEN COALESCE(ls.today_running_seconds, 0) ELSE 0 END AS today_running_seconds, "
            "CASE WHEN ls.day_bucket = :local_day THEN COALESCE(ls.today_effective_seconds, 0) ELSE 0 END AS today_effective_seconds "
            "FROM devices d "
            "LEFT JOIN device_live_state ls ON ls.device_id = d.device_id "
            "ORDER BY today_energy_kwh DESC, today_loss_cost_inr DESC, d.device_name ASC LIMIT 50"
        )
        result = await self._execute_curated_query(sql, tenant_id=tenant_id, params={"local_day": local_day})
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows:
            return self._curated_no_data(question, question.no_data_message)

        def _row_has_today_signal(row: list[Any]) -> bool:
            return (
                (self._to_float(row[4]) or 0.0) > 0
                or (self._to_float(row[5]) or 0.0) > 0
                or (self._to_float(row[6]) or 0.0) > 0
                or (self._to_float(row[7]) or 0.0) > 0
                or (self._to_float(row[8]) or 0.0) > 0
                or str(row[2] or "").strip().lower() not in {"", "unknown", "n/a", "none", "null"}
            )

        if not any(_row_has_today_signal(row) for row in result.rows):
            return self._curated_no_data(question, question.no_data_message)

        total_energy_kwh = sum(self._to_float(row[4]) or 0.0 for row in result.rows)
        total_idle_kwh = sum(self._to_float(row[5]) or 0.0 for row in result.rows)
        total_loss_kwh = sum(self._to_float(row[6]) or 0.0 for row in result.rows)
        total_loss_cost = sum(self._to_float(row[7]) or 0.0 for row in result.rows)
        active_status_count = sum(
            1 for row in result.rows if str(row[2] or "").strip().lower() in {"running", "active", "idle", "unload"}
        )

        top_energy_row = max(result.rows, key=lambda row: self._to_float(row[4]) or 0.0)
        top_loss_row = max(result.rows, key=lambda row: self._to_float(row[7]) or 0.0)
        top_energy_kwh = self._to_float(top_energy_row[4]) or 0.0
        top_loss_cost = self._to_float(top_loss_row[7]) or 0.0
        total_idle_cost = round(total_idle_kwh * tariff_rate, 2) if tariff_rate > 0 else 0.0
        concern_text = (
            f"Biggest current concern: {top_loss_row[1]} has the highest avoidable loss at INR {top_loss_cost:.2f}."
            if top_loss_cost > 0
            else f"Biggest current concern: {top_energy_row[1]} is the top energy user at {top_energy_kwh:.3f} kWh."
        )

        sections = ReasoningSections(
            what_happened=(
                f"Today's factory performance: {active_status_count}/{len(result.rows)} machines are active, "
                f"total energy is {total_energy_kwh:.3f} kWh, idle loss is INR {total_idle_cost:.2f}, "
                f"and total avoidable loss is INR {total_loss_cost:.2f} ({total_loss_kwh:.3f} kWh)."
            ),
            why_it_matters=f"{concern_text} This helps prioritize immediate action on today's top operational and cost driver.",
            how_calculated=(
                "Summarized tenant-scoped device_live_state current-day fields (energy, idle loss, total loss, runtime) joined with devices."
            ),
        )
        return CopilotResponse(
            answer=sections.what_happened,
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(
                headers=["Machine", "Status", "Today Energy (kWh)", "Idle Loss (INR)", "Total Loss (INR)"],
                rows=[[row[1], row[2], row[4], round((self._to_float(row[5]) or 0.0) * tariff_rate, 2), row[7]] for row in result.rows[:10]],
            ),
            chart=Chart(
                type="bar",
                title="Energy Today by Machine",
                labels=[str(row[1]) for row in result.rows[:10]],
                datasets=[ChartDataset(label="kWh", data=[float(self._to_float(row[4]) or 0.0) for row in result.rows[:10]])],
            ),
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
            curated_context=CuratedContext(device_id=str(top_energy_row[0])),
        )

    async def _answer_top_energy_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        result = await self._execute_curated_query(
            self._energy_by_machine_sql(limit=10),
            tenant_id=tenant_id,
            params={"local_day": self._today_local_date()},
        )
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows or all((self._to_float(row[2]) or 0.0) <= 0 for row in result.rows):
            return self._curated_no_data(question, "No telemetry available yet for today.")

        top_row = result.rows[0]
        top_kwh = self._to_float(top_row[2]) or 0.0
        top_cost = round(top_kwh * tariff_rate, 2) if tariff_rate > 0 else None
        total_kwh = sum(self._to_float(row[2]) or 0.0 for row in result.rows)
        table_rows = []
        for row in result.rows[:10]:
            row_kwh = self._to_float(row[2]) or 0.0
            row_cost = round(row_kwh * tariff_rate, 2) if tariff_rate > 0 else None
            pct = round((row_kwh / total_kwh) * 100, 2) if total_kwh > 0 else 0.0
            table_rows.append([row[1], row_kwh, row_cost, pct])

        answer = f"{top_row[1]} consumed the most power today at {top_kwh:.3f} kWh."
        if top_cost is not None:
            answer = f"{top_row[1]} consumed the most power today at {top_kwh:.3f} kWh ({currency} {top_cost:.2f})."
        sections = ReasoningSections(
            what_happened=f"Today, {top_row[1]} consumed the most energy at {top_kwh:.3f} kWh.",
            why_it_matters="This identifies the machine to investigate first for immediate energy savings.",
            how_calculated="Ranked device_live_state.today_energy_kwh across tenant devices for today.",
        )
        return CopilotResponse(
            answer=answer,
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(headers=["Machine", "kWh", f"Cost {currency}", "% of Total"], rows=table_rows),
            chart=Chart(
                type="bar",
                title="Energy Today by Machine",
                labels=[str(row[1]) for row in result.rows[:10]],
                datasets=[ChartDataset(label="kWh", data=[float(self._to_float(row[2]) or 0.0) for row in result.rows[:10]])],
            ),
            page_links=[PageLink(label=f"View {top_row[1]}", route=f"/machines/{top_row[0]}")],
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
            curated_context=CuratedContext(device_id=str(top_row[0])),
        )

    async def _answer_health_scores(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        local_day = self._today_local_date()
        sql = (
            "SELECT d.device_id, d.device_name, "
            "COALESCE(ls.uptime_percentage, ls.health_score, 0) AS efficiency_pct, "
            "COALESCE(ls.health_score, 0) AS health_score, "
            "COALESCE(ls.today_energy_kwh, 0) AS today_energy_kwh, "
            "COALESCE(ls.runtime_status, 'stopped') AS runtime_status "
            "FROM devices d "
            "LEFT JOIN device_live_state ls ON ls.device_id = d.device_id "
            "WHERE ls.day_bucket = :local_day "
            "ORDER BY efficiency_pct ASC, d.device_name ASC LIMIT 50"
        )
        result = await self._execute_curated_query(sql, tenant_id=tenant_id, params={"local_day": local_day})
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows:
            return self._curated_no_data(question, question.no_data_message)

        top_row = result.rows[0]
        efficiency = self._to_float(top_row[2]) or 0.0
        health_score = self._to_float(top_row[3]) or 0.0
        sections = ReasoningSections(
            what_happened=f"{top_row[1]} has the lowest efficiency today at {efficiency:.2f}%.",
            why_it_matters="This shows the machine most in need of operational attention first.",
            how_calculated="Compared today's live device efficiency values and ranked machines from lowest to highest.",
        )
        return CopilotResponse(
            answer=f"{top_row[1]} has the lowest efficiency today at {efficiency:.2f}% (health score {health_score:.2f}).",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(
                headers=["Machine", "Efficiency %", "Health Score", "Runtime Status"],
                rows=[[row[1], row[2], row[3], row[5]] for row in result.rows[:10]],
            ),
            chart=Chart(
                type="bar",
                title="Lowest Efficiency Today",
                labels=[str(row[1]) for row in result.rows[:10]],
                datasets=[ChartDataset(label="Efficiency %", data=[float(self._to_float(row[2]) or 0.0) for row in result.rows[:10]])],
            ),
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
            curated_context=CuratedContext(device_id=str(top_row[0])),
        )

    async def _answer_total_energy_cost_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        sql = (
            "SELECT COALESCE(SUM(ls.today_energy_kwh), 0) AS total_energy_kwh "
            "FROM device_live_state ls WHERE ls.day_bucket = :local_day"
        )
        result = await self._execute_curated_query(
            sql,
            tenant_id=tenant_id,
            params={"local_day": self._today_local_date()},
        )
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        total_kwh = self._to_float(result.rows[0][0]) if result.rows else 0.0
        if not total_kwh or total_kwh <= 0:
            return self._curated_no_data(question, question.no_data_message)
        if tariff_rate <= 0:
            return self._curated_no_data(
                question,
                "Today's energy usage is available, but total energy cost cannot be calculated because tariff is not configured.",
            )

        total_cost = round(total_kwh * tariff_rate, 2)
        sections = ReasoningSections(
            what_happened=f"Today's total energy cost is {currency} {total_cost:.2f} from {total_kwh:.3f} kWh of usage.",
            why_it_matters="This gives you the current cost exposure for today's production energy.",
            how_calculated=f"Summed device_live_state.today_energy_kwh across tenant devices and applied the current tariff of {currency} {tariff_rate:.4f}/kWh.",
        )
        return CopilotResponse(
            answer=f"Today's total energy cost is {currency} {total_cost:.2f}.",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(headers=["Total Energy (kWh)", f"Total Cost ({currency})"], rows=[[round(total_kwh, 3), total_cost]]),
            chart=None,
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
        )

    async def _answer_highest_loss_machine_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        local_day = self._today_local_date()
        sql = (
            "SELECT d.device_id, d.device_name, COALESCE(ls.today_loss_cost_inr, 0) AS today_loss_cost_inr, "
            "COALESCE(ls.today_loss_kwh, 0) AS today_loss_kwh "
            "FROM devices d "
            "JOIN device_live_state ls ON ls.device_id = d.device_id "
            "WHERE ls.day_bucket = :local_day "
            "ORDER BY today_loss_cost_inr DESC, today_loss_kwh DESC, d.device_name ASC LIMIT 10"
        )
        result = await self._execute_curated_query(sql, tenant_id=tenant_id, params={"local_day": local_day})
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows or all((self._to_float(row[2]) or 0.0) <= 0 for row in result.rows):
            return self._curated_no_data(question, question.no_data_message)

        top_row = result.rows[0]
        top_cost = self._to_float(top_row[2]) or 0.0
        sections = ReasoningSections(
            what_happened=f"{top_row[1]} caused the highest loss today at INR {top_cost:.2f}.",
            why_it_matters="This identifies the machine creating the biggest avoidable cost today.",
            how_calculated="Ranked device_live_state.today_loss_cost_inr across tenant devices.",
        )
        return CopilotResponse(
            answer=sections.what_happened,
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(
                headers=["Machine", "Loss Cost (INR)", "Loss Energy (kWh)"],
                rows=[[row[1], row[2], row[3]] for row in result.rows[:10]],
            ),
            chart=Chart(
                type="bar",
                title="Loss Cost Today by Machine",
                labels=[str(row[1]) for row in result.rows[:10]],
                datasets=[ChartDataset(label="Loss Cost (INR)", data=[float(self._to_float(row[2]) or 0.0) for row in result.rows[:10]])],
            ),
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
            curated_context=CuratedContext(device_id=str(top_row[0])),
        )

    async def _answer_total_idle_loss_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        sql = (
            "SELECT COALESCE(SUM(ls.today_idle_kwh), 0) AS total_idle_kwh "
            "FROM device_live_state ls WHERE ls.day_bucket = :local_day"
        )
        result = await self._execute_curated_query(
            sql,
            tenant_id=tenant_id,
            params={"local_day": self._today_local_date()},
        )
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        total_idle_kwh = self._to_float(result.rows[0][0]) if result.rows else 0.0
        total_cost = round(total_idle_kwh * tariff_rate, 4) if total_idle_kwh and tariff_rate > 0 else 0.0
        if not total_idle_kwh or total_idle_kwh <= 0:
            return self._curated_no_data(question, question.no_data_message)

        sections = ReasoningSections(
            what_happened=f"Today's total idle loss is {currency} {total_cost:.2f}.",
            why_it_matters="This shows the avoidable idle cost already accumulated today.",
            how_calculated=f"Summed device_live_state.today_idle_kwh for the current local day and applied the tariff of {currency} {tariff_rate:.4f}/kWh.",
        )
        return CopilotResponse(
            answer=sections.what_happened,
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(headers=[f"Total Idle Loss ({currency})", "Idle Energy (kWh)"], rows=[[total_cost, round(total_idle_kwh, 6)]]),
            chart=None,
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
        )

    async def _answer_top_5_energy_machines_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        result = await self._execute_curated_query(
            self._energy_by_machine_sql(limit=5),
            tenant_id=tenant_id,
            params={"local_day": self._today_local_date()},
        )
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows or all((self._to_float(row[2]) or 0.0) <= 0 for row in result.rows):
            return self._curated_no_data(question, question.no_data_message)

        top_row = result.rows[0]
        sections = ReasoningSections(
            what_happened=f"The highest energy consumer today is {top_row[1]} at {(self._to_float(top_row[2]) or 0.0):.3f} kWh.",
            why_it_matters="The top five machines show where most of today's energy is concentrated.",
            how_calculated="Ranked device_live_state.today_energy_kwh and returned the top five devices.",
        )
        return CopilotResponse(
            answer="Showing the top 5 machines by energy consumption today.",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(headers=["Machine", "kWh"], rows=[[row[1], row[2]] for row in result.rows[:5]]),
            chart=Chart(
                type="bar",
                title="Top 5 Machines by Energy Today",
                labels=[str(row[1]) for row in result.rows[:5]],
                datasets=[ChartDataset(label="kWh", data=[float(self._to_float(row[2]) or 0.0) for row in result.rows[:5]])],
            ),
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
            curated_context=CuratedContext(device_id=str(top_row[0])),
        )

    async def _answer_top_5_idle_cost_machines_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        local_day = self._today_local_date()
        sql = (
            "SELECT d.device_id, d.device_name, COALESCE(ls.today_idle_kwh, 0) AS today_idle_kwh "
            "FROM devices d "
            "JOIN device_live_state ls ON ls.device_id = d.device_id "
            "WHERE ls.day_bucket = :local_day AND COALESCE(ls.today_idle_kwh, 0) > 0 "
            "ORDER BY today_idle_kwh DESC, d.device_name ASC LIMIT 5"
        )
        result = await self._execute_curated_query(sql, tenant_id=tenant_id, params={"local_day": local_day})
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows:
            return self._curated_no_data(question, question.no_data_message)

        top_row = result.rows[0]
        top_cost = round((self._to_float(top_row[2]) or 0.0) * tariff_rate, 2)
        sections = ReasoningSections(
            what_happened=f"The highest idle-cost machine today is {top_row[1]} at {currency} {top_cost:.2f}.",
            why_it_matters="The top five idle-cost machines show where today's avoidable waste is concentrated.",
            how_calculated=f"Ranked current-day device_live_state.today_idle_kwh per machine and applied the tariff of {currency} {tariff_rate:.4f}/kWh.",
        )
        return CopilotResponse(
            answer="Showing the top 5 machines by idle cost today.",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(
                headers=["Machine", f"Idle Cost ({currency})", "Idle Energy (kWh)"],
                rows=[[row[1], round((self._to_float(row[2]) or 0.0) * tariff_rate, 2), row[2]] for row in result.rows[:5]],
            ),
            chart=Chart(
                type="bar",
                title="Top 5 Machines by Idle Cost Today",
                labels=[str(row[1]) for row in result.rows[:5]],
                datasets=[ChartDataset(label=f"Idle Cost ({currency})", data=[round((self._to_float(row[2]) or 0.0) * tariff_rate, 2) for row in result.rows[:5]])],
            ),
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
            curated_context=CuratedContext(device_id=str(top_row[0])),
        )

    async def _answer_device_power_trend_last_7_days(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        device = await self._resolve_device_for_curated_trend(message=message, curated_context=curated_context)
        if device is None:
            return self._curated_no_data(question, "Please mention or select a machine to show its 7-day trend.")

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        try:
            payload = await self.data_service.fetch_telemetry(
                device_id=device["device_id"],
                start=start,
                end=end,
                tenant_id=tenant_id,
                fields=["power"],
                limit=1500,
            )
        except Exception:
            payload = {}

        items = payload.get("data", {}).get("items", []) if isinstance(payload, dict) else []
        points = [item for item in items if item.get("power") is not None][:200]
        if not points:
            return self._curated_no_data(
                question,
                f"Not enough data for last 7 days trend for {device['device_id']}.",
                curated_context=CuratedContext(device_id=device["device_id"]),
            )

        labels = [str(point.get("timestamp", ""))[:16] for point in points]
        values = [float(point.get("power")) for point in points]
        sections = ReasoningSections(
            what_happened=f"Showing the last 7 days power trend for {device['device_name']}.",
            why_it_matters="This helps you spot repeated spikes, unstable load, and developing operating drift.",
            how_calculated="Fetched timestamped power telemetry for the resolved machine over the last 7 days.",
        )
        return CopilotResponse(
            answer=f"Showing the last 7 days power trend for {device['device_name']}.",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(headers=["Timestamp", "Power"], rows=[[labels[i], values[i]] for i in range(min(len(labels), 30))]),
            chart=Chart(
                type="line",
                title=f"Power Trend: {device['device_name']}",
                labels=labels,
                datasets=[ChartDataset(label="Power", data=values)],
            ),
            page_links=[PageLink(label=f"View {device['device_name']}", route=f"/machines/{device['device_id']}")],
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
            curated_context=CuratedContext(device_id=device["device_id"]),
        )

    async def _answer_alerts_triggered_count_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        start_str, end_str = self._today_window_factory_tz()
        sql = (
            "SELECT COUNT(*) AS alert_count "
            "FROM alerts "
            "WHERE tenant_id = :tenant_id AND created_at >= :start_ts AND created_at < :end_ts"
        )
        try:
            async with get_db_session() as db:
                result = await db.execute(text(sql), {"tenant_id": tenant_id, "start_ts": start_str, "end_ts": end_str})
                row = result.first()
                count = int(row[0]) if row else 0
        except Exception:
            return self._curated_no_data(question, question.no_data_message)
        if count <= 0:
            return self._curated_no_data(question, question.no_data_message)

        sections = ReasoningSections(
            what_happened=f"{count} alerts were triggered today.",
            why_it_matters="This gives a quick measure of today's operational rule activity.",
            how_calculated="Counted today's alert records in the tenant-scoped alerts table.",
        )
        return CopilotResponse(
            answer=f"{count} alerts were triggered today.",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(headers=["Alerts Triggered Today"], rows=[[count]]),
            chart=None,
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
        )

    async def _answer_top_plant_energy_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        local_day = self._today_local_date()
        sql = (
            "SELECT d.plant_id, SUM(ls.today_energy_kwh) AS total_energy_kwh "
            "FROM devices d "
            "JOIN device_live_state ls ON ls.device_id = d.device_id "
            "WHERE d.plant_id IS NOT NULL AND ls.day_bucket = :local_day "
            "GROUP BY d.plant_id "
            "HAVING SUM(ls.today_energy_kwh) > 0 "
            "ORDER BY total_energy_kwh DESC, d.plant_id ASC LIMIT 5"
        )
        result = await self._execute_curated_query(sql, tenant_id=tenant_id, params={"local_day": local_day})
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows:
            return self._curated_no_data(question, question.no_data_message)

        top_row = result.rows[0]
        sections = ReasoningSections(
            what_happened=f"Plant {top_row[0]} consumed the most energy today at {(self._to_float(top_row[1]) or 0.0):.3f} kWh.",
            why_it_matters="This shows which plant is driving the largest share of today's energy demand.",
            how_calculated="Grouped device_live_state.today_energy_kwh by devices.plant_id and ranked the totals.",
        )
        return CopilotResponse(
            answer=f"Plant {top_row[0]} consumed the most energy today.",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(headers=["Plant", "kWh"], rows=[[f"Plant {row[0]}", row[1]] for row in result.rows[:5]]),
            chart=Chart(
                type="bar",
                title="Energy Today by Plant",
                labels=[f"Plant {row[0]}" for row in result.rows[:5]],
                datasets=[ChartDataset(label="kWh", data=[float(self._to_float(row[1]) or 0.0) for row in result.rows[:5]])],
            ),
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
        )

    async def _answer_top_plant_idle_loss_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        local_day = self._today_local_date()
        sql = (
            "SELECT d.plant_id, SUM(ls.today_idle_kwh) AS total_idle_kwh "
            "FROM devices d "
            "JOIN device_live_state ls ON ls.device_id = d.device_id "
            "WHERE d.plant_id IS NOT NULL AND ls.day_bucket = :local_day "
            "GROUP BY d.plant_id "
            "HAVING SUM(ls.today_idle_kwh) > 0 "
            "ORDER BY total_idle_kwh DESC, d.plant_id ASC LIMIT 5"
        )
        result = await self._execute_curated_query(sql, tenant_id=tenant_id, params={"local_day": local_day})
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows:
            return self._curated_no_data(question, question.no_data_message)

        top_row = result.rows[0]
        top_cost = round((self._to_float(top_row[1]) or 0.0) * tariff_rate, 2)
        sections = ReasoningSections(
            what_happened=f"Plant {top_row[0]} has the highest idle loss today at {currency} {top_cost:.2f}.",
            why_it_matters="This identifies the plant where avoidable idle waste is currently highest.",
            how_calculated=f"Grouped current-day device_live_state.today_idle_kwh by devices.plant_id and applied the tariff of {currency} {tariff_rate:.4f}/kWh.",
        )
        return CopilotResponse(
            answer=f"Plant {top_row[0]} has the highest idle loss today.",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(
                headers=["Plant", f"Idle Loss ({currency})", "Idle Energy (kWh)"],
                rows=[[f"Plant {row[0]}", round((self._to_float(row[1]) or 0.0) * tariff_rate, 2), row[1]] for row in result.rows[:5]],
            ),
            chart=Chart(
                type="bar",
                title="Idle Loss Today by Plant",
                labels=[f"Plant {row[0]}" for row in result.rows[:5]],
                datasets=[ChartDataset(label=f"Idle Loss ({currency})", data=[round((self._to_float(row[1]) or 0.0) * tariff_rate, 2) for row in result.rows[:5]])],
            ),
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
        )

    async def _answer_runtime_summary_today(
        self,
        question: CuratedQuestion,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
        curated_context: CuratedContext | None,
    ) -> CopilotResponse:
        local_day = self._today_local_date()
        sql = (
            "SELECT COALESCE(ls.runtime_status, 'stopped') AS runtime_status, "
            "COUNT(*) AS machine_count, "
            "COALESCE(SUM(ls.today_running_seconds), 0) AS total_running_seconds, "
            "COALESCE(SUM(ls.today_effective_seconds), 0) AS total_effective_seconds "
            "FROM device_live_state ls "
            "WHERE ls.day_bucket = :local_day "
            "GROUP BY runtime_status "
            "ORDER BY machine_count DESC, runtime_status ASC"
        )
        result = await self._execute_curated_query(sql, tenant_id=tenant_id, params={"local_day": local_day})
        if result.error:
            return self._blocked_response(question.follow_up_ids)
        if not result.rows:
            return self._curated_no_data(question, question.no_data_message)

        total_machines = sum(int(row[1]) for row in result.rows)
        total_running_seconds = sum(int(row[2]) for row in result.rows)
        total_effective_seconds = sum(int(row[3]) for row in result.rows)
        if total_machines <= 0:
            return self._curated_no_data(question, question.no_data_message)

        sections = ReasoningSections(
            what_happened=f"Today's runtime summary covers {total_machines} machines with {round(total_running_seconds / 3600, 2)} running hours recorded.",
            why_it_matters="This gives a quick view of how much of today's available machine time was actually active.",
            how_calculated="Grouped device_live_state by runtime_status and summed today's running and effective seconds.",
        )
        return CopilotResponse(
            answer="Showing today's runtime summary.",
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            data_table=DataTable(
                headers=["Runtime Status", "Machine Count", "Running Hours", "Effective Hours"],
                rows=[[row[0], row[1], round(int(row[2]) / 3600, 2), round(int(row[3]) / 3600, 2)] for row in result.rows],
            ),
            chart=Chart(
                type="bar",
                title="Machines by Runtime Status",
                labels=[str(row[0]) for row in result.rows],
                datasets=[ChartDataset(label="Machine Count", data=[int(row[1]) for row in result.rows])],
            ),
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
        )

    async def _run_ai_sql(
        self,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
        tenant_id: str,
    ) -> CopilotResponse:
        if not self._can_use_ai_sql():
            return self._approved_questions_only_response()

        history_text = "\n".join(
            f"{t.get('role', 'user').upper()}: {t.get('content', '')}" for t in history[-settings.max_history_turns :]
        )
        schema_context = get_schema_context()

        user_prompt = (
            f"SCHEMA:\n{schema_context}\n\n"
            f"CONVERSATION:\n{history_text}\n\n"
            f"USER QUESTION: {message}\n\n"
            "Write the query now."
        )

        try:
            sql = (await self.model_client.generate(
                messages=[
                    {"role": "system", "content": SQL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=settings.stage1_max_tokens,
            )).strip()
        except AIUnavailableError:
            return CopilotResponse(
                answer="AI service is temporarily unavailable. Please try again.",
                reasoning="What happened: AI query generation is temporarily unavailable.\n"
                "Why it matters: I could not safely translate your question into a query.\n"
                "How calculated: Provider request failed during query generation.",
                reasoning_sections=ReasoningSections(
                    what_happened="AI query generation is temporarily unavailable.",
                    why_it_matters="I could not safely translate your question into a query.",
                    how_calculated="Provider request failed during query generation.",
                ),
                error_code="AI_UNAVAILABLE",
            )

        if sql.upper() == "NO_DATA":
            return CopilotResponse(
                answer="No data found for this period.",
                reasoning="What happened: I could not find a reliable data path for this question.\n"
                "Why it matters: Returning guessed numbers would be misleading.\n"
                "How calculated: Matched your question against current FactoryOPS schema and available modules.",
                reasoning_sections=ReasoningSections(
                    what_happened="I could not find a reliable data path for this question.",
                    why_it_matters="Returning guessed numbers would be misleading.",
                    how_calculated="Matched your question against current FactoryOPS schema and available modules.",
                ),
                error_code="NO_DATA",
            )

        result = await self.query_engine.execute_query(sql, tenant_id=tenant_id)
        if result.error:
            return self._blocked_response(list(DEFAULT_CURATED_FOLLOWUP_IDS))
        if not result.rows:
            return CopilotResponse(
                answer="No data found for this period.",
                reasoning="What happened: No matching records were found.\n"
                "Why it matters: There is no data to conclude on this request for the selected period.\n"
                "How calculated: Ran a safe read-only query using current FactoryOPS schema.",
                reasoning_sections=ReasoningSections(
                    what_happened="No matching records were found.",
                    why_it_matters="There is no data to conclude on this request for the selected period.",
                    how_calculated="Ran a safe read-only query using current FactoryOPS schema.",
                ),
                error_code="NO_DATA",
            )

        payload = {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "sql": sql,
        }
        return await self._format_with_ai_or_fallback(
            message=message,
            payload=payload,
            reasoning="",
            chart_hint="table",
            default_title="Query Results",
            default_followups=[
                "Summarize today's factory performance",
                "Which machine consumed the most power today?",
                "What is today's total idle loss?",
            ],
            tariff_rate=tariff_rate,
            currency=currency,
            intent="ai_sql",
        )

    async def _format_with_ai_or_fallback(
        self,
        message: str,
        payload: dict[str, Any],
        reasoning: str,
        chart_hint: str,
        default_title: str,
        default_followups: list[str],
        tariff_rate: float,
        currency: str,
        force_chart: Chart | None = None,
        chart_omission_reason: str | None = None,
        intent: str = "ai_sql",
    ) -> CopilotResponse:
        headers = payload.get("columns") or []
        rows = payload.get("rows") or []
        forced_table = DataTable(headers=headers, rows=rows[:50]) if headers and rows else None
        composed = ReasoningComposer.for_query_result(
            intent=intent,
            message=message,
            columns=headers,
            rows=rows,
            chart_omission_reason=chart_omission_reason,
        )
        if not self._can_use_ai_sql():
            return CopilotResponse(
                answer=composed.answer,
                reasoning=self._append_chart_omission(composed.text if not reasoning else reasoning, chart_omission_reason),
                reasoning_sections=composed.sections,
                data_table=forced_table,
                chart=force_chart or self._fallback_chart(headers, rows, default_title, chart_hint),
                follow_up_suggestions=self._validated_followups(default_followups),
            )
        try:
            formatted_raw = await self.model_client.generate(
                messages=[
                    {"role": "system", "content": FORMATTER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"USER QUESTION: {message}\n"
                            f"QUERY RESULTS JSON: {json.dumps(payload, default=str)}\n"
                            f"TARIFF: {currency} {tariff_rate}/kWh\n"
                            f"CHART_HINT: {chart_hint}\n"
                        ),
                    },
                ],
                max_tokens=settings.stage2_max_tokens,
            )
            parsed = json.loads(formatted_raw)

            response = CopilotResponse(**parsed)
            response.follow_up_suggestions = self._validated_followups(response.follow_up_suggestions or default_followups)
            response.data_table = response.data_table or forced_table
            if force_chart is not None:
                response.chart = force_chart
            elif chart_omission_reason:
                response.chart = None
            if not response.reasoning:
                response.reasoning = composed.text if not reasoning else reasoning
            if not response.reasoning_sections:
                response.reasoning_sections = composed.sections
            if chart_omission_reason and chart_omission_reason not in response.reasoning:
                response.reasoning = f"{response.reasoning} Chart omitted: {chart_omission_reason}"
            if self._looks_technical(response.answer):
                response.answer = composed.answer
            if self._looks_technical(response.reasoning):
                response.reasoning = composed.text
            response.chart = self._sanitize_chart(response.chart)
            return response
        except json.JSONDecodeError as exc:
            COPILOT_METRICS["formatter_parse_failed"] += 1
            logger.warning("copilot_formatter_parse_failed error=%s", exc)
            return CopilotResponse(
                answer=composed.answer,
                reasoning=self._append_chart_omission(composed.text if not reasoning else reasoning, chart_omission_reason),
                reasoning_sections=composed.sections,
                data_table=forced_table,
                chart=force_chart or self._fallback_chart(headers, rows, default_title, chart_hint),
                follow_up_suggestions=self._validated_followups(default_followups),
            )
        except Exception as exc:
            COPILOT_METRICS["formatter_parse_failed"] += 1
            logger.warning("copilot_formatter_validation_failed error=%s", exc)
            return CopilotResponse(
                answer=composed.answer,
                reasoning=self._append_chart_omission(composed.text if not reasoning else reasoning, chart_omission_reason),
                reasoning_sections=composed.sections,
                data_table=forced_table,
                chart=force_chart or self._fallback_chart(headers, rows, default_title, chart_hint),
                follow_up_suggestions=self._validated_followups(default_followups),
            )

    @staticmethod
    def _fallback_answer(rows: list[list[Any]]) -> str:
        if not rows:
            return "No data found for this period."
        return f"I found {len(rows)} matching records and highlighted the top result for quick review."

    @staticmethod
    def _fallback_chart(headers: list[str], rows: list[list[Any]], title: str, chart_hint: str) -> Chart | None:
        if chart_hint not in {"bar", "line"}:
            return None
        if len(headers) < 2 or not rows:
            return None
        fallback = CopilotEngine._build_chart_from_rows(
            columns=headers,
            rows=rows,
            chart_type=chart_hint,
            title=title,
            x_key=headers[0],
            y_key=headers[1],
        )
        return fallback[0]

    @staticmethod
    def _append_chart_omission(reasoning: str, chart_omission_reason: str | None) -> str:
        if not chart_omission_reason:
            return reasoning
        if chart_omission_reason in reasoning:
            return reasoning
        return f"{reasoning} Chart omitted: {chart_omission_reason}"

    @staticmethod
    def _sanitize_chart(chart: Chart | None) -> Chart | None:
        if chart is None:
            return None
        if not chart.datasets:
            return None
        dataset = chart.datasets[0]
        labels: list[str] = []
        values: list[float] = []
        for i, label in enumerate(chart.labels):
            if i >= len(dataset.data):
                break
            value = CopilotEngine._to_float(dataset.data[i])
            if value is None:
                continue
            labels.append(str(label))
            values.append(value)
        if not labels:
            return None
        return Chart(
            type=chart.type,
            title=chart.title,
            labels=labels,
            datasets=[ChartDataset(label=dataset.label, data=values)],
        )

    @staticmethod
    def _build_chart_from_rows(
        columns: list[str],
        rows: list[list[Any]],
        chart_type: str,
        title: str,
        x_key: str | None,
        y_key: str | None,
    ) -> tuple[Chart | None, str | None, int]:
        if chart_type not in {"bar", "line"}:
            return None, "chart type is not plottable", 0
        if not columns or not rows:
            return None, "no rows available for charting", 0
        if not x_key or not y_key:
            return None, "missing chart column mapping", 0

        x_idx = CopilotEngine._find_column_idx(columns, x_key)
        y_idx = CopilotEngine._find_column_idx(columns, y_key)
        if x_idx is None or y_idx is None:
            return None, f"mapped chart columns not present ({x_key}, {y_key})", 0

        labels: list[str] = []
        values: list[float] = []
        for row in rows[:50]:
            if x_idx >= len(row) or y_idx >= len(row):
                continue
            num = CopilotEngine._to_float(row[y_idx])
            if num is None:
                continue
            labels.append(str(row[x_idx]))
            values.append(num)

        if not labels:
            return None, f"no numeric data for '{y_key}'", 0

        return (
            Chart(
                type=chart_type,
                title=title,
                labels=labels,
                datasets=[ChartDataset(label=y_key, data=values)],
            ),
            None,
            len(values),
        )

    @staticmethod
    def _find_column_idx(columns: list[str], key: str) -> int | None:
        target = key.lower().strip()
        for idx, name in enumerate(columns):
            if str(name).lower().strip() == target:
                return idx
        return None

    @staticmethod
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

    @staticmethod
    def _extract_device_from_text(text_value: str) -> str | None:
        match = DEVICE_ID_PATTERN.search(text_value.upper())
        return match.group(0) if match else None

    async def _resolve_device_for_curated_trend(
        self,
        message: str,
        curated_context: CuratedContext | None,
    ) -> dict[str, str] | None:
        devices = await self._list_devices()
        if curated_context and curated_context.device_id:
            target_id = curated_context.device_id.upper()
            for device in devices:
                device_id = str(device.get("device_id", "")).upper()
                if device_id == target_id:
                    return {"device_id": device_id, "device_name": str(device.get("device_name") or device_id)}

        explicit_device_id = self._extract_device_from_text(message)
        if explicit_device_id:
            for device in devices:
                device_id = str(device.get("device_id", "")).upper()
                if device_id == explicit_device_id:
                    return {"device_id": device_id, "device_name": str(device.get("device_name") or device_id)}

        message_upper = (message or "").upper()
        for device in devices:
            device_name = str(device.get("device_name") or "")
            if device_name and device_name.upper() in message_upper:
                return {"device_id": str(device.get("device_id") or ""), "device_name": device_name}
        return None

    @staticmethod
    def _energy_by_machine_sql(limit: int) -> str:
        return (
            "SELECT d.device_id, d.device_name, COALESCE(ls.today_energy_kwh, 0) AS today_energy_kwh "
            "FROM devices d "
            "JOIN device_live_state ls ON ls.device_id = d.device_id "
            "WHERE ls.day_bucket = :local_day "
            "ORDER BY today_energy_kwh DESC, d.device_name ASC "
            f"LIMIT {limit}"
        )

    @staticmethod
    def _today_window_factory_tz() -> tuple[str, str]:
        tz_name = settings.factory_timezone or "Asia/Kolkata"
        try:
            local_tz = ZoneInfo(tz_name)
        except Exception:
            local_tz = ZoneInfo("Asia/Kolkata")
        now_local = datetime.now(local_tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        return start_local.strftime("%Y-%m-%d %H:%M:%S"), end_local.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _source_tables_for_intent(intent: str) -> list[str]:
        mapping = {
            "factory_summary": ["devices", "idle_running_log"],
            "idle_waste": ["idle_running_log", "devices"],
            "health_scores": ["devices", "device_live_state"],
        }
        return mapping.get(intent, ["devices"])

    def _validated_followups(self, candidates: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = candidate.strip() if candidate else ""
            dedupe_key = normalized.lower()
            if normalized and dedupe_key not in seen and match_curated_question(normalized):
                seen.add(dedupe_key)
                out.append(normalized)
            if len(out) == 3:
                break
        return out

    def _followup_texts(self, follow_up_ids: tuple[str, ...]) -> list[str]:
        return self._validated_followups(get_follow_up_texts(follow_up_ids))

    def _curated_no_data(
        self,
        question: CuratedQuestion,
        answer: str,
        curated_context: CuratedContext | None = None,
    ) -> CopilotResponse:
        sections = ReasoningSections(
            what_happened=answer,
            why_it_matters="Copilot is returning a healthy no-data response instead of guessing or failing.",
            how_calculated="Used the deterministic curated handler for this question and found no reliable data to return yet.",
        )
        return CopilotResponse(
            answer=answer,
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            chart=None,
            data_table=None,
            follow_up_suggestions=self._followup_texts(question.follow_up_ids),
            curated_context=curated_context,
            error_code="NO_DATA",
        )

    @staticmethod
    def _looks_technical(value: str | None) -> bool:
        if not value:
            return False
        lowered = value.lower()
        technical_tokens = [
            "decimal(",
            "datetime.datetime(",
            "top row: [",
            "source: ai_factoryops",
            "template-specific aggregate",
            "filters: template intent constraints",
        ]
        return any(token in lowered for token in technical_tokens)

    @staticmethod
    def _sections_to_text(sections: ReasoningSections) -> str:
        return (
            f"What happened: {sections.what_happened}\n"
            f"Why it matters: {sections.why_it_matters}\n"
            f"How calculated: {sections.how_calculated}"
        )

    def _can_use_ai_sql(self) -> bool:
        if self.model_client is None:
            return False
        availability = getattr(self.model_client, "is_available", None)
        if callable(availability):
            return bool(availability())
        return hasattr(self.model_client, "generate")

    def _approved_questions_only_response(self) -> CopilotResponse:
        sections = ReasoningSections(
            what_happened="This Copilot currently supports approved factory questions only.",
            why_it_matters="Without an external AI provider, only curated deterministic questions are supported.",
            how_calculated="Matched your request against the approved curated question catalog and blocked non-curated execution.",
        )
        return CopilotResponse(
            answer=sections.what_happened,
            reasoning=self._sections_to_text(sections),
            reasoning_sections=sections,
            chart=None,
            data_table=None,
            follow_up_suggestions=self._followup_texts(DEFAULT_CURATED_FOLLOWUP_IDS),
            error_code="APPROVED_QUESTIONS_ONLY",
        )

    def _blocked_response(self, suggested_followups: list[str]) -> CopilotResponse:
        blocked = ReasoningComposer.for_blocked_query()
        return CopilotResponse(
            answer=blocked.answer,
            reasoning=blocked.text,
            reasoning_sections=blocked.sections,
            follow_up_suggestions=self._validated_followups(
                suggested_followups
                or [
                    "Summarize today's factory performance",
                    "Which machine consumed the most power today?",
                    "What is today's total idle loss?",
                ]
            ),
            error_code="QUERY_BLOCKED",
        )

    async def _list_devices(self) -> list[dict[str, Any]]:
        async with get_db_session() as db:
            result = await db.execute(
                text("SELECT device_id, device_name FROM devices WHERE deleted_at IS NULL ORDER BY device_id LIMIT 200")
            )
            return [{"device_id": r[0], "device_name": r[1]} for r in result.fetchall()]
