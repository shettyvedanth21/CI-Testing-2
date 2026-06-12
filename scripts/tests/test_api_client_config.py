from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "helpers" / "api_client.py"
)
SPEC = importlib.util.spec_from_file_location("tests.helpers.api_client_runtime", MODULE_PATH)
assert SPEC and SPEC.loader
api_client = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = api_client
SPEC.loader.exec_module(api_client)


def test_get_auth_settings_prefers_certification_env(monkeypatch):
    monkeypatch.setenv("CERTIFY_STACK_AUTH_URL", "http://auth.local")
    monkeypatch.setenv("CERTIFY_STACK_EMAIL", "seed@example.com")
    monkeypatch.setenv("CERTIFY_STACK_PASSWORD", "seed-secret")

    auth_url, email, password = api_client.get_auth_settings()

    assert auth_url == "http://auth.local"
    assert email == "seed@example.com"
    assert password == "seed-secret"


def test_login_cache_keys_include_effective_auth_config(monkeypatch):
    captured = []
    api_client._LOGIN_CACHE.clear()

    class FakeResponse:
        def __init__(self, token: str):
            self._token = token

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"access_token": self._token}

    def fake_post(url: str, json: dict[str, str], timeout: int):
        captured.append((url, json["email"], json["password"], timeout))
        return FakeResponse(f"token-{len(captured)}")

    monkeypatch.setattr(api_client.httpx, "post", fake_post)
    monkeypatch.setenv("CERTIFY_STACK_AUTH_URL", "http://auth-one.local")
    monkeypatch.setenv("CERTIFY_STACK_EMAIL", "admin-one@example.com")
    monkeypatch.setenv("CERTIFY_STACK_PASSWORD", "secret-one")
    first = api_client._login_super_admin()
    second = api_client._login_super_admin()

    monkeypatch.setenv("CERTIFY_STACK_AUTH_URL", "http://auth-two.local")
    monkeypatch.setenv("CERTIFY_STACK_EMAIL", "admin-two@example.com")
    monkeypatch.setenv("CERTIFY_STACK_PASSWORD", "secret-two")
    third = api_client._login_super_admin()

    assert first == second
    assert third != first
    assert captured == [
        ("http://auth-one.local/api/v1/auth/login", "admin-one@example.com", "secret-one", api_client.TIMEOUT_SHORT),
        ("http://auth-two.local/api/v1/auth/login", "admin-two@example.com", "secret-two", api_client.TIMEOUT_SHORT),
    ]


def test_waste_download_pdf_resolves_relative_download_urls(monkeypatch):
    class FakeJsonResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"download_url": "/api/waste/analysis/JOB-1/file"}

    class FakeHttpResponse:
        content = b"%PDF-test"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        base_url = "http://localhost:8087"
        
        def __init__(self) -> None:
            self.requested_urls: list[str] = []

        def get(self, path: str):
            self.requested_urls.append(path)
            if path == "/api/v1/waste/analysis/JOB-1/download":
                return FakeJsonResponse()
            assert path == "http://localhost:8087/api/v1/waste/analysis/JOB-1/file"
            return FakeHttpResponse()

    waste_client = api_client.WasteClient.__new__(api_client.WasteClient)
    fake_client = FakeClient()

    waste_client.c = fake_client
    pdf = waste_client.download_pdf("JOB-1")

    assert pdf == b"%PDF-test"
    assert fake_client.requested_urls == [
        "/api/v1/waste/analysis/JOB-1/download",
        "http://localhost:8087/api/v1/waste/analysis/JOB-1/file",
    ]
