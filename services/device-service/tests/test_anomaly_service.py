"""Tests for app.services.anomaly.service orchestration helpers.

Strategy: patch sqlalchemy select/delete/and_ inside the service module so
that MagicMock model classes never reach real SQLAlchemy coercion logic.
db.execute is mocked to return appropriate result objects.
"""

from __future__ import annotations

import json
import sys
import types
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, patch

import pytest

_BASE_DIR = Path(__file__).resolve().parents[1]


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


def _build_model_device_stub():
    _md = types.ModuleType("app.models.device")
    for name in ("MachineAnomalyBaseline", "MachineAnomalyEvent",
                  "MachineAnomalyDailyCount", "MachineAnomalyWeeklyCount",
                  "MachineHealthFeatureWindow", "Device",
                  "ParameterHealthConfig"):
        setattr(_md, name, _ModelStub())
    return _md


def _build_models_stub(device_stub):
    _models = types.ModuleType("app.models")
    _models.device = device_stub
    return _models


def _ensure_app_stubs() -> dict:
    saved = {}
    for key in ("app", "app.services", "app.services.anomaly",
                "app.services.degradation", "app.services.degradation.service",
                "app.models", "app.models.device"):
        saved[key] = sys.modules.get(key)

    if "app" not in sys.modules:
        _app = types.ModuleType("app")
        _app.__path__ = [str(_BASE_DIR / "app")]
        _app.__package__ = "app"
        _app.__file__ = str(_BASE_DIR / "app" / "__init__.py")
        sys.modules["app"] = _app

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

    _device_stub = _build_model_device_stub()
    _models_stub = _build_models_stub(_device_stub)

    sys.modules["app.models.device"] = _device_stub
    sys.modules["app.models"] = _models_stub
    sys.modules["app"].models = _models_stub
    sys.modules["app.models"].device = _device_stub

    return saved


_SAVED_MODULES = _ensure_app_stubs()

from app.services.anomaly.types import AnomalyFieldBaseline, AnomalyCandidate, DailyCountResult
from app.services.anomaly.helpers import (
    build_anomaly_baseline_dict,
    build_anomaly_event_dict,
    build_daily_count_dict,
    build_weekly_count_dict,
)

_device_stub = sys.modules["app.models.device"]
_models_stub = sys.modules["app.models"]

sys.modules.pop("app.models.device", None)
sys.modules.pop("app.models", None)
_app_mod = sys.modules.get("app")
if _app_mod is not None and hasattr(_app_mod, "models"):
    try:
        delattr(_app_mod, "models")
    except AttributeError:
        pass


_SVC_MOD = "app.services.anomaly.service"


@pytest.fixture(autouse=True)
def _restore_model_stubs():
    sys.modules["app.models"] = _models_stub
    sys.modules["app.models.device"] = _device_stub
    _app = sys.modules.get("app")
    if _app is not None:
        _app.models = _models_stub
    _models_stub.device = _device_stub
    yield
    if sys.modules.get("app.models") is _models_stub:
        sys.modules.pop("app.models", None)
    if sys.modules.get("app.models.device") is _device_stub:
        sys.modules.pop("app.models.device", None)
    for _key in ("app.services.degradation.service", "app.services.degradation"):
        if _key in sys.modules and getattr(sys.modules[_key], "__file__", None) is None:
            sys.modules.pop(_key, None)
    _a = sys.modules.get("app")
    if _a is not None and hasattr(_a, "models") and _a.models is _models_stub:
        try:
            delattr(_a, "models")
        except AttributeError:
            pass


@contextmanager
def _patch_sqlalchemy():
    mock_query = MagicMock()
    mock_query.where.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query

    mock_and = MagicMock()

    with patch(f"{_SVC_MOD}.select", return_value=mock_query) as sel, \
         patch(f"{_SVC_MOD}.delete", return_value=mock_query) as del_p, \
         patch(f"{_SVC_MOD}.and_", side_effect=lambda *a, **kw: mock_and):
        yield mock_query


def _make_baseline(field_name="current_avg", mean=10.0, std=1.0, quality_score=0.9,
                   status="active", baseline_version=1):
    return AnomalyFieldBaseline(
        field_name=field_name,
        baseline_mean=mean,
        baseline_std=std,
        quality_score=quality_score,
        status=status,
        baseline_version=baseline_version,
    )


