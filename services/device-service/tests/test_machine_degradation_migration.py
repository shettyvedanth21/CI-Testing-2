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
    / "20260521_0001_machine_degradation_score.py"
)

EXPECTED_TABLES = [
    "machine_health_feature_windows",
    "machine_health_baselines",
    "machine_health_latest",
    "machine_health_history",
]

EXPECTED_HEAD_REVISION = "20260506_0003_split_live_uptime_semantics"

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
            "Column", "ForeignKeyConstraint", "UniqueConstraint",
            "CheckConstraint", "PrimaryKeyConstraint", "text", "inspect",
        ):
            setattr(_sa, _name, _callable)

        sys.modules["sqlalchemy"] = _sa


def _load_migration_module():
    _inject_stubs()

    spec = importlib.util.spec_from_file_location("degradation_score_migration", MIGRATION_PATH)
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


def test_degradation_migration_creates_four_tables(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp()
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector())

    module.upgrade()

    assert fake_op.created_tables == EXPECTED_TABLES


def test_degradation_migration_creates_expected_indexes(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp()
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector())

    module.upgrade()

    feature_window_indexes = [
        (name, table, cols)
        for name, table, cols in fake_op.created_indexes
        if table == "machine_health_feature_windows"
    ]
    assert (
        "ix_mhfw_tenant_device_start",
        "machine_health_feature_windows",
        ("tenant_id", "device_id", "window_start"),
    ) in feature_window_indexes
    assert (
        "ix_mhfw_tenant_start",
        "machine_health_feature_windows",
        ("tenant_id", "window_start"),
    ) in feature_window_indexes
    assert (
        "ix_mhfw_device_start",
        "machine_health_feature_windows",
        ("device_id", "window_start"),
    ) in feature_window_indexes

    baseline_indexes = [
        (name, table, cols)
        for name, table, cols in fake_op.created_indexes
        if table == "machine_health_baselines"
    ]
    assert (
        "ix_mhb_tenant_device_status",
        "machine_health_baselines",
        ("tenant_id", "device_id", "status"),
    ) in baseline_indexes
    assert (
        "ix_mhb_tenant_status",
        "machine_health_baselines",
        ("tenant_id", "status"),
    ) in baseline_indexes

    latest_indexes = [
        (name, table, cols)
        for name, table, cols in fake_op.created_indexes
        if table == "machine_health_latest"
    ]
    assert (
        "ix_mhl_tenant_status",
        "machine_health_latest",
        ("tenant_id", "status"),
    ) in latest_indexes

    history_indexes = [
        (name, table, cols)
        for name, table, cols in fake_op.created_indexes
        if table == "machine_health_history"
    ]
    assert (
        "ix_mhh_tenant_device_time",
        "machine_health_history",
        ("tenant_id", "device_id", "computed_at"),
    ) in history_indexes


def test_degradation_migration_downgrade_drops_all_tables_and_indexes(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp(existing_tables=EXPECTED_TABLES)
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector(EXPECTED_TABLES))

    module.downgrade()

    assert fake_op.dropped_tables == list(reversed(EXPECTED_TABLES))
    assert len(fake_op.dropped_indexes) > 0


def test_degradation_migration_down_revision_points_to_correct_head():
    module = _load_migration_module()
    assert module.down_revision == EXPECTED_HEAD_REVISION


def test_degradation_migration_revision_id_is_set():
    module = _load_migration_module()
    assert module.revision == "20260521_0001_machine_degradation_score"


def test_degradation_migration_is_idempotent(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp(existing_tables=EXPECTED_TABLES)
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector(EXPECTED_TABLES))

    module.upgrade()

    assert fake_op.created_tables == []
    assert fake_op.created_indexes == []


def test_degradation_migration_downgrade_is_safe_when_tables_absent(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp(existing_tables=[])
    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector([]))

    module.downgrade()

    assert fake_op.dropped_tables == []
    assert fake_op.dropped_indexes == []


def test_degradation_migration_running_state_default_matches_check_constraint():
    migration_source = MIGRATION_PATH.read_text()

    check_match = re.search(
        r"running_state IN \(([^)]+)\)",
        migration_source,
    )
    assert check_match is not None, "Could not find running_state CHECK constraint in migration"
    allowed_states = {s.strip().strip("'\"") for s in check_match.group(1).split(",")}

    default_match = re.search(
        r'server_default="([^"]+)"',
        migration_source.split("running_state")[1].split("\n")[0],
    ) or re.search(
        r"server_default='([^']+)'",
        migration_source.split("running_state")[1].split("\n")[0],
    )
    assert default_match is not None, "Could not find running_state server_default in migration"
    default_value = default_match.group(1)

    assert default_value in allowed_states, (
        f"running_state server_default '{default_value}' is not in CHECK constraint allowed values {allowed_states}"
    )


def test_degradation_model_default_matches_migration_default():
    migration_source = MIGRATION_PATH.read_text()
    model_source = MODEL_PATH.read_text()

    model_match = re.search(
        r'running_state.*?default="([^"]+)"',
        model_source,
    ) or re.search(
        r"running_state.*?default='([^']+)'",
        model_source,
    )
    assert model_match is not None, "Could not find running_state default in model source"
    model_default = model_match.group(1)

    migration_match = re.search(
        r'server_default="([^"]+)"',
        migration_source.split("running_state")[1].split("\n")[0],
    ) or re.search(
        r"server_default='([^']+)'",
        migration_source.split("running_state")[1].split("\n")[0],
    )
    assert migration_match is not None, "Could not find running_state server_default in migration"
    migration_default = migration_match.group(1)

    assert model_default == migration_default, (
        f"Model default '{model_default}' does not match migration server_default '{migration_default}' for running_state"
    )
