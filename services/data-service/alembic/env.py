from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table, engine_from_config, pool
from sqlalchemy.engine import Connection

from src.config import settings
from src.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.mysql_sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
VERSION_TABLE_NAME = "alembic_version_data"
LEGACY_VERSION_TABLE_NAME = "alembic_version"
VERSION_NUM_LENGTH = 191


def _get_script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(config)


def _get_service_revision_ids() -> set[str]:
    return {revision.revision for revision in _get_script_directory().walk_revisions()}


def _get_single_head_revision() -> str:
    heads = _get_script_directory().get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"data-service migration graph expected exactly one head, found {heads}")
    return heads[0]


def _table_exists(connection: Connection, table_name: str) -> bool:
    return bool(
        connection.exec_driver_sql(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
            """,
            (table_name,),
        ).scalar_one()
    )


def _ensure_version_table_shape(connection: Connection) -> None:
    if not _table_exists(connection, VERSION_TABLE_NAME):
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
        raise RuntimeError(f"{VERSION_TABLE_NAME}.version_num is missing; cannot run migrations safely")
    if int(current_length) < VERSION_NUM_LENGTH:
        connection.exec_driver_sql(
            f"""
            ALTER TABLE `{VERSION_TABLE_NAME}`
            MODIFY `version_num` VARCHAR({VERSION_NUM_LENGTH}) NOT NULL
            """
        )


def _get_version_rows(connection: Connection, table_name: str) -> list[str]:
    if not _table_exists(connection, table_name):
        return []
    rows = connection.exec_driver_sql(f"SELECT version_num FROM `{table_name}`").scalars().all()
    return [str(row) for row in rows if row]


def _stamp_version_table(connection: Connection, revision: str) -> None:
    connection.exec_driver_sql(f"DELETE FROM `{VERSION_TABLE_NAME}`")
    connection.exec_driver_sql(
        f"INSERT INTO `{VERSION_TABLE_NAME}` (version_num) VALUES (%s)",
        (revision,),
    )


def _head_schema_is_present(connection: Connection) -> bool:
    required_tables = (
        "telemetry_outbox",
        "reconciliation_log",
    )
    return all(_table_exists(connection, table_name) for table_name in required_tables)


def _bootstrap_version_table(connection: Connection) -> None:
    if _get_version_rows(connection, VERSION_TABLE_NAME):
        return

    service_revision_ids = _get_service_revision_ids()
    for revision in _get_version_rows(connection, LEGACY_VERSION_TABLE_NAME):
        if revision in service_revision_ids:
            _stamp_version_table(connection, revision)
            return

    head_revision = _get_single_head_revision()
    if _head_schema_is_present(connection):
        _stamp_version_table(connection, head_revision)


def _build_version_table() -> Table:
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
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE_NAME,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        _ensure_version_table_shape(connection)
        _bootstrap_version_table(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE_NAME,
        )
        context.get_context()._version = _build_version_table()
        with context.begin_transaction():
            context.run_migrations()
        if connection.in_transaction():
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