def _make_mock_row(**kwargs):
    row = Mock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _scalar_result(rows):
    m = Mock()
    m.scalars.return_value = Mock(all=Mock(return_value=rows))
    return m


def _scalar_one(val):
    m = Mock()
    m.scalar_one_or_none.return_value = val
    return m


@pytest.mark.asyncio
async def test_load_feature_windows_for_anomaly_uses_recent_windows_for_detection(monkeypatch):
    from app.services.anomaly import service as anomaly_service
    from app.services.degradation import service as degradation_service

    recent = [
        Mock(window_start=datetime(2026, 1, day, tzinfo=timezone.utc))
        for day in range(1, 6)
    ]
    baseline_loader = AsyncMock(return_value=[])
    recent_loader = AsyncMock(return_value=recent)

    monkeypatch.setattr(degradation_service, "load_feature_windows_for_baseline", baseline_loader)
    monkeypatch.setattr(degradation_service, "load_feature_windows_for_device", recent_loader)

    result = await anomaly_service.load_feature_windows_for_anomaly(Mock(), "TENANT-A", "DEVICE-A", limit=3)

    assert result == recent[-3:]
    recent_loader.assert_awaited_once()
    baseline_loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_feature_windows_for_anomaly_uses_multiday_windows_for_baseline(monkeypatch):
    from app.services.anomaly import service as anomaly_service
    from app.services.degradation import service as degradation_service

    baseline_windows = [Mock(window_start=datetime(2026, 1, 1, tzinfo=timezone.utc))]
    baseline_loader = AsyncMock(return_value=baseline_windows)
    recent_loader = AsyncMock(return_value=[])

    monkeypatch.setattr(degradation_service, "load_feature_windows_for_baseline", baseline_loader)
    monkeypatch.setattr(degradation_service, "load_feature_windows_for_device", recent_loader)

    result = await anomaly_service.load_feature_windows_for_anomaly(
        Mock(),
        "TENANT-A",
        "DEVICE-A",
        minimum_days=7,
        interval_seconds=300,
    )

    assert result == baseline_windows
    baseline_loader.assert_awaited_once_with(
        ANY,
        "TENANT-A",
        "DEVICE-A",
        minimum_days=7,
        interval_seconds=300,
    )
    recent_loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_anomaly_baselines_passes_multiday_window_settings(monkeypatch):
    from app.services.anomaly import service as anomaly_service

    baseline_windows = [
        types.SimpleNamespace(
            window=types.SimpleNamespace(
                current_avg_mean=10.0 + index,
                power_mean=None,
                power_factor_mean=None,
                voltage_avg_mean=None,
                phase_imbalance=None,
            ),
            running_state="STEADY_RUNNING",
            window_start=datetime(2026, 1, index + 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 1, index + 1, 0, 5, tzinfo=timezone.utc),
        )
        for index in range(8)
    ]
    loader = AsyncMock(return_value=baseline_windows)
    persister = AsyncMock(return_value=5)
    monkeypatch.setattr(anomaly_service, "load_feature_windows_for_anomaly", loader)
    monkeypatch.setattr(anomaly_service, "persist_anomaly_baselines", persister)

    result = await anomaly_service.refresh_anomaly_baselines_for_device(
        Mock(),
        "TENANT-A",
        "DEVICE-A",
        minimum_days=7,
        interval_seconds=300,
    )

    assert result == 5
    loader.assert_awaited_once_with(
        ANY,
        "TENANT-A",
        "DEVICE-A",
        minimum_days=7,
        interval_seconds=300,
    )
    persister.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_active_anomaly_baselines_returns_field_baselines():
    mock_row = _make_mock_row(
        field_name="current_avg", time_window="5min",
        baseline_mean=10.0, baseline_std=1.0, baseline_median=10.0,
        baseline_mad=1.0, baseline_p05=8.0, baseline_p95=12.0,
        reading_count=20, quality_score=0.9, learned_from_ts=None,
        learned_to_ts=None, status="active", baseline_version=1,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([mock_row]))

    with _patch_sqlalchemy():
        from app.services.anomaly.service import load_active_anomaly_baselines_for_device
        result = await load_active_anomaly_baselines_for_device(db, "t1", "d1")

    assert len(result) == 1
    assert result[0].field_name == "current_avg"
    assert result[0].status == "active"


