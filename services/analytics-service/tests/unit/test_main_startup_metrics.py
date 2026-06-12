from __future__ import annotations

import builtins
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from tests._bootstrap import bootstrap_test_imports


bootstrap_test_imports()

from src.models.schemas import JobStatus


REPO_ROOT = Path(__file__).resolve().parents[4]
REQUIREMENTS_API_PATH = REPO_ROOT / "services" / "analytics-service" / "requirements.api.txt"


def _import_main_without_prometheus(monkeypatch: pytest.MonkeyPatch):
    sys.modules.pop("src.main", None)
    for module_name in ("tensorflow", "torch", "xgboost", "sklearn", "prophet", "shap"):
        sys.modules.pop(module_name, None)
    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "prometheus_client":
            raise ImportError("simulated missing prometheus client")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    return importlib.import_module("src.main")


def _import_main_with_installed_prometheus():
    sys.modules.pop("src.main", None)
    for module_name in ("tensorflow", "torch", "xgboost", "sklearn", "prophet", "shap"):
        sys.modules.pop(module_name, None)
    return importlib.import_module("src.main")


@pytest.mark.asyncio
async def test_lifespan_boots_without_prometheus_client(monkeypatch: pytest.MonkeyPatch):
    main = _import_main_without_prometheus(monkeypatch)

    monkeypatch.setattr(main, "validate_startup_contract", lambda: None)
    monkeypatch.setattr(main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(main, "cleanup_stale_jobs", AsyncMock(return_value=0))
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(
            log_level="INFO",
            queue_backend="memory",
            queue_max_length=10,
            app_role="api",
            ml_weekly_retrainer_enabled=False,
            retention_enabled=False,
            retention_interval_seconds=300,
        ),
    )

    app = main.create_app()

    async with main.lifespan(app):
        assert app.state.prom_counters == {
            "tenant_cap": None,
            "overloaded": None,
        }


def test_requirements_api_includes_prometheus_client() -> None:
    lines = [
        line.strip()
        for line in REQUIREMENTS_API_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert any(line.startswith("prometheus-client") for line in lines)


@pytest.mark.asyncio
async def test_metrics_route_returns_prometheus_payload(monkeypatch: pytest.MonkeyPatch):
    main = _import_main_with_installed_prometheus()

    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(redis_url=None, worker_heartbeat_ttl_seconds=30))
    monkeypatch.setattr(
        main,
        "async_session_maker",
        lambda: _SessionCtx(),
    )
    monkeypatch.setattr(
        "src.infrastructure.mysql_repository.MySQLResultRepository",
        lambda _session: SimpleNamespace(
            count_jobs=AsyncMock(
                side_effect=lambda statuses=None, attempts_gte=None: _count_jobs(statuses=statuses, attempts_gte=attempts_gte)
            )
        ),
    )

    app = main.create_app()
    app.state.job_queue = SimpleNamespace(metrics=AsyncMock(return_value={"queued_messages": 3, "claimed_messages": 1, "dead_letter_messages": 0}))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "analytics_queue_depth" in response.text
    assert "analytics_processing_job_count" in response.text
    assert "analytics_active_workers" in response.text
    assert '"error"' not in response.text


@pytest.mark.asyncio
async def test_metrics_route_fails_loudly_when_prometheus_runtime_missing(monkeypatch: pytest.MonkeyPatch):
    main = _import_main_without_prometheus(monkeypatch)

    app = main.create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/metrics")

    assert response.status_code == 500
    assert response.json()["error"] == "METRICS_RUNTIME_UNAVAILABLE"


def _count_jobs(*, statuses=None, attempts_gte=None):
    if attempts_gte is not None:
        return 1
    status_values = tuple(statuses or [])
    if status_values == (JobStatus.PENDING.value,):
        return 4
    if status_values == (JobStatus.RUNNING.value,):
        return 2
    if status_values == (JobStatus.FAILED.value,):
        return 1
    return 0


class _Result:
    def scalars(self):
        return self

    def all(self):
        return [SimpleNamespace(worker_id="worker-a"), SimpleNamespace(worker_id="worker-b")]


class _Session:
    async def execute(self, _query):
        return _Result()


class _SessionCtx:
    async def __aenter__(self):
        return _Session()

    async def __aexit__(self, exc_type, exc, tb):
        return False
