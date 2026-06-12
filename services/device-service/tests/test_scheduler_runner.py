from pathlib import Path
from types import SimpleNamespace
import os
import sys
from unittest.mock import AsyncMock, Mock

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 48)
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "y" * 48)

from app import scheduler_runner


@pytest.mark.asyncio
async def test_scheduler_runtime_exits_cleanly_when_no_jobs_enabled(monkeypatch: pytest.MonkeyPatch):
    dispose_mock = AsyncMock()
    validate_contract = Mock()
    configure_logging = Mock()
    validate_dns = Mock()

    monkeypatch.setattr(scheduler_runner, "validate_startup_contract", validate_contract)
    monkeypatch.setattr(scheduler_runner, "configure_logging", configure_logging)
    monkeypatch.setattr(scheduler_runner, "validate_dependency_dns", validate_dns)
    monkeypatch.setattr(scheduler_runner, "scheduler_engine", SimpleNamespace(dispose=dispose_mock))

    monkeypatch.setattr(scheduler_runner.settings, "DEVICE_SERVICE_RUN_STARTUP_MAINTENANCE", False)
    monkeypatch.setattr(scheduler_runner.settings, "PERFORMANCE_TRENDS_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "PERFORMANCE_TRENDS_CRON_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_SNAPSHOT_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_RECONCILE_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(scheduler_runner.settings, "STATE_INTERVAL_RETENTION_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_SNAPSHOT_CLEANUP_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "RECENT_TELEMETRY_SAMPLE_CLEANUP_ENABLED", False)

    await scheduler_runner.run_scheduler_runtime()

    validate_contract.assert_called_once_with()
    configure_logging.assert_called_once_with()
    validate_dns.assert_called_once_with(log_failures=False)
    dispose_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_runtime_runs_startup_maintenance_before_exit(monkeypatch: pytest.MonkeyPatch):
    dispose_mock = AsyncMock()
    validate_contract = Mock()
    configure_logging = Mock()
    validate_dns = Mock()
    reconcile_mock = AsyncMock()
    backfill_mock = AsyncMock()

    monkeypatch.setattr(scheduler_runner, "validate_startup_contract", validate_contract)
    monkeypatch.setattr(scheduler_runner, "configure_logging", configure_logging)
    monkeypatch.setattr(scheduler_runner, "validate_dependency_dns", validate_dns)
    monkeypatch.setattr(scheduler_runner, "scheduler_engine", SimpleNamespace(dispose=dispose_mock))
    monkeypatch.setattr(scheduler_runner, "_run_live_projection_reconciliation_cycle", reconcile_mock)
    monkeypatch.setattr(scheduler_runner, "_run_activation_backfill_cycle", backfill_mock)

    monkeypatch.setattr(scheduler_runner.settings, "DEVICE_SERVICE_RUN_STARTUP_MAINTENANCE", True)
    monkeypatch.setattr(scheduler_runner.settings, "PERFORMANCE_TRENDS_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "PERFORMANCE_TRENDS_CRON_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_SNAPSHOT_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_RECONCILE_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(scheduler_runner.settings, "STATE_INTERVAL_RETENTION_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_SNAPSHOT_CLEANUP_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "RECENT_TELEMETRY_SAMPLE_CLEANUP_ENABLED", False)

    await scheduler_runner.run_scheduler_runtime()

    reconcile_mock.assert_awaited_once_with(refresh_fleet_snapshot=True)
    backfill_mock.assert_awaited_once()
    dispose_mock.assert_awaited_once()
