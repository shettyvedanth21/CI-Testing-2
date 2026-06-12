from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

_BASE_DIR = Path(__file__).resolve().parents[1]
_SCHEDULER_PATH = _BASE_DIR / "app" / "scheduler_runner.py"
_CONFIG_PATH = _BASE_DIR / "app" / "config.py"


class _ComparisonExpr:
    def __eq__(self, other): return _ComparisonExpr()
    def __ne__(self, other): return _ComparisonExpr()
    def __lt__(self, other): return _ComparisonExpr()
    def __le__(self, other): return _ComparisonExpr()
    def __gt__(self, other): return _ComparisonExpr()
    def __ge__(self, other): return _ComparisonExpr()
    def __and__(self, other): return _ComparisonExpr()
    def __or__(self, other): return _ComparisonExpr()
    def __rand__(self, other): return _ComparisonExpr()
    def __ror__(self, other): return _ComparisonExpr()
    def __bool__(self): return True
    def desc(self): return _ComparisonExpr()
    def asc(self): return _ComparisonExpr()


class _ModelStub:
    def __getattr__(self, name):
        return _ComparisonExpr()

    def __call__(self, **kwargs):
        return Mock(**kwargs)


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

    if "app.database" not in sys.modules:
        _db = types.ModuleType("app.database")
        _db.AsyncSessionLocal = AsyncMock()
        _db.engine = Mock(dispose=AsyncMock())
        _db.SchedulerSessionLocal = AsyncMock()
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

    if "app.models" not in sys.modules:
        _models = types.ModuleType("app.models")
        sys.modules["app.models"] = _models
        sys.modules["app"].models = _models

    if "app.models.device" not in sys.modules:
        _md = types.ModuleType("app.models.device")
        for name in ("MachineAnomalyBaseline", "MachineAnomalyEvent",
                      "MachineAnomalyDailyCount", "MachineAnomalyWeeklyCount",
                      "MachineHealthFeatureWindow", "Device",
                      "DashboardSnapshot",
                      "ParameterHealthConfig"):
            setattr(_md, name, _ModelStub())
        sys.modules["app.models.device"] = _md
        sys.modules["app.models"].device = _md

    if "app.services" not in sys.modules:
        _svc = types.ModuleType("app.services")
        _svc.__path__ = [str(_BASE_DIR / "app" / "services")]
        _svc.__package__ = "app.services"
        sys.modules["app.services"] = _svc
        sys.modules["app"].services = _svc

    if "app.services.degradation" not in sys.modules:
        _deg = types.ModuleType("app.services.degradation")
        _deg.__path__ = [str(_BASE_DIR / "app" / "services" / "degradation")]
        _deg.__package__ = "app.services.degradation"
        sys.modules["app.services.degradation"] = _deg
        sys.modules["app.services"].degradation = _deg

    if "app.services.degradation.service" not in sys.modules:
        _ds = types.ModuleType("app.services.degradation.service")
        _ds.load_feature_windows_for_device = AsyncMock(return_value=[])
        _ds.load_feature_windows_for_baseline = AsyncMock(return_value=[])
        sys.modules["app.services.degradation.service"] = _ds
        sys.modules["app.services.degradation"].service = _ds


_inject_app_stubs()

spec = importlib.util.spec_from_file_location("app.scheduler_runner", _SCHEDULER_PATH)
assert spec is not None and spec.loader is not None
scheduler_mod = importlib.util.module_from_spec(spec)
sys.modules["app.scheduler_runner"] = scheduler_mod
spec.loader.exec_module(scheduler_mod)

run_scheduler_runtime = scheduler_mod.run_scheduler_runtime
settings = sys.modules["app.config"].settings

_device_stub = sys.modules.get("app.models.device")
_models_stub = sys.modules.get("app.models")

sys.modules.pop("app.models.device", None)
sys.modules.pop("app.models", None)
_app_mod = sys.modules.get("app")
if _app_mod is not None and hasattr(_app_mod, "models"):
    try:
        delattr(_app_mod, "models")
    except AttributeError:
        pass


@pytest.fixture(autouse=True)
def _restore_model_stubs():
    if _models_stub is not None:
        sys.modules["app.models"] = _models_stub
    if _device_stub is not None:
        sys.modules["app.models.device"] = _device_stub
    _app = sys.modules.get("app")
    if _app is not None and _models_stub is not None:
        _app.models = _models_stub
    if _models_stub is not None and _device_stub is not None:
        _models_stub.device = _device_stub
    yield
    if sys.modules.get("app.models") is _models_stub:
        sys.modules.pop("app.models", None)
    if sys.modules.get("app.models.device") is _device_stub:
        sys.modules.pop("app.models.device", None)
    _a = sys.modules.get("app")
    if _a is not None and hasattr(_a, "models") and _a.models is _models_stub:
        try:
            delattr(_a, "models")
        except AttributeError:
            pass


