"""Database connection management."""

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.config.settings import get_settings
settings = get_settings()
engine = create_async_engine(
    settings.mysql_dsn,
    echo=False,
    pool_size=settings.mysql_pool_size,
    max_overflow=10,
    pool_recycle=settings.mysql_pool_recycle,
    pool_pre_ping=True,
)

async_session_maker = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def is_transient_disconnect(exc: Exception) -> bool:
    """Return True for MySQL disconnects that should be healed by reconnecting."""
    if not isinstance(exc, DBAPIError):
        return False
    if getattr(exc, "connection_invalidated", False):
        return True
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    args = getattr(orig, "args", ())
    if not args:
        return False
    return args[0] in {2003, 2006, 2013}


async def reset_db_connections() -> None:
    """Dispose pooled connections so the next checkout creates fresh connections."""
    await engine.dispose()


async def get_db_session() -> AsyncSession:
    """Get database session."""
    return async_session_maker()
