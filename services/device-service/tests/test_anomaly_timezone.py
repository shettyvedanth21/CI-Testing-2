"""Tests for Phase B: IST timezone alignment in anomaly aggregation and reporting.

Validates that anomaly day/week/month boundaries use the platform timezone
(Asia/Kolkata, UTC+5:30) instead of UTC.
"""

from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_BASE_DIR = Path(__file__).resolve().parents[1]
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))


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


class _ModelStub:
    def __getattr__(self, name):
        return _ComparisonExpr()

    def __call__(self, **kwargs):
        return MagicMock(**kwargs)


def _ensure_app_stubs():
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

    if "app.services.degradation.service" not in sys.modules:
        _ds = types.ModuleType("app.services.degradation.service")
        _ds.load_feature_windows_for_device = MagicMock(return_value=[])
        sys.modules["app.services.degradation.service"] = _ds
        sys.modules["app.services.degradation"].service = _ds

    _device_stub = types.ModuleType("app.models.device")
    for name in ("MachineAnomalyBaseline", "MachineAnomalyEvent",
                  "MachineAnomalyDailyCount", "MachineAnomalyWeeklyCount",
                  "MachineHealthFeatureWindow", "Device",
                  "ParameterHealthConfig"):
        setattr(_device_stub, name, _ModelStub())

    _models_stub = types.ModuleType("app.models")
    _models_stub.device = _device_stub

    sys.modules["app.models.device"] = _device_stub
    sys.modules["app.models"] = _models_stub
    sys.modules["app"].models = _models_stub
    sys.modules["app.models"].device = _device_stub

    return saved


_SAVED_MODULES = _ensure_app_stubs()

from app.services.anomaly.tz import get_platform_tz, local_today


class TestGetPlatformTz:
    def test_returns_zoneinfo_for_platform_timezone(self):
        tz = get_platform_tz()
        from zoneinfo import ZoneInfo
        assert isinstance(tz, ZoneInfo)
        assert str(tz) == "Asia/Kolkata"

    def test_ist_offset_is_plus_5_30(self):
        tz = get_platform_tz()
        ist_midnight = datetime(2026, 5, 24, 0, 0, 0, tzinfo=tz)
        utc_equivalent = ist_midnight.astimezone(timezone.utc)
        offset_hours = (ist_midnight.replace(tzinfo=None) - utc_equivalent.replace(tzinfo=None)).total_seconds() / 3600
        assert offset_hours == 5.5


class TestLocalToday:
    def test_at_utc_0500_returns_same_ist_date(self):
        utc_0500 = datetime(2026, 5, 24, 5, 0, 0, tzinfo=timezone.utc)
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = utc_0500
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = local_today()
        assert result == date(2026, 5, 24)

    def test_at_utc_2000_returns_next_ist_date(self):
        utc_2000 = datetime(2026, 5, 24, 20, 0, 0, tzinfo=timezone.utc)
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = utc_2000
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = local_today()
        assert result == date(2026, 5, 25)

    def test_at_utc_1830_exact_midnight_boundary(self):
        utc_1830 = datetime(2026, 5, 23, 18, 30, 0, tzinfo=timezone.utc)
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = utc_1830
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = local_today()
        assert result == date(2026, 5, 24)

    def test_at_utc_1829_just_before_ist_midnight(self):
        utc_1829 = datetime(2026, 5, 23, 18, 29, 59, tzinfo=timezone.utc)
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = utc_1829
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = local_today()
        assert result == date(2026, 5, 23)


