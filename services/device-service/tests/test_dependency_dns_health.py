from pathlib import Path
import importlib
import os
import sys
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parents[1]
BASE_DIR_STR = str(BASE_DIR)
if BASE_DIR_STR in sys.path:
    sys.path.remove(BASE_DIR_STR)
sys.path.insert(0, BASE_DIR_STR)
sys.modules.pop("app", None)

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("DATA_SERVICE_BASE_URL", "http://data-service:8081")
os.environ.setdefault("RULE_ENGINE_SERVICE_BASE_URL", "http://rule-engine-service:8002")
os.environ.setdefault("REPORTING_SERVICE_BASE_URL", "http://reporting-service:8085")
os.environ.setdefault("ENERGY_SERVICE_BASE_URL", "http://energy-service:8010")

app_module = importlib.import_module("app")
from app import config as config_module


def test_health_exposes_dependency_dns_failures_without_blocking_startup(monkeypatch):
    broadcaster = type("Broadcaster", (), {"start": AsyncMock(), "stop": AsyncMock()})()
    monkeypatch.setattr(config_module.socket, "getaddrinfo", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("dns failed")))
    monkeypatch.setattr(app_module, "validate_startup_contract", lambda: None, raising=False)
    monkeypatch.setattr(app_module, "configure_logging", lambda: None, raising=False)
    monkeypatch.setattr(app_module, "fleet_stream_broadcaster", broadcaster, raising=False)
    monkeypatch.setattr(config_module.settings, "PERFORMANCE_TRENDS_CRON_ENABLED", False)
    monkeypatch.setattr(config_module.settings, "DASHBOARD_SNAPSHOT_ENABLED", False)
    monkeypatch.setattr(config_module.settings, "DASHBOARD_RECONCILE_INTERVAL_SECONDS", 0)

    with TestClient(app_module.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert "dependency_dns" in payload
    assert any(result.get("resolved") is False for result in payload["dependency_dns"].values())


def test_validate_dependency_dns_can_suppress_startup_failure_logs(monkeypatch):
    messages: list[str] = []

    def raising_getaddrinfo(*args, **kwargs):
        raise OSError("dns failed")

    def capture_critical(message, *args, **kwargs):
        messages.append(message % args)

    monkeypatch.setattr(config_module.socket, "getaddrinfo", raising_getaddrinfo)
    monkeypatch.setattr(config_module.logger, "critical", capture_critical)

    result = config_module.validate_dependency_dns(log_failures=False)

    assert messages == []
    assert any(entry.get("resolved") is False for entry in result.values())
