"""Alembic environment configuration."""

import asyncio
import os
import sys
import types
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context


def _ensure_migration_env_defaults() -> None:
    if os.getenv("DATABASE_URL"):
        return
    raw_url = context.config.get_main_option("sqlalchemy.url")
    if raw_url.startswith("mysql+pymysql://"):
        raw_url = raw_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    raw_url = raw_url.replace("@mysql:", "@127.0.0.1:")
    os.environ.setdefault("DATABASE_URL", raw_url)
    os.environ.setdefault("DATA_SERVICE_BASE_URL", "http://localhost")
    os.environ.setdefault("RULE_ENGINE_SERVICE_BASE_URL", "http://localhost")
    os.environ.setdefault("REPORTING_SERVICE_BASE_URL", "http://localhost")
    os.environ.setdefault("ENERGY_SERVICE_BASE_URL", "http://localhost")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


def _bootstrap_app_namespace() -> None:
    if "app" in sys.modules:
        return
    app_root = Path(__file__).resolve().parents[1] / "app"
    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = [str(app_root)]
    sys.modules["app"] = app_pkg


_ensure_migration_env_defaults()
_bootstrap_app_namespace()

from app.config import settings
from app.database import Base
from app.models.device import Device  # noqa: F401 - Import all models here

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

VERSION_TABLE_NAME = "alembic_version_device"
# utf8mb4-safe indexed width that comfortably supports descriptive revision IDs.
VERSION_NUM_LENGTH = 191

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_url():
    """Get database URL from settings."""
    return settings.DATABASE_URL


def ensure_version_table_shape(connection: Connection) -> None:
    """Ensure the Alembic version table can store this service's revision IDs."""
    table_exists = connection.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s
        """,
        (VERSION_TABLE_NAME,),
    ).scalar_one()

    if not table_exists:
        connection.exec_driver_sql(
            f"""
            CREATE TABLE `{VERSION_TABLE_NAME}` (
                `version_num` VARCHAR({VERSION_NUM_LENGTH}) NOT NULL,
                PRIMARY KEY (`version_num`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        return

    current_length = connection.exec_driver_sql(
        """
        SELECT CHARACTER_MAXIMUM_LENGTH
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = 'version_num'
        """,
        (VERSION_TABLE_NAME,),
    ).scalar_one_or_none()

    if current_length is None:
        raise RuntimeError(
            f"{VERSION_TABLE_NAME}.version_num is missing; cannot run migrations safely"
        )

    if current_length < VERSION_NUM_LENGTH:
        connection.exec_driver_sql(
            f"""
            ALTER TABLE `{VERSION_TABLE_NAME}`
            MODIFY `version_num` VARCHAR({VERSION_NUM_LENGTH}) NOT NULL
            """
        )


def build_version_table():
    version_table = Table(
        VERSION_TABLE_NAME,
        MetaData(),
        Column("version_num", String(VERSION_NUM_LENGTH), nullable=False),
    )
    version_table.append_constraint(
        PrimaryKeyConstraint("version_num", name=f"{VERSION_TABLE_NAME}_pkc")
    )
    return version_table


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE_NAME,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    ensure_version_table_shape(connection)
    context.configure(
        connection=connection, 
        target_metadata=target_metadata,
        version_table=VERSION_TABLE_NAME,
    )
    context.get_context()._version = build_version_table()

    with context.begin_transaction():
        context.run_migrations()

    # MySQL DDL autocommits schema changes, but the final alembic_version update
    # still needs an explicit commit so the head revision is durable.
    if connection.in_transaction():
        connection.commit()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()
    
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
