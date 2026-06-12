from src.response.schema import CopilotResponse


def test_response_has_reasoning_field():
    res = CopilotResponse(answer="ok", reasoning="source + metric")
    assert res.reasoning
