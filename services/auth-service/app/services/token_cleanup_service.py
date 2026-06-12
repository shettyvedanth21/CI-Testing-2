from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionFactory
from app.models.auth import AuthActionToken, RefreshToken

UTC = timezone.utc
logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
CLEANUP_BATCH_SIZE = 10_000
LOCK_KEY = "auth:refresh_tokens:cleanup:lock"
LOCK_TTL_SECONDS = 55 * 60

_REDIS_CLIENT: Redis | None = None


def _as_utc_datetime(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    raise ValueError("Unsupported token timestamp")


class TokenCleanupService:
    def __init__(self, session_factory=AsyncSessionFactory):
        self._session_factory = session_factory

    def _get_redis_client(self) -> Redis:
        global _REDIS_CLIENT
        if _REDIS_CLIENT is None:
            _REDIS_CLIENT = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        return _REDIS_CLIENT

    def _lock_value(self) -> str:
        return str(uuid4())

    def _acquire_lock(self) -> str | None:
        lock_value = self._lock_value()
        try:
            locked = self._get_redis_client().set(
                LOCK_KEY,
                lock_value,
                nx=True,
                ex=LOCK_TTL_SECONDS,
            )
        except RedisError as exc:
            logger.warning("Refresh token cleanup lock unavailable", extra={"error": str(exc)})
            return None
        return lock_value if locked else None

    def _release_lock(self, lock_value: str) -> None:
        try:
            self._get_redis_client().eval(
                """
                if redis.call("GET", KEYS[1]) == ARGV[1] then
                    return redis.call("DEL", KEYS[1])
                end
                return 0
                """,
                1,
                LOCK_KEY,
                lock_value,
            )
        except RedisError as exc:
            logger.warning("Failed to release refresh token cleanup lock", extra={"error": str(exc)})

    def _refresh_lock(self, lock_value: str) -> None:
        try:
            refreshed = self._get_redis_client().eval(
                """
                if redis.call("GET", KEYS[1]) == ARGV[1] then
                    return redis.call("EXPIRE", KEYS[1], ARGV[2])
                end
                return 0
                """,
                1,
                LOCK_KEY,
                lock_value,
                LOCK_TTL_SECONDS,
            )
            if refreshed != 1:
                logger.warning("Refresh token cleanup lock was not refreshed", extra={"lock_key": LOCK_KEY})
        except RedisError as exc:
            logger.warning("Failed to refresh refresh token cleanup lock", extra={"error": str(exc)})

    async def purge_refresh_tokens_once(self, db: AsyncSession, *, batch_size: int = CLEANUP_BATCH_SIZE) -> int:
        now = datetime.now(UTC)
        result = await db.execute(
            select(RefreshToken.id)
            .where(
                or_(
                    RefreshToken.expires_at < now,
                    RefreshToken.revoked_at.is_not(None),
                )
            )
            .order_by(RefreshToken.expires_at.asc(), RefreshToken.id.asc())
            .limit(batch_size)
        )
        token_ids = list(result.scalars().all())
        if not token_ids:
            return 0

        await db.execute(delete(RefreshToken).where(RefreshToken.id.in_(token_ids)))
        await db.commit()
        return len(token_ids)

    async def purge_action_tokens_once(self, db: AsyncSession, *, batch_size: int = CLEANUP_BATCH_SIZE) -> int:
        retention_cutoff = datetime.now(UTC) - timedelta(hours=settings.ACTION_TOKEN_RETENTION_HOURS)
        result = await db.execute(
            select(AuthActionToken.id)
            .where(
                or_(
                    AuthActionToken.used_at < retention_cutoff,
                    (AuthActionToken.used_at.is_(None) & (AuthActionToken.expires_at < retention_cutoff)),
                )
            )
            .order_by(
                AuthActionToken.used_at.asc(),
                AuthActionToken.expires_at.asc(),
                AuthActionToken.id.asc(),
            )
            .limit(batch_size)
        )
        token_ids = list(result.scalars().all())
        if not token_ids:
            return 0

        await db.execute(delete(AuthActionToken).where(AuthActionToken.id.in_(token_ids)))
        await db.commit()
        return len(token_ids)

    async def purge_until_empty(
        self,
        db: AsyncSession,
        *,
        batch_size: int = CLEANUP_BATCH_SIZE,
        lock_value: str | None = None,
    ) -> int:
        total_deleted = 0
        while True:
            deleted_refresh_tokens = await self.purge_refresh_tokens_once(db, batch_size=batch_size)
            total_deleted += deleted_refresh_tokens
            if lock_value is not None:
                self._refresh_lock(lock_value)
            if deleted_refresh_tokens == batch_size:
                continue

            deleted_action_tokens = await self.purge_action_tokens_once(db, batch_size=batch_size)
            total_deleted += deleted_action_tokens
            if lock_value is not None:
                self._refresh_lock(lock_value)
            if deleted_action_tokens < batch_size:
                return total_deleted

    async def run_cycle(
        self,
        *,
        batch_size: int = CLEANUP_BATCH_SIZE,
        lock_value: str | None = None,
    ) -> int:
        async with self._session_factory() as db:
            return await self.purge_until_empty(db, batch_size=batch_size, lock_value=lock_value)

    async def run_forever(self, *, sleep=asyncio.sleep, batch_size: int = CLEANUP_BATCH_SIZE) -> None:
        while True:
            try:
                lock_value = self._acquire_lock()
                if lock_value is None:
                    await sleep(CLEANUP_INTERVAL_SECONDS)
                    continue

                try:
                    deleted = await self.run_cycle(batch_size=batch_size, lock_value=lock_value)
                    if deleted:
                        logger.info(
                            "Purged auth tokens",
                            extra={"deleted": deleted, "interval_seconds": CLEANUP_INTERVAL_SECONDS},
                        )
                finally:
                    self._release_lock(lock_value)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Refresh token cleanup loop failed", extra={"error": str(exc)})

            await sleep(CLEANUP_INTERVAL_SECONDS)


refresh_token_cleanup_svc = TokenCleanupService()
