from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "services" / "energy-service" / "alembic" / "versions" / "0006_add_tenant_id_to_energy_aggregates.py"


spec = importlib.util.spec_from_file_location("energy_migration_0006", MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration = importlib.util.module_from_spec(spec)
sys.modules.setdefault("energy_migration_0006", migration)
spec.loader.exec_module(migration)


class _FakeResult:
    def __init__(self, *, scalar_value=None, mappings_rows=None):
        self._scalar_value = scalar_value
        self._mappings_rows = mappings_rows or []

    def scalar_one(self):
        return self._scalar_value

    def mappings(self):
        return self

    def all(self):
        return self._mappings_rows


class _RecordingBind:
    def __init__(self):
        self.sql: list[str] = []
        self._responses = [
            _FakeResult(scalar_value=0),
            _FakeResult(mappings_rows=[]),
        ]

    def execute(self, statement):
        self.sql.append(str(statement))
        return self._responses.pop(0)


def test_orphan_query_qualifies_ambiguous_columns(monkeypatch):
    bind = _RecordingBind()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    count, rows = migration._orphaned_rows("energy_device_day", "day")

    assert count == 0
    assert rows == []
    assert "CAST(target.id AS CHAR) AS id" in bind.sql[1]
    assert "target.device_id AS device_id" in bind.sql[1]
    assert "CAST(target.day AS CHAR) AS bucket" in bind.sql[1]


def test_tenant_mismatch_query_qualifies_ambiguous_columns(monkeypatch):
    bind = _RecordingBind()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    count, rows = migration._tenant_mismatches("energy_device_month", "month")

    assert count == 0
    assert rows == []
    assert "CAST(target.id AS CHAR) AS id" in bind.sql[1]
    assert "target.device_id AS device_id" in bind.sql[1]
    assert "CAST(target.month AS CHAR) AS bucket" in bind.sql[1]


def test_ensure_tenant_column_skips_add_when_partial_state_column_exists(monkeypatch):
    class _Inspector:
        def get_columns(self, _table_name):
            return [{"name": "id"}, {"name": "tenant_id"}]

    add_calls: list[str] = []
    monkeypatch.setattr(migration.op, "add_column", lambda table_name, _column: add_calls.append(table_name))

    migration._ensure_tenant_column(_Inspector(), "energy_device_day")

    assert add_calls == []
