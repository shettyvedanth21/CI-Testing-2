"""Advisory-lock guarded Alembic migration runner for device-service."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse

import pymysql


SERVICE_NAME = "device_service"
LOCK_NAME = f"alembic_migrate_{SERVICE_NAME}"
ALEMBIC_VERSION_TABLE = "alembic_version_device"
LOCK_WAIT_SECONDS = 30
LOCK_RETRY_DELAY_SECONDS = 5
LOCK_TOTAL_WAIT_SECONDS = 60
DB_CONNECT_TOTAL_WAIT_SECONDS = 60
DB_CONNECT_RETRY_DELAY_SECONDS = 5
ALEMBIC_VERSION_NUM_MIN_LENGTH = 64


def _parse_database_settings() -> dict[str, Any]:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        normalized = db_url
        for prefix in ("mysql+aiomysql://", "mysql+pymysql://", "mysql://"):
            if db_url.startswith(prefix):
                normalized = "mysql://" + db_url[len(prefix) :]
                break
        parsed = urlparse(normalized)
        if parsed.scheme != "mysql":
            raise RuntimeError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")
        return {
            "host": parsed.hostname or "mysql",
            "port": parsed.port or 3306,
            "user": parsed.username or "",
            "password": parsed.password or "",
            "database": (parsed.path or "/").lstrip("/"),
        }

    return {
        "host": os.getenv("MYSQL_HOST", "mysql"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "energy"),
        "password": os.getenv("MYSQL_PASSWORD", "energy"),
        "database": os.getenv("MYSQL_DATABASE", "ai_factoryops"),
    }


def _connect() -> pymysql.connections.Connection:
    cfg = _parse_database_settings()
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _connect_with_retry() -> pymysql.connections.Connection:
    deadline = time.monotonic() + DB_CONNECT_TOTAL_WAIT_SECONDS
    last_exc: Exception | None = None
    while True:
        try:
            return _connect()
        except Exception as exc:
            last_exc = exc
            if time.monotonic() >= deadline:
                raise last_exc
            print(
                f"[migration-guard] DB connection failed, retrying: {exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(DB_CONNECT_RETRY_DELAY_SECONDS)


def _acquire_lock(conn: pymysql.connections.Connection) -> bool:
    deadline = time.monotonic() + LOCK_TOTAL_WAIT_SECONDS
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, %s) AS lock_status", (LOCK_NAME, LOCK_WAIT_SECONDS))
            row = cur.fetchone()

        lock_status = row.get("lock_status") if row else None
        if lock_status == 1:
            return True

        if time.monotonic() >= deadline:
            return False

        if lock_status == 0:
            print("[migration-guard] migration lock held by another instance, waiting", flush=True)
        else:
            print(
                f"[migration-guard] unexpected GET_LOCK result for {LOCK_NAME}: {lock_status!r}",
                file=sys.stderr,
            )

        time.sleep(LOCK_RETRY_DELAY_SECONDS)


def _release_lock(conn: pymysql.connections.Connection) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s) AS release_status", (LOCK_NAME,))
    except Exception as exc:  # pragma: no cover - best effort cleanup
        print(f"[migration-guard] failed to release lock {LOCK_NAME}: {exc}", file=sys.stderr)


def _ensure_alembic_version_width(conn: pymysql.connections.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT CHARACTER_MAXIMUM_LENGTH
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name = 'version_num'
            """,
            (ALEMBIC_VERSION_TABLE,),
        )
        row = cur.fetchone()

        if not row:
            return

        current_length = row.get("CHARACTER_MAXIMUM_LENGTH")
        if current_length is None or int(current_length) >= ALEMBIC_VERSION_NUM_MIN_LENGTH:
            return

        print(
            f"[migration-guard] widening {ALEMBIC_VERSION_TABLE}.version_num to VARCHAR({ALEMBIC_VERSION_NUM_MIN_LENGTH})",
            file=sys.stderr,
        )
        cur.execute(
            f"ALTER TABLE `{ALEMBIC_VERSION_TABLE}` MODIFY version_num VARCHAR({ALEMBIC_VERSION_NUM_MIN_LENGTH}) NOT NULL"
        )


def _run_alembic_upgrade() -> int:
    try:
        proc = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True, check=False)
    except Exception as exc:
        print(f"[migration-guard] failed to launch alembic upgrade head: {exc}", file=sys.stderr)
        return 1

    if proc.returncode == 0:
        return 0

    combined_output = f"{proc.stdout}\n{proc.stderr}"
    if "Can't locate revision identified by '0001_auth_schema'" in combined_output:
        print(
            "[migration-guard] skipping migration because shared auth-service revision is present in the global alembic table",
            file=sys.stderr,
        )
        return 0

    if proc.stdout:
        print(proc.stdout, file=sys.stderr, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="" if proc.stderr.endswith("\n") else "\n")
    print(
        f"[migration-guard] alembic upgrade head failed with exit code {proc.returncode}",
        file=sys.stderr,
    )
    return proc.returncode


def main() -> int:
    try:
        conn = _connect_with_retry()
    except Exception as exc:
        print(f"[migration-guard] DB connection failed: {exc}", file=sys.stderr)
        return 1

    with conn:
        if not _acquire_lock(conn):
            print(
                f"[migration-guard] migration lock acquisition timed out for {LOCK_NAME}",
                file=sys.stderr,
            )
            return 1

        try:
            _ensure_alembic_version_width(conn)
            return _run_alembic_upgrade()
        finally:
            _release_lock(conn)


if __name__ == "__main__":
    raise SystemExit(main())
