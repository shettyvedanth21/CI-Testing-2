from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pymysql
import pytest

from scripts import migration_guard


SERVICE_ROOT = Path(__file__).resolve().parents[1]
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "energy")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "energy")
MYSQL_ROOT_PASSWORD = os.getenv("MYSQL_ROOT_PASSWORD", "rootpassword")


def _admin_connection(database: str | None = None) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user="root",
        password=MYSQL_ROOT_PASSWORD,
        database=database,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _require_mysql() -> None:
    try:
        with _admin_connection():
            return
    except pymysql.MySQLError as exc:  # pragma: no cover - environment-dependent guard
        pytest.skip(f"MySQL is unavailable for waste migration bootstrap validation: {exc}")


def _service_env(database: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env["MYSQL_HOST"] = MYSQL_HOST
    env["MYSQL_PORT"] = str(MYSQL_PORT)
    env["MYSQL_USER"] = MYSQL_USER
    env["MYSQL_PASSWORD"] = MYSQL_PASSWORD
    env["MYSQL_DATABASE"] = database
    env.setdefault("APP_ENV", "test")
    env.setdefault("APP_ROLE", "api")
    env.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-at-least-32-chars")
    env.setdefault(
        "INTERNAL_SERVICE_SHARED_SECRET",
        "test-internal-service-secret-at-least-32-chars",
    )
    return env


def _run(command: list[str], database: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=SERVICE_ROOT,
        env=_service_env(database),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture
def fresh_database() -> str:
    _require_mysql()
    database = f"test_waste_bootstrap_{uuid.uuid4().hex[:12]}"
    with _admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{database}`")
            cur.execute(
                f"GRANT ALL PRIVILEGES ON `{database}`.* TO %s@'%%'",
                (MYSQL_USER,),
            )
            cur.execute("FLUSH PRIVILEGES")
    try:
        yield database
    finally:
        with _admin_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{database}`")


def test_fresh_bootstrap_and_rerun_upgrade_head_succeed(fresh_database: str):
    first = _run([sys.executable, "scripts/migration_guard.py"], fresh_database)
    assert first.returncode == 0, first.stderr or first.stdout

    with _admin_connection(fresh_database) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT CHARACTER_MAXIMUM_LENGTH
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                  AND column_name = 'version_num'
                """,
                (fresh_database, migration_guard.ALEMBIC_VERSION_TABLES[0]),
            )
            width_row = cur.fetchone()
            cur.execute("SHOW COLUMNS FROM waste_analysis_jobs LIKE 'retry_count'")
            retry_count = cur.fetchone()
            cur.execute("SHOW COLUMNS FROM waste_analysis_jobs LIKE 'timeout_count'")
            timeout_count = cur.fetchone()
            cur.execute("SHOW TABLES LIKE 'waste_worker_heartbeat'")
            heartbeat_table = cur.fetchone()
            cur.execute(
                """
                SELECT COLUMN_TYPE
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = 'waste_analysis_jobs'
                  AND column_name = 'status'
                """,
                (fresh_database,),
            )
            status_row = cur.fetchone()

    assert width_row is not None
    assert int(width_row["CHARACTER_MAXIMUM_LENGTH"]) >= migration_guard.ALEMBIC_VERSION_NUM_MIN_LENGTH
    assert retry_count is not None
    assert timeout_count is not None
    assert heartbeat_table is not None
    assert status_row is not None
    assert "enqueue_failed" in str(status_row["COLUMN_TYPE"])

    second = _run([sys.executable, "scripts/migration_guard.py"], fresh_database)
    assert second.returncode == 0, second.stderr or second.stdout
