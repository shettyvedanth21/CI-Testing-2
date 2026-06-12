from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from scripts import migration_guard


class _FakeCursor:
    def __init__(self, fetches):
        self._fetches = list(fetches)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        if self._fetches:
            return self._fetches.pop(0)
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, fetches):
        self.cursor_obj = _FakeCursor(fetches)

    def cursor(self):
        return self.cursor_obj


def test_required_version_num_length_covers_current_revisions():
    required_length = migration_guard._required_version_num_length()

    assert required_length >= migration_guard.ALEMBIC_VERSION_NUM_FLOOR
    assert required_length >= len("0006_fleet_dispatch_orchestration")


def test_ensure_alembic_version_table_creates_wide_table_on_first_bootstrap():
    conn = _FakeConnection(fetches=[None])

    migration_guard._ensure_alembic_version_table(conn)

    executed_sql = [sql for sql, _ in conn.cursor_obj.executed]
    assert any("FROM information_schema.tables" in sql for sql in executed_sql)
    create_sql = next(sql for sql in executed_sql if sql.startswith("CREATE TABLE"))
    assert migration_guard.ALEMBIC_VERSION_TABLE in create_sql
    assert f"VARCHAR({migration_guard._required_version_num_length()})" in create_sql


def test_ensure_alembic_version_table_widens_existing_narrow_column():
    conn = _FakeConnection(fetches=[{"1": 1}, {"CHARACTER_MAXIMUM_LENGTH": 32}])

    migration_guard._ensure_alembic_version_table(conn)

    executed_sql = [sql for sql, _ in conn.cursor_obj.executed]
    alter_sql = next(sql for sql in executed_sql if sql.startswith("ALTER TABLE"))
    assert f"VARCHAR({migration_guard._required_version_num_length()})" in alter_sql

