from src.intent.router import classify_intent, is_answerable_followup


def test_classify_top_energy():
    result = classify_intent("Which machine consumed the most power today?", [])
    assert result.intent == "top_energy_today"


def test_classify_unsupported():
    result = classify_intent("Show OEE by line", [])
    assert result.intent == "unsupported"


def test_followup_filter():
    assert is_answerable_followup("Show this machine trend for last 7 days")
    assert not is_answerable_followup("Show unresolved alerts only")
    assert not is_answerable_followup("Compute OEE for all shifts")
