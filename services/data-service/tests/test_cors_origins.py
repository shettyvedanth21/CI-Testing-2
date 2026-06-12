from __future__ import annotations

import importlib.util
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _load_cors_module():
    spec = importlib.util.spec_from_file_location(
        "data_service_cors",
        SERVICE_ROOT / "src" / "config" / "cors.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_allowed_origins_includes_local_and_configured_public_urls(monkeypatch):
    monkeypatch.setenv("EXTERNAL_URL", "https://api.shivex.ai")
    monkeypatch.setenv("NEXT_PUBLIC_API_URL", "https://shivex.ai")

    module = _load_cors_module()
    origins = module.build_allowed_origins(
        "https://shivex.ai",
        "https://app.shivex.ai, https://portal.shivex.ai/",
    )

    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins
    assert "https://shivex.ai" in origins
    assert "https://api.shivex.ai" in origins
    assert "https://app.shivex.ai" in origins
    assert "https://portal.shivex.ai" in origins
    assert "*" not in origins
