from __future__ import annotations

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260426_0001_maintenance_log_foundation.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("maintenance_log_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeOp:
    def __init__(self):
        self.created_tables: list[str] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []

    def create_table(self, name: str, *args, **kwargs):
        self.created_tables.append(name)

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs):
        self.created_indexes.append((name, table_name, tuple(columns)))


def test_maintenance_log_migration_creates_expected_table_and_indexes(monkeypatch):
    module = _load_migration_module()
    fake_op = _FakeOp()
    monkeypatch.setattr(module, "op", fake_op)

    module.upgrade()

    assert fake_op.created_tables == ["maintenance_logs"]
    assert (
        "ix_maintenance_logs_tenant_device_date",
        "maintenance_logs",
        ("tenant_id", "device_id", "maintenance_date"),
    ) in fake_op.created_indexes
    assert (
        "ix_maintenance_logs_tenant_device_next_due",
        "maintenance_logs",
        ("tenant_id", "device_id", "next_due_date"),
    ) in fake_op.created_indexes
    assert (
        "ix_maintenance_logs_tenant_status",
        "maintenance_logs",
        ("tenant_id", "status"),
    ) in fake_op.created_indexes
