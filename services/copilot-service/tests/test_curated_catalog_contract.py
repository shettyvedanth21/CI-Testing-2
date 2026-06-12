from pathlib import Path

from src.ai.copilot_engine import CopilotEngine
from src.intent.router import is_answerable_followup
from src.templates.curated_catalog import CATALOG, get_starter_questions, match_curated_question


DEPRECATED_QUESTIONS = {
    "Which rule triggered most this week?",
    "Show unresolved alerts only",
    "Why is this machine idle so long?",
    "Open waste analysis report",
    "Show recent alerts for the top idle machine",
    "Show recent alerts for the lowest-efficiency machine",
    "Why did it spike at 3pm?",
}


def test_all_starter_questions_come_from_authoritative_catalog():
    starters = get_starter_questions()
    assert starters
    starter_ids = {question.id for question in starters}
    assert "factory_summary" in starter_ids
    assert "top_energy_today" in starter_ids
    assert "health_scores" in starter_ids


def test_all_followups_resolve_to_catalog_entries():
    all_ids = {question.id for question in CATALOG}
    for question in CATALOG:
        for follow_up_id in question.follow_up_ids:
            assert follow_up_id in all_ids


def test_deprecated_questions_are_absent_from_curated_catalog():
    all_curated_text = {question.text for question in CATALOG}
    followup_text = {
        match_curated_question(question.text).text
        for question in CATALOG
        if match_curated_question(question.text) is not None
    }
    for question in DEPRECATED_QUESTIONS:
        assert question not in all_curated_text
        assert question not in followup_text
        assert not is_answerable_followup(question)


def test_replacement_wording_maps_to_curated_handlers_without_being_display_text():
    assert match_curated_question("What is today's idle running cost?").id == "total_idle_loss_today"
    assert match_curated_question("Which machine has the highest idle cost today?").id == "highest_loss_machine_today"
    assert match_curated_question("Show energy trend for this machine").id == "device_power_trend_last_7_days"
    assert match_curated_question("Show performance trend for this machine").id == "device_power_trend_last_7_days"


def test_every_curated_question_has_a_deterministic_engine_handler():
    handler_names = {
        name
        for name in dir(CopilotEngine)
        if name.startswith("_answer_")
    }
    for question in CATALOG:
        assert f"_answer_{question.handler}" in handler_names


def test_ui_uses_backend_curated_questions_endpoint_and_real_page_path():
    page = Path(__file__).resolve().parents[2] / "../ui-web/app/(protected)/copilot/page.tsx"
    page = page.resolve(strict=True)
    content = page.read_text()

    assert "fetchCuratedStarterQuestions" in content
    assert "/api/v1/copilot/curated-questions" not in content
    assert "What is today's idle running cost?" not in content
    assert "Which machine consumed the most power today?" not in content
