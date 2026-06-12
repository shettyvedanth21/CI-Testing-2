from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CuratedQuestion:
    id: str
    text: str
    handler: str
    required_context: str
    chart_type: str
    no_data_message: str
    follow_up_ids: tuple[str, ...] = field(default_factory=tuple)
    starter: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)


CATALOG: tuple[CuratedQuestion, ...] = (
    CuratedQuestion(
        id="factory_summary",
        text="Summarize today's factory performance",
        handler="factory_summary",
        required_context="none",
        chart_type="bar",
        no_data_message="No telemetry available yet for today.",
        follow_up_ids=("total_idle_loss_today", "highest_loss_machine_today", "alerts_triggered_count_today"),
        starter=True,
    ),
    CuratedQuestion(
        id="top_energy_today",
        text="Which machine consumed the most power today?",
        handler="top_energy_today",
        required_context="none",
        chart_type="bar",
        no_data_message="No telemetry available yet for today.",
        follow_up_ids=("device_power_trend_last_7_days", "total_energy_cost_today", "top_5_energy_machines_today"),
        starter=True,
    ),
    CuratedQuestion(
        id="health_scores",
        text="Which machine has the lowest efficiency today?",
        handler="health_scores",
        required_context="none",
        chart_type="bar",
        no_data_message="No runtime summary is available yet today.",
        follow_up_ids=("runtime_summary_today", "highest_loss_machine_today", "top_energy_today"),
        starter=True,
    ),
    CuratedQuestion(
        id="total_energy_cost_today",
        text="What is today's total energy cost?",
        handler="total_energy_cost_today",
        required_context="none",
        chart_type="none",
        no_data_message="No telemetry available yet for today, so total energy cost cannot be calculated.",
        follow_up_ids=("top_5_energy_machines_today", "top_plant_energy_today", "top_energy_today"),
        starter=True,
    ),
    CuratedQuestion(
        id="highest_loss_machine_today",
        text="Which machine caused the highest loss today?",
        handler="highest_loss_machine_today",
        required_context="none",
        chart_type="bar",
        no_data_message="No machine loss recorded today.",
        follow_up_ids=("total_idle_loss_today", "top_5_idle_cost_machines_today", "device_power_trend_last_7_days"),
        starter=True,
        aliases=("Which machine has the highest idle cost today?",),
    ),
    CuratedQuestion(
        id="total_idle_loss_today",
        text="What is today's total idle loss?",
        handler="total_idle_loss_today",
        required_context="none",
        chart_type="none",
        no_data_message="No idle loss recorded today.",
        follow_up_ids=("top_5_idle_cost_machines_today", "highest_loss_machine_today", "runtime_summary_today"),
        starter=True,
        aliases=("What is today's idle running cost?",),
    ),
    CuratedQuestion(
        id="top_5_energy_machines_today",
        text="Show top 5 machines by energy consumption today",
        handler="top_5_energy_machines_today",
        required_context="none",
        chart_type="bar",
        no_data_message="No telemetry available yet for today.",
        follow_up_ids=("total_energy_cost_today", "top_plant_energy_today", "device_power_trend_last_7_days"),
        starter=True,
    ),
    CuratedQuestion(
        id="top_5_idle_cost_machines_today",
        text="Show top 5 machines by idle cost today",
        handler="top_5_idle_cost_machines_today",
        required_context="none",
        chart_type="bar",
        no_data_message="No idle loss recorded today.",
        follow_up_ids=("total_idle_loss_today", "highest_loss_machine_today", "top_plant_idle_loss_today"),
        starter=True,
    ),
    CuratedQuestion(
        id="device_power_trend_last_7_days",
        text="Show this machine trend for last 7 days",
        handler="device_power_trend_last_7_days",
        required_context="device",
        chart_type="line",
        no_data_message="Please mention or select a machine to show its 7-day trend.",
        follow_up_ids=("top_energy_today", "highest_loss_machine_today", "runtime_summary_today"),
        aliases=("Show energy trend for this machine", "Show performance trend for this machine"),
    ),
    CuratedQuestion(
        id="alerts_triggered_count_today",
        text="How many alerts were triggered today?",
        handler="alerts_triggered_count_today",
        required_context="none",
        chart_type="none",
        no_data_message="No alerts triggered today.",
        follow_up_ids=("factory_summary", "runtime_summary_today", "top_energy_today"),
        starter=True,
    ),
    CuratedQuestion(
        id="top_plant_energy_today",
        text="Which plant consumed the most energy today?",
        handler="top_plant_energy_today",
        required_context="none",
        chart_type="bar",
        no_data_message="No plant-level energy data is available today.",
        follow_up_ids=("top_5_energy_machines_today", "total_energy_cost_today", "top_plant_idle_loss_today"),
        starter=True,
    ),
    CuratedQuestion(
        id="top_plant_idle_loss_today",
        text="Which plant has the highest idle loss today?",
        handler="top_plant_idle_loss_today",
        required_context="none",
        chart_type="bar",
        no_data_message="No plant-level idle loss recorded today.",
        follow_up_ids=("top_5_idle_cost_machines_today", "total_idle_loss_today", "top_plant_energy_today"),
        starter=True,
    ),
    CuratedQuestion(
        id="runtime_summary_today",
        text="Show today's runtime summary",
        handler="runtime_summary_today",
        required_context="none",
        chart_type="bar",
        no_data_message="No runtime summary is available yet today.",
        follow_up_ids=("health_scores", "highest_loss_machine_today", "alerts_triggered_count_today"),
        starter=True,
    ),
)


CATALOG_BY_ID = {question.id: question for question in CATALOG}
CATALOG_BY_TEXT = {question.text.lower(): question for question in CATALOG}
CATALOG_BY_ALIAS = {alias.lower(): question for question in CATALOG for alias in question.aliases}


def get_curated_question_by_id(question_id: str) -> CuratedQuestion | None:
    return CATALOG_BY_ID.get(question_id)


def match_curated_question(text: str) -> CuratedQuestion | None:
    normalized = (text or "").strip().lower()
    if not normalized:
        return None
    return CATALOG_BY_TEXT.get(normalized) or CATALOG_BY_ALIAS.get(normalized)


def get_starter_questions() -> list[CuratedQuestion]:
    return [question for question in CATALOG if question.starter]


def get_follow_up_texts(follow_up_ids: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for question_id in follow_up_ids:
        question = get_curated_question_by_id(question_id)
        if question:
            out.append(question.text)
    return out
