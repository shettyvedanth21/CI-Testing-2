"""
Direct MySQL client for DB-level verification in E2E tests.
Use sparingly - only when API verification is not sufficient.
"""

from __future__ import annotations

import os

try:
    import pymysql
except ModuleNotFoundError:  # pragma: no cover - optional in minimal test environments
    pymysql = None


def get_db_connection():
    """
    Connect to ai_factoryops directly.
    Prefer env vars, but fall back to local Docker Compose defaults.
    """
    if pymysql is None:
        raise RuntimeError("PyMySQL is not installed in this environment")

    host = os.getenv("MYSQL_HOST", "localhost")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    database = os.getenv("MYSQL_DATABASE", "ai_factoryops")

    candidates = [
        (
            os.getenv("MYSQL_USER", "energy"),
            os.getenv("MYSQL_PASSWORD", "energy"),
        ),
        (
            os.getenv("MYSQL_ROOT_USER", "root"),
            os.getenv("MYSQL_ROOT_PASSWORD", "rootpassword"),
        ),
    ]

    last_error = None
    for user, password in candidates:
        try:
            return pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5,
            )
        except Exception as exc:  # pragma: no cover
            last_error = exc

    raise last_error or RuntimeError("Unable to connect to MySQL")


def db_query(sql: str, params: tuple = ()) -> list:
    """
    Run a SELECT query and return list of dicts.
    Handles connection lifecycle automatically.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())
    finally:
        conn.close()


def db_query_one(sql: str, params: tuple = ()) -> dict | None:
    results = db_query(sql, params)
    return results[0] if results else None
