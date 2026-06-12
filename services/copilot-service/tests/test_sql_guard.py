from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from src.db.query_engine import QueryEngine
from src.utils.sql_guard import SQLGuard


def test_select_allowed():
    guard = SQLGuard()
    column_name = "tenant_id"
    is_safe, reason = guard.validate(f"SELECT * FROM devices WHERE {column_name}='t1'")
    assert is_safe is True
    assert reason == "ok"


def test_insert_blocked():
    guard = SQLGuard()
    is_safe, reason = guard.validate("INSERT INTO devices(device_id) VALUES ('d1')")
    assert is_safe is False
    assert "Only SELECT" in reason or "Blocked keyword" in reason


def test_drop_blocked():
    guard = SQLGuard()
    is_safe, reason = guard.validate("DROP TABLE devices")
    assert is_safe is False
    assert "Only SELECT" in reason or "Blocked keyword" in reason


def test_union_blocked():
    guard = SQLGuard()
    is_safe, reason = guard.validate("SELECT 1 UNION SELECT password FROM users")
    assert is_safe is False
    assert "UNION" in reason


def test_semicolon_blocked():
    guard = SQLGuard()
    is_safe, reason = guard.validate("SELECT 1; DROP TABLE devices")
    assert is_safe is False
    assert "Semicolons" in reason
    assert "Multiple statements" in reason


def test_tenant_filter_injected():
    guard = SQLGuard()
    sql = guard.inject_tenant_filter("SELECT * FROM devices ORDER BY created_at DESC", "tenant-a")
    assert "WHERE tenant_id = :tenant_id" in sql
    assert "ORDER BY created_at DESC" in sql


@pytest.mark.asyncio
async def test_readonly_engine_used(monkeypatch):
    engine = QueryEngine()
    captured: dict[str, object] = {"used": False}
    monkeypatch.setattr(
        "src.db.query_engine.get_schema_manifest",
        lambda: {
            "tables": {
                "devices": {"columns": [{"name": "device_id"}, {"name": "tenant_id"}]},
            }
        },
    )
    QueryEngine._tenant_scoped_tables.cache_clear()

    class FakeResult:
        def fetchmany(self, limit: int):
            return [("dev-1",)]

        def keys(self):
            return ["device_id"]

    class FakeSession:
        async def execute(self, stmt, params=None):
            captured["used"] = True
            captured["sql"] = str(stmt)
            captured["params"] = params
            return FakeResult()

    @asynccontextmanager
    async def fake_readonly_db_session():
        yield FakeSession()

    monkeypatch.setattr("src.db.query_engine.get_readonly_db_session", fake_readonly_db_session)

    result = await engine.execute_query("SELECT device_id FROM devices", tenant_id="tenant-a")

    assert captured["used"] is True
    assert "tenant_id = :tenant_id" in captured["sql"]
    assert captured["params"] == {"tenant_id": "tenant-a"}
    assert result.error is None
    assert result.row_count == 1
    QueryEngine._tenant_scoped_tables.cache_clear()


def test_long_query_blocked():
    guard = SQLGuard()
    sql = "SELECT * FROM devices WHERE note = '" + ("x" * 2100) + "'"
    is_safe, reason = guard.validate(sql)
    assert is_safe is False
    assert "Query too long" in reason
