from __future__ import annotations

import sys
from types import SimpleNamespace

from scripts import migration_guard


def test_run_alembic_upgrade_uses_active_python_interpreter(monkeypatch):
    recorded: dict[str, object] = {}

    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+aiomysql://energy:secret@db.internal:3307/factory_ops",
    )

    def _fake_run(argv, env, capture_output, text, check):
        recorded["argv"] = argv
        recorded["env"] = env
        recorded["capture_output"] = capture_output
        recorded["text"] = text
        recorded["check"] = check
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(migration_guard.subprocess, "run", _fake_run)

    result = migration_guard._run_alembic_upgrade()

    assert result == 0
    assert recorded["argv"] == [sys.executable, "-m", "alembic", "upgrade", "head"]
    assert recorded["env"]["DATABASE_URL"] == "mysql+aiomysql://energy:secret@db.internal:3307/factory_ops"
    assert recorded["capture_output"] is True
    assert recorded["text"] is True
    assert recorded["check"] is False


def test_run_alembic_upgrade_builds_database_url_from_mysql_settings(monkeypatch):
    recorded: dict[str, object] = {}

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "mysql.internal")
    monkeypatch.setenv("MYSQL_PORT", "3308")
    monkeypatch.setenv("MYSQL_USER", "waste-user")
    monkeypatch.setenv("MYSQL_PASSWORD", "p@ss word")
    monkeypatch.setenv("MYSQL_DATABASE", "factory ops")

    def _fake_run(argv, env, capture_output, text, check):
        recorded["argv"] = argv
        recorded["env"] = env
        recorded["capture_output"] = capture_output
        recorded["text"] = text
        recorded["check"] = check
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(migration_guard.subprocess, "run", _fake_run)

    result = migration_guard._run_alembic_upgrade()

    assert result == 0
    assert recorded["argv"] == [sys.executable, "-m", "alembic", "upgrade", "head"]
    assert recorded["env"]["DATABASE_URL"] == (
        "mysql+aiomysql://waste-user:p%40ss%20word@mysql.internal:3308/factory%20ops"
    )
    assert recorded["capture_output"] is True
    assert recorded["text"] is True
    assert recorded["check"] is False
