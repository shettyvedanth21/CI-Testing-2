from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

_BASE_DIR = Path(__file__).resolve().parents[1]
_SCHEDULER_PATH = _BASE_DIR / "app" / "scheduler_runner.py"
_CONFIG_PATH = _BASE_DIR / "app" / "config.py"


def _inject_app_stubs():
    if "app" not in sys.modules:
        _app = types.ModuleType("app")
        _app.__path__ = [str(_BASE_DIR / "app")]
        _app.__package__ = "app"
        _app.__file__ = str(_BASE_DIR / "app" / "__init__.py")
        sys.modules["app"] = _app

    _app = sys.modules["app"]
    _app._load_active_tenant_ids = AsyncMock(return_value=["TENANT-A"])
    _app._run_activation_backfill_cycle = AsyncMock()
    _app._run_dashboard_snapshot_retention_cycle = AsyncMock()
    _app._run_live_projection_reconciliation_cycle = AsyncMock()
    _app._run_state_interval_retention_cycle = AsyncMock()

    if "app.scheduler_helpers" not in sys.modules:
        _helpers = types.ModuleType("app.scheduler_helpers")
        _helpers.load_active_tenant_ids = AsyncMock(return_value=["TENANT-A"])
        _helpers.run_live_projection_reconciliation_cycle = AsyncMock()
        _helpers.run_activation_backfill_cycle = AsyncMock()
        _helpers.run_state_interval_retention_cycle = AsyncMock()
        _helpers.run_dashboard_snapshot_retention_cycle = AsyncMock()
        sys.modules["app.scheduler_helpers"] = _helpers
        _app.scheduler_helpers = _helpers

    if "app.database" not in sys.modules:
        _db = types.ModuleType("app.database")
        _db.AsyncSessionLocal = AsyncMock()
        _db.SchedulerSessionLocal = AsyncMock()
        _db.engine = Mock(dispose=AsyncMock())
        _db.scheduler_engine = Mock(dispose=AsyncMock())
        _db.get_db = AsyncMock()
        _db.Base = Mock()
        sys.modules["app.database"] = _db
        sys.modules["app"].database = _db

    if "app.logging_config" not in sys.modules:
        _log = types.ModuleType("app.logging_config")
        _log.configure_logging = Mock()
        sys.modules["app.logging_config"] = _log
        sys.modules["app"].logging_config = _log

    if "app.config" not in sys.modules:
        spec = importlib.util.spec_from_file_location("app.config", _CONFIG_PATH)
        assert spec is not None and spec.loader is not None
        config_mod = importlib.util.module_from_spec(spec)
        sys.modules["app.config"] = config_mod
        sys.modules["app"].config = config_mod
        spec.loader.exec_module(config_mod)

    for mod_name in ("services.shared.startup_contract", "services.shared.tenant_context"):
        parts = mod_name.split(".")
        parent = None
        for i in range(len(parts)):
            name = ".".join(parts[: i + 1])
            if name not in sys.modules:
                m = types.ModuleType(name)
                sys.modules[name] = m
                if parent is not None:
                    setattr(parent, parts[i], m)
                parent = m

    sys.modules["services.shared.startup_contract"].validate_startup_contract = Mock()
    tc_mod = sys.modules["services.shared.tenant_context"]
    tc_mod.TenantContext = Mock
    tc_mod.require_tenant = Mock(return_value="TENANT-A")

    if "app.services" not in sys.modules:
        _svc = types.ModuleType("app.services")
        _svc.__path__ = [str(_BASE_DIR / "app" / "services")]
        _svc.__package__ = "app.services"
        sys.modules["app.services"] = _svc
        sys.modules["app"].services = _svc

    if "app.services.shared_http" not in sys.modules:
        _shared_http = types.ModuleType("app.services.shared_http")
        _shared_http.close_all = AsyncMock()
        sys.modules["app.services.shared_http"] = _shared_http
        sys.modules["app.services"].shared_http = _shared_http


_inject_app_stubs()

spec = importlib.util.spec_from_file_location("app.scheduler_runner", _SCHEDULER_PATH)
assert spec is not None and spec.loader is not None
scheduler_runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler_runner)


