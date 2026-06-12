from dataclasses import dataclass

from src.templates.curated_catalog import match_curated_question


@dataclass
class IntentResult:
    intent: str
    confidence: float


INTENT_PATTERNS: dict[str, list[str]] = {
    "unsupported": ["oee", "overall equipment effectiveness", "yield", "production count"],
    "top_energy_today": ["most power today", "most energy today", "consumed the most power", "top energy consumers"],
    "factory_summary": ["summarize", "factory performance", "overview today", "top problems"],
    "alerts_recent": ["recent alerts", "alerts today", "rules triggered", "anomalies today", "most alerts"],
    "idle_waste": ["idle cost", "idle running", "standby loss", "waste energy"],
    "health_scores": ["health score", "lowest efficiency", "below 80% efficiency"],
    "telemetry_trend": ["trend", "last 30 days", "spike", "at 3pm", "over time", "yesterday"],
}


def classify_intent(message: str, history: list[dict]) -> IntentResult:
    msg = message.lower()
    for intent, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            if pattern in msg:
                return IntentResult(intent=intent, confidence=0.9)

    if history:
        return IntentResult(intent="ai_sql_with_context", confidence=0.5)
    return IntentResult(intent="ai_sql", confidence=0.4)


def is_answerable_followup(question: str) -> bool:
    return match_curated_question(question) is not None
