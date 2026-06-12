from typing import AsyncGenerator

from sqlalchemy.dialects.mysql.aiomysql import AsyncAdapt_aiomysql_connection
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _patch_aiomysql_pool_ping() -> None:
    if getattr(AsyncAdapt_aiomysql_connection.ping, "_shivex_compat", False):
        return

    original_ping = AsyncAdapt_aiomysql_connection.ping

    def ping(self, reconnect: bool = False):
        return original_ping(self, reconnect)

    ping._shivex_compat = True  # type: ignore[attr-defined]
    AsyncAdapt_aiomysql_connection.ping = ping


_patch_aiomysql_pool_ping()


class Base(DeclarativeBase):
    pass


database_url = make_url(settings.DATABASE_URL)
engine_kwargs = {
    "pool_recycle": 1800,
    "pool_pre_ping": True,
    "echo": settings.SQLALCHEMY_ECHO,
}
if not database_url.drivername.startswith("sqlite"):
    engine_kwargs.update(
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
    )

engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs,
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
