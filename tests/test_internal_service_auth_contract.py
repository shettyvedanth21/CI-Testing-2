from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
import yaml

from services.shared.startup_contract import validate_startup_contract


def test_internal_service_headers_require_explicit_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTERNAL_SERVICE_SHARED_SECRET", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-secret-that-must-not-sign-internal-requests")

    tenant_context = importlib.import_module("services.shared.tenant_context")

    with pytest.raises(RuntimeError, match="Internal service authentication secret is not configured"):
        tenant_context.build_internal_headers("analytics-service", "tenant-a")


def test_startup_contract_requires_internal_service_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "j" * 32)
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://user:pass@mysql:3306/app")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.delenv("INTERNAL_SERVICE_SHARED_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="INTERNAL_SERVICE_SHARED_SECRET"):
        validate_startup_contract()


def test_compose_wires_internal_service_shared_secret_for_all_internal_auth_services() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = compose["services"]
    expected = {
        "analytics-service",
        "analytics-worker",
        "analytics-worker-2",
        "auth-service",
        "copilot-service",
        "data-export-service",
        "data-service",
        "data-telemetry-worker",
        "data-telemetry-worker-2",
        "device-service",
        "energy-service",
        "reporting-service",
        "reporting-worker",
        "rule-engine-service",
        "rule-engine-worker",
        "waste-analysis-service",
    }

    for service_name in expected:
        env = services[service_name].get("environment") or {}
        if isinstance(env, list):
            keys = {entry.split("=", 1)[0] for entry in env if isinstance(entry, str)}
        else:
            keys = set(env.keys())
        assert "INTERNAL_SERVICE_SHARED_SECRET" in keys, service_name