@pytest.mark.asyncio
async def test_load_active_anomaly_baselines_picks_latest_version():
    r1 = _make_mock_row(field_name="current_avg", baseline_version=1, status="active", quality_score=0.5,
                        time_window="5min", baseline_mean=10.0, baseline_std=1.0,
                        baseline_median=10.0, baseline_mad=1.0, baseline_p05=8.0,
                        baseline_p95=12.0, reading_count=20, learned_from_ts=None,
                        learned_to_ts=None)
    r2 = _make_mock_row(field_name="current_avg", baseline_version=3, status="active", quality_score=0.9,
                        time_window="5min", baseline_mean=12.0, baseline_std=1.5,
                        baseline_median=12.0, baseline_mad=1.5, baseline_p05=9.0,
                        baseline_p95=15.0, reading_count=30, learned_from_ts=None,
                        learned_to_ts=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([r1, r2]))

    with _patch_sqlalchemy():
        from app.services.anomaly.service import load_active_anomaly_baselines_for_device
        result = await load_active_anomaly_baselines_for_device(db, "t1", "d1")

    assert len(result) == 1
    assert result[0].baseline_version == 3


@pytest.mark.asyncio
async def test_load_recent_anomaly_events_returns_candidates():
    mock_row = _make_mock_row(
        signal_field="current_avg", signal_value=14.0,
        baseline_mean=10.0, baseline_std=1.0, z_score=4.0,
        anomaly_type="deviation", severity="severe",
        confidence=0.9, supply_related=False, startup_adjacent=False,
        mode_change=False, recurring=False, time_window="5min",
        correlated_signals_json=None, baseline_version=1,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=None, duration_seconds=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([mock_row]))

    with _patch_sqlalchemy():
        from app.services.anomaly.service import load_recent_anomaly_events_for_device
        result = await load_recent_anomaly_events_for_device(db, "t1", "d1")

    assert len(result) == 1
    assert result[0].signal_field == "current_avg"


@pytest.mark.asyncio
async def test_load_recent_anomaly_events_parses_correlated_json():
    mock_row = _make_mock_row(
        signal_field="current_avg", signal_value=14.0,
        baseline_mean=10.0, baseline_std=1.0, z_score=4.0,
        anomaly_type="deviation", severity="severe",
        confidence=0.9, supply_related=False, startup_adjacent=False,
        mode_change=False, recurring=False, time_window="5min",
        correlated_signals_json=json.dumps(["power", "power_factor"]),
        baseline_version=1,
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=None, duration_seconds=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([mock_row]))

    with _patch_sqlalchemy():
        from app.services.anomaly.service import load_recent_anomaly_events_for_device
        result = await load_recent_anomaly_events_for_device(db, "t1", "d1")

    assert result[0].correlated_signals == ("power", "power_factor")


