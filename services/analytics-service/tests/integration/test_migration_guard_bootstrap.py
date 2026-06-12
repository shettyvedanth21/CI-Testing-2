from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pymysql
import pytest

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from scripts import migration_guard


SERVICE_ROOT = Path(__file__).resolve().parents[2]
MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
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
    database = f"test_analytics_bootstrap_{uuid.uuid4().hex[:12]}"
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


def test_fresh_bootstrap_and_rerun_succeed_without_replaying_partial_ddl(fresh_database: str):
    first = _run(["python", "scripts/migration_guard.py"], fresh_database)
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
                (fresh_database, migration_guard.ALEMBIC_VERSION_TABLE),
            )
            width_row = cur.fetchone()
            cur.execute(f"SELECT version_num FROM `{migration_guard.ALEMBIC_VERSION_TABLE}`")
            version_row = cur.fetchone()
            cur.execute("SHOW COLUMNS FROM analytics_jobs LIKE 'job_kind'")
            job_kind = cur.fetchone()

    assert width_row is not None
    assert int(width_row["CHARACTER_MAXIMUM_LENGTH"]) >= migration_guard._required_version_num_length()
    assert version_row == {"version_num": "0007_first_class_tenant_columns"}
    assert job_kind is not None

    second = _run(["python", "scripts/migration_guard.py"], fresh_database)
    assert second.returncode == 0, second.stderr or second.stdout
    combined_output = "\n".join(part for part in (second.stdout, second.stderr) if part)
    assert "Duplicate column name" not in combined_output

    with _admin_connection(fresh_database) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT version_num FROM `{migration_guard.ALEMBIC_VERSION_TABLE}`")
            rerun_version_row = cur.fetchone()

    assert rerun_version_row == {"version_num": "0007_first_class_tenant_columns"}


def test_long_revision_id_persists_on_fresh_database(fresh_database: str):
    with _admin_connection(fresh_database) as conn:
        migration_guard._ensure_alembic_version_table(conn)

    upgrade_0006 = _run(["alembic", "upgrade", "0006_fleet_dispatch_orchestration"], fresh_database)
    assert upgrade_0006.returncode == 0, upgrade_0006.stderr or upgrade_0006.stdout

    with _admin_connection(fresh_database) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT version_num FROM `{migration_guard.ALEMBIC_VERSION_TABLE}`")
            version_row = cur.fetchone()

    assert version_row == {"version_num": "0006_fleet_dispatch_orchestration"}

    upgrade_head = _run(["alembic", "upgrade", "head"], fresh_database)
    assert upgrade_head.returncode == 0, upgrade_head.stderr or upgrade_head.stdout
