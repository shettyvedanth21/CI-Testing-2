from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.dialects.mysql.aiomysql import AsyncAdapt_aiomysql_connection

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

engine_kwargs = {
    "echo": False,
}

if settings.DATABASE_URL and not settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs.update(
        {
            "pool_size": settings.DATABASE_POOL_SIZE,
            "max_overflow": settings.DATABASE_MAX_OVERFLOW,
            "pool_timeout": settings.DATABASE_POOL_TIMEOUT,
            "pool_recycle": settings.DATABASE_POOL_RECYCLE,
            "pool_pre_ping": True,
        }
    )

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