@pytest.mark.asyncio
async def test_persist_anomaly_event_inserts_when_no_duplicate():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    db.add = Mock()
    db.flush = AsyncMock()

    event_dict = {
        "tenant_id": "t1", "device_id": "d1",
        "signal_field": "current_avg",
        "occurred_at": datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_event
        inserted = await persist_anomaly_event(db, event_dict)

    assert inserted is True
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_persist_anomaly_event_skips_duplicate():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(42))
    db.add = Mock()

    event_dict = {
        "tenant_id": "t1", "device_id": "d1",
        "signal_field": "current_avg",
        "occurred_at": datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_event
        inserted = await persist_anomaly_event(db, event_dict)

    assert inserted is False
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_persist_anomaly_event_inserts_when_no_occurred_at():
    db = AsyncMock()
    db.add = Mock()
    db.flush = AsyncMock()

    event_dict = {
        "tenant_id": "t1", "device_id": "d1",
        "signal_field": "current_avg",
        "occurred_at": None,
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_event
        inserted = await persist_anomaly_event(db, event_dict)

    assert inserted is True


@pytest.mark.asyncio
async def test_update_anomaly_event_updates_fields():
    mock_row = _make_mock_row(ended_at=None, duration_seconds=None, anomaly_type=None, z_score=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(mock_row))
    db.flush = AsyncMock()

    event_dict = {
        "ended_at": datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        "duration_seconds": 7200,
        "z_score": 4.2,
        "anomaly_type": "persistent",
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import update_anomaly_event
        await update_anomaly_event(db, 1, event_dict)

    assert mock_row.ended_at == event_dict["ended_at"]
    assert mock_row.duration_seconds == 7200
    assert mock_row.anomaly_type == "persistent"


@pytest.mark.asyncio
async def test_update_anomaly_event_skips_when_not_found():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))

    with _patch_sqlalchemy():
        from app.services.anomaly.service import update_anomaly_event
        await update_anomaly_event(db, 999, {"ended_at": datetime(2026, 1, 1, tzinfo=timezone.utc)})


@pytest.mark.asyncio
async def test_persist_daily_count_upserts():
    existing_row = Mock()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(existing_row))
    db.flush = AsyncMock()

    count_dict = {
        "tenant_id": "t1", "device_id": "d1", "date": date(2026, 5, 21),
        "total_count": 5, "mild_count": 3,
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_daily_count
        await persist_daily_count(db, count_dict)

    assert existing_row.total_count == 5


@pytest.mark.asyncio
async def test_persist_daily_count_inserts_new():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    db.add = Mock()
    db.flush = AsyncMock()

    count_dict = {
        "tenant_id": "t1", "device_id": "d1", "date": date(2026, 5, 21),
        "total_count": 2, "mild_count": 1,
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_daily_count
        await persist_daily_count(db, count_dict)

    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_persist_weekly_count_upserts():
    existing_row = Mock()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(existing_row))
    db.flush = AsyncMock()

    count_dict = {
        "tenant_id": "t1", "device_id": "d1", "week_start_date": date(2026, 5, 19),
        "total_count": 10,
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_weekly_count
        await persist_weekly_count(db, count_dict)

    assert existing_row.total_count == 10


@pytest.mark.asyncio
async def test_persist_weekly_count_inserts_new():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    db.add = Mock()
    db.flush = AsyncMock()

    count_dict = {
        "tenant_id": "t1", "device_id": "d1", "week_start_date": date(2026, 5, 19),
        "total_count": 10,
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_weekly_count
        await persist_weekly_count(db, count_dict)

    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_delete_daily_count():
    m = Mock()
    m.rowcount = 1
    db = AsyncMock()
    db.execute = AsyncMock(return_value=m)
    db.flush = AsyncMock()

    with _patch_sqlalchemy():
        from app.services.anomaly.service import delete_daily_count
        await delete_daily_count(db, "t1", "d1", date(2026, 5, 21))


@pytest.mark.asyncio
async def test_no_daily_row_when_no_events():
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result([]),
        _scalar_one(None),
    ])
    db.flush = AsyncMock()

    with _patch_sqlalchemy():
        from app.services.anomaly.service import aggregate_daily_counts_for_device
        result = await aggregate_daily_counts_for_device(db, "t1", "d1", date(2026, 5, 21))

    assert result is None


@pytest.mark.asyncio
async def test_stale_daily_row_deleted_when_no_events():
    stale_row = Mock()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result([]),
        _scalar_one(stale_row),
    ])
    db.flush = AsyncMock()

    with _patch_sqlalchemy() as mock_query, \
         patch(f"{_SVC_MOD}.delete_daily_count", new_callable=AsyncMock) as mock_del:
        from app.services.anomaly.service import aggregate_daily_counts_for_device
        result = await aggregate_daily_counts_for_device(db, "t1", "d1", date(2026, 5, 21))

    assert result is None
    mock_del.assert_called_once_with(db, "t1", "d1", date(2026, 5, 21))


@pytest.mark.asyncio
async def test_no_weekly_row_when_no_daily_rows():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([]))
    db.flush = AsyncMock()

    with _patch_sqlalchemy():
        from app.services.anomaly.service import aggregate_weekly_counts_for_device
        result = await aggregate_weekly_counts_for_device(db, "t1", "d1", date(2026, 5, 19))

    assert result is None


@pytest.mark.asyncio
async def test_detect_device_anomalies_no_baselines():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([]))
    db.flush = AsyncMock()

    with _patch_sqlalchemy():
        from app.services.anomaly.service import detect_device_anomalies
        result = await detect_device_anomalies(db, "t1", "d1")

    assert result["new_events"] == 0
    assert result["extended_events"] == 0
    assert result["closed_events"] == 0


