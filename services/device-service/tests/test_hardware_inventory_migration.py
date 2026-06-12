from __future__ import annotations

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260407_0002_hardware_inventory_foundation.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("hardware_inventory_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeInspector:
    def __init__(self):
        self._tables = {"hardware_units"}
        self._indexes = {
            "hardware_units": [
                {"name": "ix_hardware_units_tenant_id"},
                {"name": "ix_hardware_units_plant_id"},
                {"name": "ix_hardware_units_status"},
                {"name": "ix_hardware_units_unit_type"},
                {"name": "ix_hardware_units_tenant_plant_type"},
            ],
            "device_hardware_installations": [],
        }

    def get_table_names(self):
        return sorted(self._tables)

    def get_indexes(self, table_name: str):
        return list(self._indexes.get(table_name, []))


class _FakeOp:
    def __init__(self):
        self.created_tables: list[str] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []

    def get_bind(self):
        return object()

    def create_table(self, name: str, *args, **kwargs):
        self.created_tables.append(name)

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs):
        self.created_indexes.append((name, table_name, tuple(columns)))


def test_hardware_inventory_migration_resumes_from_partially_applied_state(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp()
    fake_inspector = _FakeInspector()

    monkeypatch.setattr(module, "op", fake_op)
    monkeypatch.setattr(module.sa, "inspect", lambda bind: fake_inspector)

    module.upgrade()

    assert fake_op.created_tables == ["device_hardware_installations"]
    assert ("ix_hardware_units_tenant_id", "hardware_units", ("tenant_id",)) not in fake_op.created_indexes
    assert (
        "ix_device_hardware_installations_tenant_id",
        "device_hardware_installations",
        ("tenant_id",),
    ) in fake_op.created_indexes
