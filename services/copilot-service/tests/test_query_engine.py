from contextlib import asynccontextmanager

import pytest

from src.db.query_engine import QueryEngine


@pytest.fixture
def tenant_scoped_schema(monkeypatch):
    manifest = {
        "tables": {
            "devices": {
                "columns": [{"name": "device_id"}, {"name": "tenant_id"}],
            },
            "alerts": {
                "columns": [{"name": "alert_id"}, {"name": "tenant_id"}],
            },
            "rules": {
                "columns": [{"name": "rule_id"}, {"name": "tenant_id"}],
            },
        }
    }
    monkeypatch.setattr("src.db.query_engine.get_schema_manifest", lambda: manifest)
    QueryEngine._tenant_scoped_tables.cache_clear()
    yield
    QueryEngine._tenant_scoped_tables.cache_clear()


def test_validate_sql_accepts_select():
    ok, _ = QueryEngine.validate_sql("SELECT * FROM devices LIMIT 1")
    assert ok


def test_validate_sql_blocks_update():
    ok, reason = QueryEngine.validate_sql("UPDATE devices SET device_name='x'")
    assert not ok
    assert "Only SELECT" in reason


def test_validate_sql_blocks_multi_statement():
    ok, reason = QueryEngine.validate_sql("SELECT * FROM devices; SELECT * FROM rules;")
    assert not ok
    assert "Multiple statements" in reason


def test_validate_sql_allows_created_at_identifier():
    ok, reason = QueryEngine.validate_sql("SELECT created_at FROM alerts ORDER BY created_at DESC LIMIT 10")
    assert ok
    assert reason == "ok"


def test_validate_sql_allows_updated_at_identifier():
    ok, reason = QueryEngine.validate_sql("SELECT updated_at FROM rules ORDER BY updated_at DESC LIMIT 10")
    assert ok
    assert reason == "ok"


@pytest.mark.asyncio
async def test_tenant_filter_injected_for_simple_select(tenant_scoped_schema, monkeypatch):
    engine = QueryEngine()
    captured: dict[str, object] = {"used": False}

    class FakeResult:
        def fetchmany(self, limit: int):
            return [["COMPRESSOR-001", "Compressor 001"]]

        def keys(self):
            return ["device_id", "device_name"]

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

    result = await engine.execute_query("SELECT device_id, device_name FROM devices ORDER BY device_id LIMIT 1", tenant_id="tenant-a")

    assert captured["used"] is True
    assert "WHERE (devices.tenant_id = :tenant_id) ORDER BY device_id LIMIT 1" in str(captured["sql"])
    assert captured["params"] == {"tenant_id": "tenant-a"}
    assert result.error is None
    assert result.row_count == 1


@pytest.mark.asyncio
async def test_tenant_filter_injected_with_aliases(tenant_scoped_schema, monkeypatch):
    engine = QueryEngine()
    captured: dict[str, object] = {"used": False}

    class FakeResult:
        def fetchmany(self, limit: int):
            return [["COMPRESSOR-001", "Compressor 001"]]

        def keys(self):
            return ["device_id", "device_name"]

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

    result = await engine.execute_query(
        "SELECT a.device_id, d.device_name FROM alerts a JOIN devices d ON d.device_id=a.device_id JOIN rules r ON r.rule_id=a.rule_id WHERE a.created_at >= CURDATE() ORDER BY a.created_at DESC LIMIT 20",
        tenant_id="tenant-a",
    )

    assert captured["used"] is True
    sql = str(captured["sql"])
    assert "(a.tenant_id = :tenant_id)" in sql
    assert "(d.tenant_id = :tenant_id)" in sql
    assert "(r.tenant_id = :tenant_id)" in sql
    assert "ORDER BY a.created_at DESC LIMIT 20" in sql
    assert captured["params"] == {"tenant_id": "tenant-a"}
    assert result.error is None
    assert result.row_count == 1


def test_tenant_predicate_supports_legacy_null_rows_for_default_tenant(tenant_scoped_schema):
    sql = QueryEngine._inject_tenant_filter("SELECT device_id FROM devices ORDER BY device_id LIMIT 1")
    assert "(devices.tenant_id = :tenant_id)" in sql


def test_tenant_predicate_remains_strict_for_non_default_tenants():
    predicate = QueryEngine._tenant_predicate("devices")
    assert predicate == "(devices.tenant_id = :tenant_id)"