@pytest.mark.asyncio
async def test_detect_device_anomalies_no_feature_windows():
    bl_row = _make_mock_row(
        field_name="current_avg", time_window="5min", baseline_mean=10.0,
        baseline_std=1.0, baseline_median=10.0, baseline_mad=1.0,
        baseline_p05=8.0, baseline_p95=12.0, reading_count=20,
        quality_score=0.9, learned_from_ts=None, learned_to_ts=None,
        status="active", baseline_version=1,
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result([bl_row]),
    ])
    db.flush = AsyncMock()

    with _patch_sqlalchemy(), \
         patch(f"{_SVC_MOD}.load_feature_windows_for_anomaly", new_callable=AsyncMock, return_value=[]):
        from app.services.anomaly.service import detect_device_anomalies
        result = await detect_device_anomalies(db, "t1", "d1")

    assert result["new_events"] == 0


@pytest.mark.asyncio
async def test_detect_device_anomalies_with_anomaly():
    from app.services.degradation.types import FeatureWindowInput, FeatureWindowResult

    bl_row = _make_mock_row(
        field_name="current_avg", time_window="5min", baseline_mean=10.0,
        baseline_std=1.0, baseline_median=10.0, baseline_mad=1.0,
        baseline_p05=8.0, baseline_p95=12.0, reading_count=20,
        quality_score=0.9, learned_from_ts=None, learned_to_ts=None,
        status="active", baseline_version=1,
    )

    fw = FeatureWindowResult(
        window=FeatureWindowInput(current_avg_mean=20.0),
        running_state="STEADY_RUNNING",
        telemetry_coverage=0.9,
        sample_count=10,
        window_start=datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 21, 1, 0, tzinfo=timezone.utc),
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result([bl_row]),
        _scalar_result([]),
        _scalar_one(None),
        _scalar_result([]),
    ])
    db.add = Mock()
    db.flush = AsyncMock()

    with _patch_sqlalchemy(), \
         patch(f"{_SVC_MOD}.load_feature_windows_for_anomaly", new_callable=AsyncMock, return_value=[fw]):
        from app.services.anomaly.service import detect_device_anomalies
        result = await detect_device_anomalies(db, "t1", "d1")

    assert result["new_events"] >= 1


@pytest.mark.asyncio
async def test_cleanup_old_anomaly_rows_deletes():
    r1 = Mock()
    r1.rowcount = 5
    r2 = Mock()
    r2.rowcount = 3
    r3 = Mock()
    r3.rowcount = 1
    r4 = Mock()
    r4.rowcount = 2
    r5 = Mock()
    r5.rowcount = 0

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[r1, r2, r3, r4, r5])
    db.flush = AsyncMock()

    with _patch_sqlalchemy():
        from app.services.anomaly.service import cleanup_old_anomaly_rows
        summary = await cleanup_old_anomaly_rows(db, retention_days=90)

    assert summary["deleted"] == 11


@pytest.mark.asyncio
async def test_refresh_anomaly_baselines_no_windows():
    db = AsyncMock()

    with _patch_sqlalchemy(), \
         patch(f"{_SVC_MOD}.load_feature_windows_for_anomaly", new_callable=AsyncMock, return_value=[]):
        from app.services.anomaly.service import refresh_anomaly_baselines_for_device
        result = await refresh_anomaly_baselines_for_device(db, "t1", "d1")

    assert result == 0


@pytest.mark.asyncio
async def test_refresh_anomaly_baselines_with_windows():
    from app.services.degradation.types import FeatureWindowInput, FeatureWindowResult

    windows = [
        FeatureWindowResult(
            window=FeatureWindowInput(current_avg_mean=10.0 + i, power_mean=100.0),
            running_state="STEADY_RUNNING",
            telemetry_coverage=0.9,
            sample_count=10,
            window_start=datetime(2026, 1, 1, i, tzinfo=timezone.utc),
            window_end=datetime(2026, 1, 1, i + 1, tzinfo=timezone.utc),
        )
        for i in range(10)
    ]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    db.add = Mock()
    db.flush = AsyncMock()

    with _patch_sqlalchemy(), \
         patch(f"{_SVC_MOD}.load_feature_windows_for_anomaly", new_callable=AsyncMock, return_value=windows), \
         patch(f"{_SVC_MOD}.persist_anomaly_baselines", new_callable=AsyncMock, return_value=3):
        from app.services.anomaly.service import refresh_anomaly_baselines_for_device
        result = await refresh_anomaly_baselines_for_device(db, "t1", "d1")

    assert result == 3


