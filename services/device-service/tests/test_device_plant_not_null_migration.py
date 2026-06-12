from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260414_0002_enforce_device_plant_not_null.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("device_plant_not_null_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeInspector:
    def __init__(self, *, nullable: bool = True):
        self.nullable = nullable

    def get_columns(self, table_name: str):
        assert table_name == "devices"
        return [{"name": "plant_id", "nullable": self.nullable}]


class _FakeBind:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _statement):
        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return list(self._rows)

        return _Result(self.rows)


class _FakeOp:
    def __init__(self, bind):
        self.bind = bind
        self.alter_calls: list[tuple[str, str, bool]] = []

    def get_bind(self):
        return self.bind

    def alter_column(self, table_name: str, column_name: str, **kwargs):
        self.alter_calls.append((table_name, column_name, kwargs["nullable"]))


def test_device_plant_not_null_migration_fails_explicitly_when_orphans_exist(monkeypatch):
    module = _load_migration_module()
    fake_bind = _FakeBind(rows=[type("Row", (), {"tenant_id": "TENANT-A", "device_id": "ORPHAN-1"})()])
    fake_op = _FakeOp(fake_bind)

    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector(nullable=True))

    with pytest.raises(RuntimeError, match="orphan devices exist"):
        module.upgrade()

    assert fake_op.alter_calls == []


def test_device_plant_not_null_migration_enforces_not_null_when_orphans_absent(monkeypatch):
    module = _load_migration_module()
    fake_bind = _FakeBind(rows=[])
    fake_op = _FakeOp(fake_bind)

    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: _FakeInspector(nullable=True))

    module.upgrade()

    assert fake_op.alter_calls == [("devices", "plant_id", False)]