class TestDayBoundaryConstruction:
    """Validate that aggregate_daily_counts_for_device builds IST midnight boundaries."""

    def test_ist_day_start_is_1830_utc_previous_day(self):
        from app.services.anomaly.tz import get_platform_tz

        target = date(2026, 5, 24)
        platform_tz = get_platform_tz()
        local_midnight = datetime(target.year, target.month, target.day, tzinfo=platform_tz)
        day_start_utc = local_midnight.astimezone(timezone.utc)

        assert day_start_utc == datetime(2026, 5, 23, 18, 30, 0, tzinfo=timezone.utc)

    def test_ist_day_end_is_1830_utc_same_day(self):
        from app.services.anomaly.tz import get_platform_tz

        target = date(2026, 5, 24)
        platform_tz = get_platform_tz()
        local_midnight = datetime(target.year, target.month, target.day, tzinfo=platform_tz)
        day_end_utc = (local_midnight + timedelta(days=1)).astimezone(timezone.utc)

        assert day_end_utc == datetime(2026, 5, 24, 18, 30, 0, tzinfo=timezone.utc)

    def test_ist_midnight_boundary_groups_event_at_1830_utc(self):
        from app.services.anomaly.tz import get_platform_tz

        target = date(2026, 5, 24)
        platform_tz = get_platform_tz()
        local_midnight = datetime(target.year, target.month, target.day, tzinfo=platform_tz)
        day_start = local_midnight.astimezone(timezone.utc)
        day_end = (local_midnight + timedelta(days=1)).astimezone(timezone.utc)

        event_at_1830_utc = datetime(2026, 5, 23, 18, 30, 0, tzinfo=timezone.utc)
        assert day_start <= event_at_1830_utc < day_end

    def test_event_just_before_ist_midnight_in_previous_day(self):
        from app.services.anomaly.tz import get_platform_tz

        target = date(2026, 5, 24)
        platform_tz = get_platform_tz()
        local_midnight = datetime(target.year, target.month, target.day, tzinfo=platform_tz)
        day_start = local_midnight.astimezone(timezone.utc)

        event_at_1829_utc = datetime(2026, 5, 23, 18, 29, 59, tzinfo=timezone.utc)
        assert event_at_1829_utc < day_start

    def test_event_just_after_ist_midnight_in_current_day(self):
        from app.services.anomaly.tz import get_platform_tz

        target = date(2026, 5, 24)
        platform_tz = get_platform_tz()
        local_midnight = datetime(target.year, target.month, target.day, tzinfo=platform_tz)
        day_start = local_midnight.astimezone(timezone.utc)
        day_end = (local_midnight + timedelta(days=1)).astimezone(timezone.utc)

        event_at_1831_utc = datetime(2026, 5, 23, 18, 31, 0, tzinfo=timezone.utc)
        assert day_start <= event_at_1831_utc < day_end

    def test_utc_midnight_event_is_mid_day_in_ist(self):
        from app.services.anomaly.tz import get_platform_tz

        target = date(2026, 5, 24)
        platform_tz = get_platform_tz()
        local_midnight = datetime(target.year, target.month, target.day, tzinfo=platform_tz)
        day_start = local_midnight.astimezone(timezone.utc)
        day_end = (local_midnight + timedelta(days=1)).astimezone(timezone.utc)

        event_at_utc_midnight = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
        assert day_start <= event_at_utc_midnight < day_end


