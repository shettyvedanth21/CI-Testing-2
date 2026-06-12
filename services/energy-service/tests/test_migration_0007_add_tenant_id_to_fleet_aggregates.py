from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "services" / "energy-service" / "alembic" / "versions" / "0007_add_tenant_id_to_fleet_aggregates.py"


spec = importlib.util.spec_from_file_location("energy_migration_0007", MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration = importlib.util.module_from_spec(spec)
sys.modules.setdefault("energy_migration_0007", migration)
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
    def __init__(self, responses):
        self.sql: list[str] = []
        self._responses = list(responses)

    def execute(self, statement):
        self.sql.append(str(statement))
        return self._responses.pop(0)


def test_rebuildability_query_qualifies_fleet_bucket(monkeypatch):
    bind = _RecordingBind([
        _FakeResult(scalar_value=0),
    ])
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration._assert_legacy_fleet_rebuildable("energy_fleet_day", "day", "energy_device_day")

    assert "fleet.day" in bind.sql[0]
    assert "source.day = fleet.day" in bind.sql[0]


def test_repopulate_fleet_day_aggregates_by_tenant_and_day(monkeypatch):
    executed: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed.append(str(statement)))

    migration._repopulate_fleet_day()

    sql = executed[0]
    assert "INSERT INTO energy_fleet_day" in sql
    assert "device_day.tenant_id" in sql
    assert "GROUP BY device_day.tenant_id, device_day.day" in sql


def test_ensure_tenant_column_skips_existing_partial_state(monkeypatch):
    class _Inspector:
        def get_columns(self, _table_name):
            return [{"name": "day"}, {"name": "tenant_id"}]

    add_calls: list[str] = []
    monkeypatch.setattr(migration.op, "add_column", lambda table_name, _column: add_calls.append(table_name))

    migration._ensure_tenant_column(_Inspector(), "energy_fleet_day")

    assert add_calls == []