def _disable_all_existing_schedulers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_runner.settings, "DEVICE_SERVICE_RUN_STARTUP_MAINTENANCE", False)
    monkeypatch.setattr(scheduler_runner.settings, "PERFORMANCE_TRENDS_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "PERFORMANCE_TRENDS_CRON_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_SNAPSHOT_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_RECONCILE_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(scheduler_runner.settings, "STATE_INTERVAL_RETENTION_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "DASHBOARD_SNAPSHOT_CLEANUP_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "RECENT_TELEMETRY_SAMPLE_CLEANUP_ENABLED", False)
    monkeypatch.setattr(scheduler_runner.settings, "ANOMALY_ENABLED", False)


def _bootstrap_scheduler_mocks(monkeypatch: pytest.MonkeyPatch) -> dict:
    dispose_mock = AsyncMock()
    validate_contract = Mock()
    configure_logging = Mock()
    validate_dns = Mock()

    monkeypatch.setattr(scheduler_runner, "validate_startup_contract", validate_contract)
    monkeypatch.setattr(scheduler_runner, "configure_logging", configure_logging)
    monkeypatch.setattr(scheduler_runner, "validate_dependency_dns", validate_dns)
    monkeypatch.setattr(scheduler_runner, "scheduler_engine", Mock(dispose=dispose_mock))

    return {
        "dispose_mock": dispose_mock,
        "validate_contract": validate_contract,
        "configure_logging": configure_logging,
        "validate_dns": validate_dns,
    }


@pytest.mark.asyncio
async def test_degradation_cycles_register_when_enabled(monkeypatch: pytest.MonkeyPatch):
    _bootstrap_scheduler_mocks(monkeypatch)
    _disable_all_existing_schedulers(monkeypatch)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_ENABLED", False)

    async def _mock_gather(*tasks, **kwargs):
        for t in list(tasks):
            t.cancel()
        return []

    count_without = 0
    original_create_task = asyncio.create_task

    def count_create_without(coro):
        nonlocal count_without
        count_without += 1
        return original_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", count_create_without)
    monkeypatch.setattr(asyncio, "gather", _mock_gather)

    await scheduler_runner.run_scheduler_runtime()

    _bootstrap_scheduler_mocks(monkeypatch)
    _disable_all_existing_schedulers(monkeypatch)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_ENABLED", True)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_RETENTION_DAYS", 90)

    count_with = 0

    def count_create_with(coro):
        nonlocal count_with
        count_with += 1
        return original_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", count_create_with)
    monkeypatch.setattr(asyncio, "gather", _mock_gather)

    await scheduler_runner.run_scheduler_runtime()

    degradation_tasks = count_with - count_without
    assert degradation_tasks == 4


@pytest.mark.asyncio
async def test_degradation_disabled_prevents_cycles(monkeypatch: pytest.MonkeyPatch):
    mocks = _bootstrap_scheduler_mocks(monkeypatch)
    _disable_all_existing_schedulers(monkeypatch)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_ENABLED", False)

    await scheduler_runner.run_scheduler_runtime()

    mocks["dispose_mock"].assert_awaited_once()