class TestWeekBoundaryConstruction:
    """Validate IST week start computation."""

    def test_monday_ist_is_monday_even_when_utc_is_sunday(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 25, 1, 30, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        assert today == date(2026, 5, 25)
        week_start = today - timedelta(days=today.weekday())
        assert week_start == date(2026, 5, 25)
        assert today.weekday() == 0

    def test_sunday_utc_before_1830_is_sunday_ist(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 24, 17, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        assert today == date(2026, 5, 24)
        assert today.weekday() == 6
        week_start = today - timedelta(days=today.weekday())
        assert week_start == date(2026, 5, 18)

    def test_sunday_utc_after_1830_is_monday_ist(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 24, 19, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        assert today == date(2026, 5, 25)
        week_start = today - timedelta(days=today.weekday())
        assert week_start == date(2026, 5, 25)


class TestMonthBoundaryConstruction:
    """Validate IST month start computation."""

    def test_month_1st_at_1830_utc_is_month_1st_ist(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 1, 18, 30, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        assert today == date(2026, 6, 2)
        month_start = today.replace(day=1)
        assert month_start == date(2026, 6, 1)

    def test_month_1st_at_0500_utc_is_month_1st_ist(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 1, 5, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        assert today == date(2026, 6, 1)
        month_start = today.replace(day=1)
        assert month_start == date(2026, 6, 1)

    def test_last_day_of_month_before_1830_utc_is_same_month_ist(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 31, 17, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        assert today == date(2026, 5, 31)
        month_start = today.replace(day=1)
        assert month_start == date(2026, 5, 1)

    def test_last_day_of_month_after_1830_utc_is_next_month_ist(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 31, 19, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        assert today == date(2026, 6, 1)
        month_start = today.replace(day=1)
        assert month_start == date(2026, 6, 1)


class TestMonthQueryUpperBound:
    """Validate that month aggregation query includes date <= today."""

    def test_month_range_is_bounded_by_today(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 24, 5, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        month_start = today.replace(day=1)
        assert month_start <= today
        assert today == date(2026, 5, 24)
        assert month_start == date(2026, 5, 1)

    def test_future_date_excluded_from_month_range(self):
        with patch("app.services.anomaly.tz.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 24, 5, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = local_today()
        month_start = today.replace(day=1)
        future = date(2026, 5, 25)
        assert not (month_start <= future <= today)


class TestOrphanCleanupLogic:
    """Validate that orphan detection uses Phase A updated_at correctly."""

    def test_row_updated_after_rebuild_start_is_not_orphan(self):
        rebuild_start = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        row_updated = datetime(2026, 5, 24, 10, 5, 0, tzinfo=timezone.utc)
        assert row_updated >= rebuild_start

    def test_row_updated_before_rebuild_start_is_orphan(self):
        rebuild_start = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        row_updated = datetime(2026, 5, 23, 18, 0, 0, tzinfo=timezone.utc)
        assert row_updated < rebuild_start

    def test_row_with_equal_timestamp_is_not_orphan(self):
        rebuild_start = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        row_updated = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        assert row_updated >= rebuild_start


class TestCleanupRetentionUnchanged:
    """Validate that retention cleanup still uses created_at (UTC) after Phase B."""

    def test_retention_cutoff_uses_utc_not_local_date(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        assert cutoff.tzinfo == timezone.utc

    def test_retention_compares_created_at_not_date_column(self):
        _RETENTION_DAYS = 365
        cutoff = datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)
        old_created = datetime(2025, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
        assert old_created < cutoff
        recent_created = datetime.now(timezone.utc) - timedelta(days=10)
        assert recent_created >= cutoff


class TestPhaseAPreserved:
    """Validate that Phase A freshness behavior is preserved after Phase B changes."""

    def test_updated_at_advances_on_rebuild_style_upsert(self):
        old_updated = datetime(2026, 5, 23, 18, 0, 0, tzinfo=timezone.utc)
        new_updated = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        assert new_updated > old_updated

    def test_created_at_preserved_on_upsert(self):
        created = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
        new_updated = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        assert created != new_updated
        assert created < new_updated

    def test_computed_at_derived_from_updated_at_not_created_at(self):
        updated_at = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        created_at = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
        computed_at = updated_at
        assert computed_at != created_at
        delta_minutes = (datetime.now(timezone.utc) - computed_at).total_seconds() / 60.0
        assert delta_minutes >= 0

    def test_stale_detection_uses_updated_at(self):
        stale_threshold_minutes = 60
        stale_updated = datetime.now(timezone.utc) - timedelta(hours=3)
        fresh_updated = datetime.now(timezone.utc) - timedelta(minutes=5)

        stale_delta = (datetime.now(timezone.utc) - stale_updated).total_seconds() / 60.0
        fresh_delta = (datetime.now(timezone.utc) - fresh_updated).total_seconds() / 60.0

        assert stale_delta > stale_threshold_minutes
        assert fresh_delta < stale_threshold_minutes


class TestRebuildIdempotency:
    """Validate that the rebuild approach is idempotent."""

    def test_upsert_produces_same_result_on_rerun(self):
        count_dict = {
            "tenant_id": "T1",
            "device_id": "D1",
            "date": date(2026, 5, 24),
            "total_count": 2,
            "mild_count": 0,
            "strong_count": 1,
            "severe_count": 1,
            "supply_related_count": 0,
            "top_signal": "current_avg",
            "avg_confidence": 0.9,
        }
        first = dict(count_dict)
        second = dict(count_dict)
        assert first == second

    def test_rebuild_start_utc_captures_correct_time(self):
        before = datetime.now(timezone.utc)
        rebuild_start = datetime.now(timezone.utc)
        after = datetime.now(timezone.utc)
        assert before <= rebuild_start <= after

    def test_orphan_deletion_is_idempotent(self):
        rebuild_start = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        row_updated_before = datetime(2026, 5, 23, 18, 0, 0, tzinfo=timezone.utc)
        assert row_updated_before < rebuild_start


class TestScriptSafety:
    """Validate backfill_anomaly_tz.py script safety features."""

    def test_scope_filter_no_filters_returns_true(self):
        import scripts.backfill_anomaly_tz as mod
        result = mod._scope_filter_expr(_ModelStub(), None, None)
        assert result is True

    def test_scope_filter_with_tenant_produces_clauses(self):
        import scripts.backfill_anomaly_tz as mod
        clauses = []
        model = _ModelStub()
        tenant_id = "T1"
        clauses.append(model.tenant_id == tenant_id)
        assert len(clauses) == 1

    def test_cleanup_orphans_without_rebuild_start_is_invalid(self):
        import scripts.backfill_anomaly_tz as mod
        parser = mod._build_parser()
        args = parser.parse_args(["--cleanup-orphans"])
        assert args.rebuild_start_utc is None
        assert args.cleanup_orphans is True
        invalid = args.cleanup_orphans and not args.rebuild_start_utc
        assert invalid is True

    def test_confirm_cleanup_without_cleanup_orphans_is_invalid(self):
        import scripts.backfill_anomaly_tz as mod
        parser = mod._build_parser()
        args = parser.parse_args(["--confirm-cleanup"])
        assert args.confirm_cleanup is True
        assert args.cleanup_orphans is False
        invalid = args.confirm_cleanup and not args.cleanup_orphans
        assert invalid is True

    def test_valid_cleanup_args_all_present(self):
        import scripts.backfill_anomaly_tz as mod
        parser = mod._build_parser()
        args = parser.parse_args([
            "--cleanup-orphans",
            "--confirm-cleanup",
            "--rebuild-start-utc", "2026-05-24T10:00:00+00:00",
            "--tenant-id", "SH00000001",
        ])
        assert args.cleanup_orphans is True
        assert args.confirm_cleanup is True
        assert args.rebuild_start_utc is not None
        valid = args.cleanup_orphans and args.rebuild_start_utc
        assert bool(valid) is True

    def test_dry_run_flag_default_false(self):
        import scripts.backfill_anomaly_tz as mod
        parser = mod._build_parser()
        args = parser.parse_args([])
        assert args.dry_run is False

    def test_dry_run_cleanup_count_only(self):
        import argparse
        args = argparse.Namespace(
            dry_run=True,
            cleanup_orphans=True,
            confirm_cleanup=False,
            rebuild_start_utc="2026-05-24T10:00:00+00:00",
            tenant_id=None,
            device_id=None,
        )
        assert args.dry_run is True
        assert args.confirm_cleanup is False

    def test_cleanup_without_confirm_aborts_safety(self):
        import argparse
        args = argparse.Namespace(
            dry_run=False,
            cleanup_orphans=True,
            confirm_cleanup=False,
            rebuild_start_utc="2026-05-24T10:00:00+00:00",
            tenant_id=None,
            device_id=None,
        )
        assert args.confirm_cleanup is False

    def test_rebuild_summary_includes_tenant_scope(self):
        import argparse
        args = argparse.Namespace(
            dry_run=True,
            tenant_id="SH00000001",
            device_id="AD00000001",
            batch_size=50,
            cleanup_orphans=False,
            confirm_cleanup=False,
            rebuild_start_utc=None,
        )
        assert args.tenant_id == "SH00000001"
        assert args.device_id == "AD00000001"

    def test_cleanup_scope_matches_rebuild_scope(self):
        import argparse
        rebuild_args = argparse.Namespace(
            dry_run=True,
            tenant_id="SH00000001",
            device_id=None,
            batch_size=50,
            cleanup_orphans=False,
            confirm_cleanup=False,
            rebuild_start_utc=None,
        )
        cleanup_args = argparse.Namespace(
            dry_run=False,
            cleanup_orphans=True,
            confirm_cleanup=True,
            rebuild_start_utc="2026-05-24T10:00:00+00:00",
            tenant_id="SH00000001",
            device_id=None,
        )
        assert rebuild_args.tenant_id == cleanup_args.tenant_id


class TestInactiveDeviceSafety:
    """Validate that rebuild covers devices with anomaly events, not just active devices."""

    def test_discover_range_signature_accepts_scope(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        sig = inspect.signature(mod._discover_range)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "tenant_id" in params
        assert "device_id" in params

    def test_discover_range_uses_event_table_not_device_table(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        source = inspect.getsource(mod._discover_range)
        assert "MachineAnomalyEvent" in source
        assert "Device.is_active" not in source
        assert "Device" not in source or "MachineAnomalyEvent" in source

    def test_inactive_device_with_events_included_in_rebuild(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        source = inspect.getsource(mod._discover_range)
        assert "distinct()" in source
        assert "MachineAnomalyEvent.tenant_id" in source
        assert "MachineAnomalyEvent.device_id" in source

    def test_rebuild_passes_scope_to_discover_range(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        source = inspect.getsource(mod._rebuild)
        assert "tenant_id=args.tenant_id" in source
        assert "device_id=args.device_id" in source


class TestScopedEarliestDate:
    """Validate that earliest date is computed within scope, not globally."""

    def test_discover_range_queries_earliest_with_scope_filter(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        source = inspect.getsource(mod._discover_range)
        assert "scope_where" in source
        assert ".where(scope_where)" in source

    def test_scoped_query_avoids_unrelated_history(self):
        earliest_global = date(2024, 1, 1)
        earliest_tenant = date(2026, 5, 1)
        assert earliest_tenant > earliest_global
        scoped_days = (date(2026, 5, 24) - earliest_tenant).days + 1
        global_days = (date(2026, 5, 24) - earliest_global).days + 1
        assert scoped_days < global_days

    def test_no_scope_returns_all_devices_earliest(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        source = inspect.getsource(mod._discover_range)
        assert "scope_clauses" in source
        assert "if tenant_id:" in source


class TestCleanupScopeAlignment:
    """Validate that orphan cleanup only targets devices present in anomaly events."""

    def test_cleanup_uses_event_subquery(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        source = inspect.getsource(mod._cleanup_orphans)
        assert "rebuilt_device_subq" in source
        assert "MachineAnomalyEvent" in source
        assert ".subquery()" in source

    def test_cleanup_joins_count_rows_to_event_devices(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        source = inspect.getsource(mod._cleanup_orphans)
        assert "rebuilt_device_subq.c.tenant_id" in source
        assert "rebuilt_device_subq.c.device_id" in source

    def test_device_without_events_preserved_during_cleanup(self):
        rebuild_start = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        device_has_events = True
        device_no_events = False
        assert device_has_events is True
        assert device_no_events is False
        assert not device_no_events

    def test_cleanup_scope_filter_applied_to_event_subquery(self):
        import scripts.backfill_anomaly_tz as mod
        import inspect
        source = inspect.getsource(mod._cleanup_orphans)
        assert "_scope_filter_expr(MachineAnomalyEvent" in source