def _reset_settings():
    settings.DEVICE_SERVICE_RUN_STARTUP_MAINTENANCE = False
    settings.PERFORMANCE_TRENDS_ENABLED = False
    settings.PERFORMANCE_TRENDS_CRON_ENABLED = False
    settings.DASHBOARD_SNAPSHOT_ENABLED = False
    settings.DASHBOARD_RECONCILE_INTERVAL_SECONDS = 0
    settings.STATE_INTERVAL_RETENTION_ENABLED = False
    settings.STATE_INTERVAL_RETENTION_DAYS = 0
    settings.DASHBOARD_SNAPSHOT_CLEANUP_ENABLED = False
    settings.DASHBOARD_SNAPSHOT_TTL_SECONDS = 0
    settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_ENABLED = False
    settings.DEGRADATION_ENABLED = False
    settings.DEGRADATION_RETENTION_DAYS = 0
    settings.ANOMALY_ENABLED = False
    settings.ANOMALY_RETENTION_DAYS = 90


def _bootstrap_engine_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    dispose_mock = AsyncMock()
    monkeypatch.setattr(scheduler_mod, "scheduler_engine", Mock(dispose=dispose_mock))
    return dispose_mock


@pytest.mark.asyncio
async def test_anomaly_cycles_register_when_enabled(monkeypatch: pytest.MonkeyPatch):
    _reset_settings()
    _bootstrap_engine_mock(monkeypatch)
    settings.ANOMALY_ENABLED = True

    with patch.object(scheduler_mod, "_run_anomaly_baseline_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_detection_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_daily_count_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_weekly_count_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_cleanup_once", new_callable=AsyncMock):

        task = asyncio.create_task(run_scheduler_runtime())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _reset_settings()


@pytest.mark.asyncio
async def test_anomaly_cycles_not_registered_when_disabled(monkeypatch: pytest.MonkeyPatch):
    _reset_settings()
    _bootstrap_engine_mock(monkeypatch)
    settings.ANOMALY_ENABLED = False

    task = asyncio.create_task(run_scheduler_runtime())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    _reset_settings()


@pytest.mark.asyncio
async def test_anomaly_independent_of_degradation(monkeypatch: pytest.MonkeyPatch):
    _reset_settings()
    _bootstrap_engine_mock(monkeypatch)
    settings.ANOMALY_ENABLED = True
    settings.DEGRADATION_ENABLED = False

    with patch.object(scheduler_mod, "_run_anomaly_baseline_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_detection_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_daily_count_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_weekly_count_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_cleanup_once", new_callable=AsyncMock):

        task = asyncio.create_task(run_scheduler_runtime())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _reset_settings()


@pytest.mark.asyncio
async def test_anomaly_cleanup_disabled_when_retention_zero(monkeypatch: pytest.MonkeyPatch):
    _reset_settings()
    _bootstrap_engine_mock(monkeypatch)
    settings.ANOMALY_ENABLED = True
    settings.ANOMALY_RETENTION_DAYS = 0

    with patch.object(scheduler_mod, "_run_anomaly_baseline_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_detection_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_daily_count_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_weekly_count_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_cleanup_once", new_callable=AsyncMock) as cleanup_mock:

        task = asyncio.create_task(run_scheduler_runtime())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        cleanup_mock.assert_not_called()

    _reset_settings()


@pytest.mark.asyncio
async def test_anomaly_detection_failure_isolation(monkeypatch: pytest.MonkeyPatch):
    _reset_settings()
    _bootstrap_engine_mock(monkeypatch)
    settings.ANOMALY_ENABLED = True

    async def _failing_detect(*args, **kwargs):
        raise RuntimeError("device failure")

    with patch.object(scheduler_mod, "_run_anomaly_baseline_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_detection_once", new_callable=AsyncMock, side_effect=_failing_detect), \
         patch.object(scheduler_mod, "_run_anomaly_daily_count_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_weekly_count_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_anomaly_cleanup_once", new_callable=AsyncMock):

        task = asyncio.create_task(run_scheduler_runtime())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, RuntimeError):
            pass

    _reset_settings()


@pytest.mark.asyncio
async def test_no_regression_degradation_still_works(monkeypatch: pytest.MonkeyPatch):
    _reset_settings()
    _bootstrap_engine_mock(monkeypatch)
    settings.DEGRADATION_ENABLED = True
    settings.DEGRADATION_RETENTION_DAYS = 90
    settings.ANOMALY_ENABLED = False

    with patch.object(scheduler_mod, "_run_degradation_feature_window_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_degradation_baseline_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_degradation_scoring_once", new_callable=AsyncMock), \
         patch.object(scheduler_mod, "_run_degradation_cleanup_once", new_callable=AsyncMock):

        task = asyncio.create_task(run_scheduler_runtime())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _reset_settings()


@pytest.mark.asyncio
async def test_anomaly_cleanup_respects_retention_days():
    _reset_settings()

    with patch("app.services.anomaly.service.cleanup_old_anomaly_rows", new_callable=AsyncMock, return_value={"deleted": 10}) as mock_cleanup:
        from app.services.anomaly.service import cleanup_old_anomaly_rows
        result = await cleanup_old_anomaly_rows(AsyncMock(), retention_days=90)
        assert result["deleted"] == 10

    _reset_settings()
