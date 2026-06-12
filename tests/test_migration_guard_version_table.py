from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_module(relative_path: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCursor:
    def __init__(self, table_rows: dict[str, dict | None]) -> None:
        self._table_rows = table_rows
        self.statements: list[str] = []
        self._last_table: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=None):
        self.statements.append(sql)
        if params:
            self._last_table = params[0]

    def fetchone(self):
        if self._last_table is None:
            return None
        return self._table_rows.get(self._last_table)


class _FakeConnection:
    def __init__(self, table_rows: dict[str, dict | None]) -> None:
        self.cursor_obj = _FakeCursor(table_rows)

    def cursor(self):
        return self.cursor_obj


def test_reporting_guard_creates_wide_version_table_before_alembic_runs():
    module = _load_module("services/reporting-service/scripts/migration_guard.py")
    conn = _FakeConnection({module.ALEMBIC_VERSION_TABLE: None})

    module._ensure_alembic_version_width(conn)

    ddl = "\n".join(conn.cursor_obj.statements)
    assert f"CREATE TABLE IF NOT EXISTS `{module.ALEMBIC_VERSION_TABLE}`" in ddl
    assert f"VARCHAR({module.ALEMBIC_VERSION_NUM_MIN_LENGTH})" in ddl


def test_waste_guard_creates_wide_version_tables_before_alembic_runs():
    module = _load_module("services/waste-analysis-service/scripts/migration_guard.py")
    conn = _FakeConnection({table: None for table in module.ALEMBIC_VERSION_TABLES})

    module._ensure_alembic_version_width(conn)

    ddl = "\n".join(conn.cursor_obj.statements)
    for table_name in module.ALEMBIC_VERSION_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS `{table_name}`" in ddl
    assert f"VARCHAR({module.ALEMBIC_VERSION_NUM_MIN_LENGTH})" in ddl


def test_rule_engine_guard_creates_wide_version_table_before_alembic_runs():
    module = _load_module("services/rule-engine-service/scripts/migration_guard.py")
    conn = _FakeConnection({module.ALEMBIC_VERSION_TABLE: None})

    module._ensure_alembic_version_width(conn)

    ddl = "\n".join(conn.cursor_obj.statements)
    assert f"CREATE TABLE IF NOT EXISTS `{module.ALEMBIC_VERSION_TABLE}`" in ddl
    assert f"VARCHAR({module.ALEMBIC_VERSION_NUM_MIN_LENGTH})" in ddl
