import importlib
import sys
import types


def _reload_model_modules(monkeypatch, *, groq_model: str | None):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    if groq_model is None:
        monkeypatch.delenv("GROQ_MODEL", raising=False)
    else:
        monkeypatch.setenv("GROQ_MODEL", groq_model)

    fake_groq = types.ModuleType("groq")

    class FakeGroq:
        def __init__(self, api_key: str):
            self.api_key = api_key

    fake_groq.Groq = FakeGroq
    monkeypatch.setitem(sys.modules, "groq", fake_groq)

    import src.config as config_module
    import src.ai.model_client as model_client_module

    importlib.reload(config_module)
    importlib.reload(model_client_module)
    return model_client_module.ModelClient()


def test_groq_uses_supported_default_model(monkeypatch):
    client = _reload_model_modules(monkeypatch, groq_model=None)
    assert client.model == "llama-3.3-70b-versatile"


def test_groq_model_override_is_respected(monkeypatch):
    client = _reload_model_modules(monkeypatch, groq_model="openai/gpt-oss-120b")
    assert client.model == "openai/gpt-oss-120b"
