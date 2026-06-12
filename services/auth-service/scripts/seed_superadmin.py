#!/usr/bin/env python3
"""
One-time super admin seed script for Shivex auth-service.

Run interactively:
    python scripts/seed_superadmin.py

Run non-interactively:
    SEED_EMAIL=admin@factory.com SEED_PASSWORD=Secret1234 SEED_FULLNAME="Super Admin" python scripts/seed_superadmin.py

From Docker:
    docker compose exec auth-service python scripts/seed_superadmin.py
"""
import asyncio
import os
import re
import sys
import traceback
import uuid
from datetime import datetime, timezone
from getpass import getpass

try:
    import aiomysql
    from passlib.context import CryptContext
except ImportError:
    print("ERROR: Run: pip install aiomysql passlib[bcrypt]")
    sys.exit(1)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
UTC = timezone.utc


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _validate_email(email: str) -> str:
    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email.strip()):
        raise ValueError("Invalid email address")
    return email.strip().lower()


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")


async def _fetch_existing_superadmins(conn) -> list[str]:
    async with conn.cursor() as cur:
        await cur.execute("SELECT email FROM users WHERE role='super_admin' ORDER BY created_at ASC")
        rows = await cur.fetchall()
        return [row[0] for row in rows]


async def _insert_superadmin(conn, email: str, password: str, full_name: str) -> tuple[str, str]:
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    hashed_password = pwd_ctx.hash(password)

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO users
                (
                    id,
                    tenant_id,
                    email,
                    hashed_password,
                    full_name,
                    `role`,
                    permissions_version,
                    is_active,
                    created_at,
                    updated_at,
                    last_login_at
                )
            VALUES
                (%s, NULL, %s, %s, %s, 'super_admin', 0, 1, %s, %s, NULL)
            """,
            (user_id, email, hashed_password, full_name, now, now),
        )
    await conn.commit()
    return user_id, email


async def main() -> None:
    host = _env("MYSQL_HOST", "localhost")
    port = int(_env("MYSQL_PORT", "3306"))
    user = _env("MYSQL_USER", "energy")
    password = _env("MYSQL_PASSWORD", "energy")
    database = _env("MYSQL_DATABASE", "ai_factoryops")

    seed_email = os.getenv("SEED_EMAIL")
    seed_password = os.getenv("SEED_PASSWORD")
    seed_fullname = os.getenv("SEED_FULLNAME")

    if all([seed_email, seed_password, seed_fullname]):
        email = seed_email
        full_name = seed_fullname
        password_value = seed_password
    else:
        email = input("Super admin email: ").strip()
        full_name = input("Full name: ").strip()
        password_value = getpass("Password: ")
        confirm_password = getpass("Confirm password: ")
        if password_value != confirm_password:
            raise ValueError("Passwords do not match")

    email = _validate_email(email)
    _validate_password(password_value)

    conn = None
    try:
        conn = await aiomysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            db=database,
            autocommit=False,
        )

        existing_superadmins = await _fetch_existing_superadmins(conn)
        if existing_superadmins:
            print("WARNING: Existing super_admin users found:")
            for existing_email in existing_superadmins:
                print(f"  - {existing_email}")
            answer = input("A super_admin already exists. Create another? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Aborted.")
                return

        user_id, created_email = await _insert_superadmin(conn, email, password_value, full_name)
        print("  \u2713 Super admin created successfully.")
        print(f"    ID:    {user_id}")
        print(f"    Email: {created_email}")
    except Exception:
        if conn is not None:
            try:
                await conn.rollback()
            except Exception:
                pass
        traceback.print_exc()
        sys.exit(1)
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    asyncio.run(main())
