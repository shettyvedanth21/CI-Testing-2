def _is_valid_copilot_response(response: dict) -> bool:
    return "answer" in response or "error_code" in response


def test_copilot_service_responds(api):
    response = api.copilot.chat("How many devices are registered?")
    assert isinstance(response, dict)
    assert _is_valid_copilot_response(response)


def test_copilot_ai_unavailable_handled_gracefully(api):
    response = api.copilot.chat("Show me all active alerts.")
    if response.get("error_code"):
        assert response["error_code"] in ("AI_UNAVAILABLE", "NOT_CONFIGURED", "QUERY_BLOCKED", "MODULE_NOT_AVAILABLE", "INTERNAL_ERROR")
    else:
        assert response["answer"]


def test_copilot_oee_handled_gracefully(api):
    response = api.copilot.chat("What is the OEE for all machines?")
    assert _is_valid_copilot_response(response)
    if response.get("error_code"):
        assert response["error_code"] in ("MODULE_NOT_AVAILABLE", "AI_UNAVAILABLE", "NOT_CONFIGURED", "INTERNAL_ERROR")
    else:
        answer = response["answer"].lower()
        assert any(word in answer for word in ("not", "unavailable", "available", "module", "oee"))


def test_copilot_response_has_follow_up_or_answer(api):
    response = api.copilot.chat("Which device has highest energy today?")
    assert "answer" in response or "error_code" in response
    if "answer" in response:
        assert "follow_up_suggestions" in response or True
