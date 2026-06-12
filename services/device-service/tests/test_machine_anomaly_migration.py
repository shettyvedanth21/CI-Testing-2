from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260522_0001_machine_anomaly_activity.py"
)

EXPECTED_TABLES = [
    "machine_anomaly_baselines",
    "machine_anomaly_events",
    "machine_anomaly_daily_counts",
    "machine_anomaly_weekly_counts",
]

EXPECTED_HEAD_REVISION = "20260521_0001_machine_degradation_score"

MODEL_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "models"
    / "device.py"
)


def _inject_stubs():
    if "alembic" not in sys.modules:
        _alembic = types.ModuleType("alembic")
        _alembic.op = object()
        sys.modules["alembic"] = _alembic

    if "sqlalchemy" not in sys.modules:
        _sa = types.ModuleType("sqlalchemy")
        _sentinel = object()

        def _callable(*_a, **_kw):
            return _sentinel

        for _name in (
            "BigInteger", "Integer", "String", "DateTime", "Float", "Text",
            "Boolean", "Date",
            "Column", "ForeignKeyConstraint", "UniqueConstraint",
            "CheckConstraint", "PrimaryKeyConstraint", "text", "inspect",
        ):
            setattr(_sa, _name, _callable)

        sys.modules["sqlalchemy"] = _sa


def _load_migration_module():
    _inject_stubs()

    spec = importlib.util.spec_from_file_location("anomaly_activity_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeInspector:
    def __init__(self, existing_tables=None):
        self._tables = set(existing_tables or [])

    def get_table_names(self):
        return sorted(self._tables)


class _FakeOp:
    def __init__(self, existing_tables=None):
        self.created_tables: list[str] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.dropped_indexes: list[tuple[str, str]] = []
        self.dropped_tables: list[str] = []
        self._inspector = _FakeInspector(existing_tables)

    def get_bind(self):
        return object()

    def create_table(self, name: str, *args, **kwargs):
        self.created_tables.append(name)

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs):
        self.created_indexes.append((name, table_name, tuple(columns)))

    def drop_index(self, name: str, table_name: str, **kwargs):
        self.dropped_indexes.append((name, table_name))

    def drop_table(self, name: str, **kwargs):
        self.dropped_tables.append(name)


def test_anomaly_migration_creates_four_tables(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp()
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector())

    module.upgrade()

    assert fake_op.created_tables == EXPECTED_TABLES


def test_anomaly_migration_creates_expected_indexes(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp()
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector())

    module.upgrade()

    baseline_indexes = [
        (name, table, cols)
        for name, table, cols in fake_op.created_indexes
        if table == "machine_anomaly_baselines"
    ]
    assert (
        "ix_mab_tenant_device_status",
        "machine_anomaly_baselines",
        ("tenant_id", "device_id", "status"),
    ) in baseline_indexes
    assert (
        "ix_mab_tenant_device_field",
        "machine_anomaly_baselines",
        ("tenant_id", "device_id", "field_name"),
    ) in baseline_indexes

    event_indexes = [
        (name, table, cols)
        for name, table, cols in fake_op.created_indexes
        if table == "machine_anomaly_events"
    ]
    assert (
        "ix_mae_tenant_device_occurred",
        "machine_anomaly_events",
        ("tenant_id", "device_id", "occurred_at"),
    ) in event_indexes
    assert (
        "ix_mae_tenant_occurred",
        "machine_anomaly_events",
        ("tenant_id", "occurred_at"),
    ) in event_indexes
    assert (
        "ix_mae_tenant_device_severity",
        "machine_anomaly_events",
        ("tenant_id", "device_id", "severity"),
    ) in event_indexes
    assert (
        "ix_mae_device_occurred",
        "machine_anomaly_events",
        ("device_id", "occurred_at"),
    ) in event_indexes

    daily_indexes = [
        (name, table, cols)
        for name, table, cols in fake_op.created_indexes
        if table == "machine_anomaly_daily_counts"
    ]
    assert (
        "ix_madc_tenant_device_date",
        "machine_anomaly_daily_counts",
        ("tenant_id", "device_id", "date"),
    ) in daily_indexes
    assert (
        "ix_madc_tenant_date",
        "machine_anomaly_daily_counts",
        ("tenant_id", "date"),
    ) in daily_indexes

    weekly_indexes = [
        (name, table, cols)
        for name, table, cols in fake_op.created_indexes
        if table == "machine_anomaly_weekly_counts"
    ]
    assert (
        "ix_mawc_tenant_device_week",
        "machine_anomaly_weekly_counts",
        ("tenant_id", "device_id", "week_start_date"),
    ) in weekly_indexes
    assert (
        "ix_mawc_tenant_week",
        "machine_anomaly_weekly_counts",
        ("tenant_id", "week_start_date"),
    ) in weekly_indexes


