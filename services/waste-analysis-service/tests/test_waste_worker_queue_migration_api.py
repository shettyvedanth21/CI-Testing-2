from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa


REVISION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
)


def _load_revision_module(revision_filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name,
        REVISION_PATH / revision_filename,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_uses_keyword_alter_column_arguments(monkeypatch):
    revision = _load_revision_module("011_waste_worker_queue_columns.py", "waste_rev_011")
    alter_calls: list[tuple[str, str, dict]] = []

    class _FakeInspector:
        def get_columns(self, table_name):
            if table_name == "waste_analysis_jobs":
                return [
                    {"name": "id", "type": SimpleNamespace(enums=None)},
                    {
                        "name": "status",
                        "type": SimpleNamespace(enums=["pending", "running", "completed", "failed"]),
                    },
                ]
            return []

        def get_table_names(self):
            return []

    class _FakeOp:
        def get_bind(self):
            return object()

        def add_column(self, table_name, column):
            return None

        def execute(self, statement):
            return None

        def alter_column(self, table_name, column_name, **kwargs):
            alter_calls.append((table_name, column_name, kwargs))

        def create_table(self, table_name, *columns):
            return None

    monkeypatch.setattr(revision, "inspect", lambda conn: _FakeInspector())
    monkeypatch.setattr(revision, "op", _FakeOp())

    revision.upgrade()

    status_call = next(
        (kwargs for table_name, column_name, kwargs in alter_calls if table_name == "waste_analysis_jobs" and column_name == "status"),
        None,
    )
    retry_call = next(
        (kwargs for table_name, column_name, kwargs in alter_calls if table_name == "waste_analysis_jobs" and column_name == "retry_count"),
        None,
    )
    timeout_call = next(
        (kwargs for table_name, column_name, kwargs in alter_calls if table_name == "waste_analysis_jobs" and column_name == "timeout_count"),
        None,
    )

    assert retry_call is not None
    assert "existing_type" in retry_call
    assert isinstance(retry_call["existing_type"], sa.Integer)
    assert retry_call.get("existing_nullable") is True
    assert retry_call.get("nullable") is False

    assert timeout_call is not None
    assert "existing_type" in timeout_call
    assert isinstance(timeout_call["existing_type"], sa.Integer)
    assert timeout_call.get("existing_nullable") is True
    assert timeout_call.get("nullable") is False

    assert status_call is not None
    assert "type_" in status_call
    assert "existing_type" in status_call
    assert status_call.get("existing_nullable") is False


def test_tenant_scope_migration_skips_cross_service_backfill_when_devices_table_is_absent(monkeypatch):
    revision = _load_revision_module("005_add_tenant_scope_to_waste_jobs.py", "waste_rev_005")
    executed_sql: list[str] = []
    alter_calls: list[tuple[str, str, dict]] = []

    class _ScalarResult:
        def scalar_one(self):
            return 0

    class _FakeBind:
        def execute(self, statement):
            sql_text = str(statement)
            executed_sql.append(sql_text)
            if "SELECT COUNT(*) FROM waste_analysis_jobs WHERE tenant_id IS NULL" in sql_text:
                return _ScalarResult()
            return None

    class _FakeInspector:
        def get_columns(self, table_name):
            assert table_name == "waste_analysis_jobs"
            return [{"name": "id"}, {"name": "tenant_id"}]

        def get_table_names(self):
            return ["waste_analysis_jobs", "waste_device_summary"]

        def get_indexes(self, table_name):
            return []

    class _FakeOp:
        def __init__(self):
            self._bind = _FakeBind()

        def get_bind(self):
            return self._bind

        def add_column(self, table_name, column):
            return None

        def execute(self, statement):
            return self._bind.execute(statement)

        def alter_column(self, table_name, column_name, **kwargs):
            alter_calls.append((table_name, column_name, kwargs))

        def drop_index(self, *args, **kwargs):
            return None

        def create_index(self, *args, **kwargs):
            return None

    monkeypatch.setattr(revision, "inspect", lambda conn: _FakeInspector())
    monkeypatch.setattr(revision, "op", _FakeOp())

    revision.upgrade()

    assert any("JSON_EXTRACT(result_json, '$.tenant_id')" in sql for sql in executed_sql)
    assert not any("JOIN devices AS d" in sql for sql in executed_sql)
    assert any(
        table_name == "waste_analysis_jobs" and column_name == "tenant_id" and kwargs.get("nullable") is False
        for table_name, column_name, kwargs in alter_calls
    )
