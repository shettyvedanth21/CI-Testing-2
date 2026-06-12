from contextlib import asynccontextmanager

from sqlalchemy.dialects.mysql.aiomysql import AsyncAdapt_aiomysql_connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings


def _patch_aiomysql_pool_ping() -> None:
    """Keep SQLAlchemy pool pre-ping compatible with aiomysql's reconnect arg."""
    if getattr(AsyncAdapt_aiomysql_connection.ping, "_shivex_compat", False):
        return

    original_ping = AsyncAdapt_aiomysql_connection.ping

    def ping(self, reconnect: bool = False):
        return original_ping(self, reconnect)

    ping._shivex_compat = True  # type: ignore[attr-defined]
    AsyncAdapt_aiomysql_connection.ping = ping


_patch_aiomysql_pool_ping()

engine = create_async_engine(
    settings.mysql_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=3600,
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

readonly_engine = create_async_engine(
    settings.mysql_readonly_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=3600,
)
ReadonlySessionLocal = async_sessionmaker(readonly_engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        await session.close()


@asynccontextmanager
async def get_readonly_db_session():
    session = ReadonlySessionLocal()
    try:
        yield session
    finally:
        await session.close()