def test_anomaly_migration_downgrade_drops_all_tables_and_indexes(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp(existing_tables=EXPECTED_TABLES)
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector(EXPECTED_TABLES))

    module.downgrade()

    assert fake_op.dropped_tables == list(reversed(EXPECTED_TABLES))
    assert len(fake_op.dropped_indexes) > 0


def test_anomaly_migration_down_revision_points_to_correct_head():
    module = _load_migration_module()
    assert module.down_revision == EXPECTED_HEAD_REVISION


def test_anomaly_migration_revision_id_is_set():
    module = _load_migration_module()
    assert module.revision == "20260522_0001_machine_anomaly_activity"


def test_anomaly_migration_is_idempotent(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp(existing_tables=EXPECTED_TABLES)
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector(EXPECTED_TABLES))

    module.upgrade()

    assert fake_op.created_tables == []
    assert fake_op.created_indexes == []


def test_anomaly_migration_downgrade_is_safe_when_tables_absent(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp(existing_tables=[])
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector([]))

    module.downgrade()

    assert fake_op.dropped_tables == []
    assert fake_op.dropped_indexes == []


def test_anomaly_migration_severity_check_constraint_values():
    migration_source = MIGRATION_PATH.read_text()

    check_match = re.search(
        r"severity IN \(([^)]+)\)",
        migration_source,
    )
    assert check_match is not None, "Could not find severity CHECK constraint in migration"
    allowed_values = {s.strip().strip("'\"") for s in check_match.group(1).split(",")}
    assert allowed_values == {"mild", "strong", "severe"}


def test_anomaly_migration_anomaly_type_check_constraint_values():
    migration_source = MIGRATION_PATH.read_text()

    check_match = re.search(
        r"anomaly_type IN \(([^)]+)\)",
        migration_source,
    )
    assert check_match is not None, "Could not find anomaly_type CHECK constraint in migration"
    allowed_values = {s.strip().strip("'\"") for s in check_match.group(1).split(",")}
    assert allowed_values == {"deviation", "persistent", "trend"}


def test_anomaly_migration_status_check_constraint_in_baselines():
    migration_source = MIGRATION_PATH.read_text()

    check_match = re.search(
        r"status IN \(([^)]+)\).*?ck_mab_status",
        migration_source,
        re.DOTALL,
    )
    assert check_match is not None, "Could not find status CHECK constraint in baselines migration"
    allowed_values = {s.strip().strip("'\"") for s in check_match.group(1).split(",")}
    assert allowed_values == {"active", "candidate", "retired"}


def test_anomaly_model_defaults_match_migration_defaults():
    migration_source = MIGRATION_PATH.read_text()
    model_source = MODEL_PATH.read_text()

    for field, migration_fragment in [
        ("severity", "machine_anomaly_events"),
        ("anomaly_type", "machine_anomaly_events"),
        ("status", "machine_anomaly_baselines"),
        ("time_window", "machine_anomaly_baselines"),
    ]:
        model_match = re.search(
            rf'{field}.*?default="([^"]+)"',
            model_source,
        ) or re.search(
            rf"{field}.*?default='([^']+)'",
            model_source,
        )
        assert model_match is not None, f"Could not find {field} default in model source"

        migration_match = re.search(
            rf'server_default="([^"]+)"',
            migration_source.split(field)[1].split("\n")[0],
        ) or re.search(
            r"server_default='([^']+)'",
            migration_source.split(field)[1].split("\n")[0],
        )
        assert migration_match is not None, f"Could not find {field} server_default in migration"
        assert model_match.group(1) == migration_match.group(1), (
            f"Model default '{model_match.group(1)}' does not match migration server_default '{migration_match.group(1)}' for {field}"
        )


def test_anomaly_migration_boolean_defaults_match():
    migration_source = MIGRATION_PATH.read_text()
    model_source = MODEL_PATH.read_text()

    for field in ("supply_related", "startup_adjacent", "mode_change", "recurring"):
        model_match = re.search(
            rf"{field}.*?default=(False|True)",
            model_source,
        )
        assert model_match is not None, f"Could not find {field} default in model source"
        expected_migration_val = "0" if model_match.group(1) == "False" else "1"

        migration_match = re.search(
            rf'server_default="{expected_migration_val}"',
            migration_source.split(field)[1].split("\n")[0],
        ) or re.search(
            rf"server_default='{expected_migration_val}'",
            migration_source.split(field)[1].split("\n")[0],
        )
        assert migration_match is not None, (
            f"Could not find {field} server_default='{expected_migration_val}' in migration"
        )