@pytest.mark.asyncio
async def test_persist_anomaly_baselines_anti_churn():
    existing_row = _make_mock_row(
        field_name="current_avg", quality_score=0.88, baseline_version=1, status="active",
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([existing_row]))
    db.add = Mock()
    db.flush = AsyncMock()

    new_bl = _make_baseline("current_avg", quality_score=0.90)
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_baselines
        result = await persist_anomaly_baselines(db, "t1", "d1", [new_bl])

    assert result == 0


@pytest.mark.asyncio
async def test_persist_anomaly_baselines_promotes_when_significantly_better():
    existing_row = _make_mock_row(
        field_name="current_avg", quality_score=0.5, baseline_version=1, status="active",
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([existing_row]))
    db.add = Mock()
    db.flush = AsyncMock()

    new_bl = _make_baseline("current_avg", quality_score=0.9)
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_baselines
        result = await persist_anomaly_baselines(db, "t1", "d1", [new_bl])

    assert result == 1
    assert existing_row.status == "retired"


@pytest.mark.asyncio
async def test_persist_anomaly_baselines_inserts_when_no_existing():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([]))
    db.add = Mock()
    db.flush = AsyncMock()

    new_bl = _make_baseline("current_avg", quality_score=0.9)
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_baselines
        result = await persist_anomaly_baselines(db, "t1", "d1", [new_bl])

    assert result == 1


@pytest.mark.asyncio
async def test_persist_anomaly_baselines_retains_only_active_baseline():
    existing_high = _make_mock_row(
        field_name="current_avg", quality_score=0.95, baseline_version=2, status="active",
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([existing_high]))
    db.add = Mock()
    db.flush = AsyncMock()

    new_bl = _make_baseline("current_avg", quality_score=0.94)
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_baselines
        result = await persist_anomaly_baselines(db, "t1", "d1", [new_bl])

    assert result == 0
    assert existing_high.status == "active"


@pytest.mark.asyncio
async def test_persist_anomaly_baselines_updates_existing_candidate_in_place():
    existing_candidate = _make_mock_row(
        field_name="current_avg",
        quality_score=0.04,
        reading_count=1,
        baseline_version=1,
        status="candidate",
        baseline_mean=10.0,
        baseline_std=1.0,
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([existing_candidate]))
    db.add = Mock()
    db.flush = AsyncMock()

    new_bl = _make_baseline("current_avg", mean=12.0, std=1.5, quality_score=0.16, status="candidate")
    new_bl = AnomalyFieldBaseline(
        **{**new_bl.__dict__, "reading_count": 4}
    )
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_baselines
        result = await persist_anomaly_baselines(db, "t1", "d1", [new_bl])

    assert result == 1
    db.add.assert_not_called()
    assert existing_candidate.quality_score == 0.16
    assert existing_candidate.reading_count == 4
    assert existing_candidate.status == "candidate"


@pytest.mark.asyncio
async def test_persist_anomaly_baselines_promotes_candidate_row_to_active_in_place():
    existing_candidate = _make_mock_row(
        field_name="current_avg",
        quality_score=0.16,
        reading_count=4,
        baseline_version=1,
        status="candidate",
        baseline_mean=10.0,
        baseline_std=1.0,
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([existing_candidate]))
    db.add = Mock()
    db.flush = AsyncMock()

    new_bl = AnomalyFieldBaseline(
        field_name="current_avg",
        baseline_mean=11.0,
        baseline_std=1.1,
        reading_count=5,
        quality_score=0.32,
        status="active",
        baseline_version=1,
    )
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_anomaly_baselines
        result = await persist_anomaly_baselines(db, "t1", "d1", [new_bl])

    assert result == 1
    db.add.assert_not_called()
    assert existing_candidate.status == "active"
    assert existing_candidate.reading_count == 5
    assert existing_candidate.quality_score == 0.32


@pytest.mark.asyncio
async def test_load_prior_week_total_returns_int():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(15))

    with _patch_sqlalchemy():
        from app.services.anomaly.service import load_prior_week_total_for_device
        result = await load_prior_week_total_for_device(db, "t1", "d1", date(2026, 5, 12))

    assert result == 15


@pytest.mark.asyncio
async def test_load_prior_week_total_returns_none():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))

    with _patch_sqlalchemy():
        from app.services.anomaly.service import load_prior_week_total_for_device
        result = await load_prior_week_total_for_device(db, "t1", "d1", date(2026, 5, 12))

    assert result is None