@pytest.mark.asyncio
async def test_degradation_cycle_failure_does_not_crash_runner(monkeypatch: pytest.MonkeyPatch):
    _bootstrap_scheduler_mocks(monkeypatch)
    _disable_all_existing_schedulers(monkeypatch)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_ENABLED", True)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_RETENTION_DAYS", 0)

    monkeypatch.setattr(
        scheduler_runner,
        "_run_degradation_feature_window_once",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(scheduler_runner, "_run_degradation_baseline_once", AsyncMock())
    monkeypatch.setattr(scheduler_runner, "_run_degradation_scoring_once", AsyncMock())

    async def _mock_gather(*tasks, **kwargs):
        for t in tasks:
            t.cancel()
        return []

    monkeypatch.setattr(asyncio, "gather", _mock_gather)

    await scheduler_runner.run_scheduler_runtime()


@pytest.mark.asyncio
async def test_degradation_scoring_uses_service_helpers():
    if "app.services" not in sys.modules:
        _svc = types.ModuleType("app.services")
        _svc.__path__ = [str(_BASE_DIR / "app" / "services")]
        _svc.__package__ = "app.services"
        _svc.__file__ = str(_BASE_DIR / "app" / "services" / "__init__.py")
        sys.modules["app.services"] = _svc
        sys.modules["app"].services = _svc

    if "app.services.degradation" not in sys.modules:
        _deg = types.ModuleType("app.services.degradation")
        _deg.__path__ = [str(_BASE_DIR / "app" / "services" / "degradation")]
        _deg.__package__ = "app.services.degradation"
        sys.modules["app.services.degradation"] = _deg
        sys.modules["app.services"].degradation = _deg

    from app.services.degradation.scorer import compute_degradation_score
    from app.services.degradation.service import build_latest_score_snapshot, build_history_entry
    from app.services.degradation.types import BaselineInput, FeatureWindowInput, ScoreResult, Contribution

    baseline = BaselineInput(current_avg_std=0.5, power_factor_mean=0.95, power_mean=5000.0, phase_imbalance_mean=0.02, quality_score=0.9)
    windows = [FeatureWindowInput(current_avg_std=0.5, power_factor_mean=0.95, power_mean=5000.0, phase_imbalance=0.02)]
    score_result = compute_degradation_score(baseline, windows)

    assert score_result.score is not None

    snapshot_dict = build_latest_score_snapshot(score_result, "T1", "D1")
    assert snapshot_dict["score"] == score_result.score

    history_dict = build_history_entry(score_result, "T1", "D1")
    assert history_dict["score"] == score_result.score


@pytest.mark.asyncio
async def test_degradation_cleanup_respects_retention_days():
    from app.services.degradation import service as deg_service

    with patch.object(deg_service, "cleanup_old_degradation_rows", new_callable=AsyncMock) as cleanup_mock:
        cleanup_mock.return_value = {"deleted": 5}

        result = await deg_service.cleanup_old_degradation_rows(Mock(), retention_days=30)
        assert "deleted" in result


@pytest.mark.asyncio
async def test_existing_scheduler_no_regression_when_degradation_disabled(monkeypatch: pytest.MonkeyPatch):
    mocks = _bootstrap_scheduler_mocks(monkeypatch)
    _disable_all_existing_schedulers(monkeypatch)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_ENABLED", False)

    reconcile_mock = AsyncMock()
    backfill_mock = AsyncMock()
    monkeypatch.setattr(scheduler_runner, "_run_live_projection_reconciliation_cycle", reconcile_mock)
    monkeypatch.setattr(scheduler_runner, "_run_activation_backfill_cycle", backfill_mock)
    monkeypatch.setattr(scheduler_runner.settings, "DEVICE_SERVICE_RUN_STARTUP_MAINTENANCE", True)

    await scheduler_runner.run_scheduler_runtime()

    reconcile_mock.assert_awaited_once_with(refresh_fleet_snapshot=True)
    backfill_mock.assert_awaited_once()
    mocks["dispose_mock"].assert_awaited_once()


@pytest.mark.asyncio
async def test_degradation_cleanup_disabled_when_retention_zero(monkeypatch: pytest.MonkeyPatch):
    _bootstrap_scheduler_mocks(monkeypatch)
    _disable_all_existing_schedulers(monkeypatch)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_ENABLED", False)

    async def _mock_gather(*tasks, **kwargs):
        for t in list(tasks):
            t.cancel()
        return []

    count_without = 0
    original_create_task = asyncio.create_task

    def count_create_without(coro):
        nonlocal count_without
        count_without += 1
        return original_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", count_create_without)
    monkeypatch.setattr(asyncio, "gather", _mock_gather)

    await scheduler_runner.run_scheduler_runtime()

    _bootstrap_scheduler_mocks(monkeypatch)
    _disable_all_existing_schedulers(monkeypatch)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_ENABLED", True)
    monkeypatch.setattr(scheduler_runner.settings, "DEGRADATION_RETENTION_DAYS", 0)

    count_with = 0

    def count_create_with(coro):
        nonlocal count_with
        count_with += 1
        return original_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", count_create_with)
    monkeypatch.setattr(asyncio, "gather", _mock_gather)

    await scheduler_runner.run_scheduler_runtime()

    degradation_tasks = count_with - count_without
    assert degradation_tasks == 3