@pytest.mark.asyncio
async def test_aggregate_daily_counts_with_events():
    event_row = _make_mock_row(
        signal_field="current_avg", severity="mild",
        confidence=0.8, supply_related=False, startup_adjacent=False,
        mode_change=False, recurring=False, correlated_signals_json=None,
        occurred_at=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result([event_row]),
    ])
    db.flush = AsyncMock()

    with _patch_sqlalchemy(), \
         patch(f"{_SVC_MOD}.persist_daily_count", new_callable=AsyncMock) as mock_persist:
        from app.services.anomaly.service import aggregate_daily_counts_for_device
        result = await aggregate_daily_counts_for_device(db, "t1", "d1", date(2026, 5, 21))

    assert result is not None
    assert result.total_count >= 1
    mock_persist.assert_called_once()


@pytest.mark.asyncio
async def test_aggregate_weekly_counts_with_dailies():
    daily_row = _make_mock_row(
        date=date(2026, 5, 19), total_count=5, mild_count=3,
        strong_count=1, severe_count=1, supply_related_count=0,
        top_signal="current_avg", avg_confidence=0.8,
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result([daily_row]),
        _scalar_one(3),
    ])
    db.flush = AsyncMock()

    with _patch_sqlalchemy(), \
         patch(f"{_SVC_MOD}.persist_weekly_count", new_callable=AsyncMock) as mock_persist:
        from app.services.anomaly.service import aggregate_weekly_counts_for_device
        result = await aggregate_weekly_counts_for_device(db, "t1", "d1", date(2026, 5, 19))

    assert result is not None
    mock_persist.assert_called_once()


@pytest.mark.asyncio
async def test_detect_device_anomalies_closes_stale_events():
    bl_row = _make_mock_row(
        field_name="current_avg", time_window="5min", baseline_mean=10.0,
        baseline_std=1.0, baseline_median=10.0, baseline_mad=1.0,
        baseline_p05=8.0, baseline_p95=12.0, reading_count=20,
        quality_score=0.9, learned_from_ts=None, learned_to_ts=None,
        status="active", baseline_version=1,
    )

    stale_event_row = _make_mock_row(
        id=42,
        occurred_at=datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=None,
        duration_seconds=None,
    )

    from app.services.degradation.types import FeatureWindowInput, FeatureWindowResult

    fw = FeatureWindowResult(
        window=FeatureWindowInput(current_avg_mean=10.0),
        running_state="STEADY_RUNNING",
        telemetry_coverage=0.9,
        sample_count=10,
        window_start=datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 21, 1, 0, tzinfo=timezone.utc),
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result([bl_row]),
        _scalar_result([]),
        _scalar_result([stale_event_row]),
    ])
    db.flush = AsyncMock()

    with _patch_sqlalchemy(), \
         patch(f"{_SVC_MOD}.load_feature_windows_for_anomaly", new_callable=AsyncMock, return_value=[fw]):
        from app.services.anomaly.service import detect_device_anomalies
        result = await detect_device_anomalies(db, "t1", "d1")

    assert result["closed_events"] == 1
    assert stale_event_row.ended_at is not None


@pytest.mark.asyncio
async def test_persist_daily_count_advances_updated_at():
    existing_row = _make_mock_row(
        created_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(existing_row))
    db.flush = AsyncMock()

    count_dict = {
        "tenant_id": "t1", "device_id": "d1", "date": date(2026, 5, 21),
        "total_count": 5, "mild_count": 3,
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_daily_count
        await persist_daily_count(db, count_dict)

    assert existing_row.total_count == 5
    assert existing_row.updated_at is not None
    assert existing_row.updated_at > datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert existing_row.created_at == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_persist_weekly_count_advances_updated_at():
    existing_row = _make_mock_row(
        created_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(existing_row))
    db.flush = AsyncMock()

    count_dict = {
        "tenant_id": "t1", "device_id": "d1", "week_start_date": date(2026, 5, 19),
        "total_count": 10,
    }
    with _patch_sqlalchemy():
        from app.services.anomaly.service import persist_weekly_count
        await persist_weekly_count(db, count_dict)

    assert existing_row.total_count == 10
    assert existing_row.updated_at is not None
    assert existing_row.updated_at > datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert existing_row.created_at == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
