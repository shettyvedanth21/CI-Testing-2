from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from jose import JWTError, jwt
from redis.asyncio import Redis as AIORedis
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.auth import RefreshToken, User
from services.shared.tenant_context import normalize_tenant_id

UTC = timezone.utc
logger = logging.getLogger(__name__)
_REDIS_CLIENT: Redis | None = None
_AIOREDIS_CLIENT: AIORedis | None = None
_AIOREDIS_LOOP_ID: int | None = None
_MEMORY_REDIS_VALUES: dict[str, str] = {}
_MEMORY_REDIS_TTLS: dict[str, int] = {}
_MEMORY_REDIS_SETS: dict[str, set[str]] = {}


class _MemorySyncPipeline:
    def __init__(self, client: "_MemorySyncRedis") -> None:
        self._client = client
        self._ops = []

    def set(self, key: str, value: str, ex: int | None = None):
        self._ops.append(lambda: self._client.set(key, value, ex=ex))
        return self

    def sadd(self, key: str, value: str):
        self._ops.append(lambda: self._client.sadd(key, value))
        return self

    def expire(self, key: str, ttl: int):
        self._ops.append(lambda: self._client.expire(key, ttl))
        return self

    def delete(self, key: str):
        self._ops.append(lambda: self._client.delete(key))
        return self

    def srem(self, key: str, value: str):
        self._ops.append(lambda: self._client.srem(key, value))
        return self

    def execute(self):
        return [op() for op in self._ops]


class _MemorySyncRedis:
    def get(self, key: str):
        return _MEMORY_REDIS_VALUES.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        _MEMORY_REDIS_VALUES[key] = value
        if ex is not None:
            _MEMORY_REDIS_TTLS[key] = int(ex)
        return True

    def sadd(self, key: str, value: str):
        _MEMORY_REDIS_SETS.setdefault(key, set()).add(value)
        return 1

    def smembers(self, key: str):
        return set(_MEMORY_REDIS_SETS.get(key, set()))

    def expire(self, key: str, ttl: int):
        _MEMORY_REDIS_TTLS[key] = int(ttl)
        return True

    def ttl(self, key: str):
        return _MEMORY_REDIS_TTLS.get(key, -1)

    def delete(self, key: str):
        _MEMORY_REDIS_VALUES.pop(key, None)
        _MEMORY_REDIS_TTLS.pop(key, None)
        return 1

    def srem(self, key: str, value: str):
        _MEMORY_REDIS_SETS.setdefault(key, set()).discard(value)
        return 1

    def pipeline(self):
        return _MemorySyncPipeline(self)


class _MemoryAsyncPipeline:
    def __init__(self, client: "_MemoryAsyncRedis") -> None:
        self._client = client
        self._ops = []

    def set(self, key: str, value: str, ex: int | None = None):
        async def _op():
            return await self._client.set(key, value, ex=ex)
        self._ops.append(_op)
        return self

    def sadd(self, key: str, value: str):
        async def _op():
            return await self._client.sadd(key, value)
        self._ops.append(_op)
        return self

    def expire(self, key: str, ttl: int):
        async def _op():
            return await self._client.expire(key, ttl)
        self._ops.append(_op)
        return self

    def delete(self, key: str):
        async def _op():
            return await self._client.delete(key)
        self._ops.append(_op)
        return self

    def srem(self, key: str, value: str):
        async def _op():
            return await self._client.srem(key, value)
        self._ops.append(_op)
        return self

    def ttl(self, key: str):
        async def _op():
            return await self._client.ttl(key)
        self._ops.append(_op)
        return self

    async def execute(self):
        results = []
        for op in self._ops:
            results.append(await op())
        return results


class _MemoryAsyncRedis:
    async def get(self, key: str):
        return _MEMORY_REDIS_VALUES.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        _MEMORY_REDIS_VALUES[key] = value
        if ex is not None:
            _MEMORY_REDIS_TTLS[key] = int(ex)
        return True

    async def sadd(self, key: str, value: str):
        _MEMORY_REDIS_SETS.setdefault(key, set()).add(value)
        return 1

    async def smembers(self, key: str):
        return set(_MEMORY_REDIS_SETS.get(key, set()))

    async def expire(self, key: str, ttl: int):
        _MEMORY_REDIS_TTLS[key] = int(ttl)
        return True

    async def ttl(self, key: str):
        return _MEMORY_REDIS_TTLS.get(key, -1)

    async def delete(self, key: str):
        _MEMORY_REDIS_VALUES.pop(key, None)
        _MEMORY_REDIS_TTLS.pop(key, None)
        return 1

    async def srem(self, key: str, value: str):
        _MEMORY_REDIS_SETS.setdefault(key, set()).discard(value)
        return 1

    def pipeline(self, transaction: bool = True):
        return _MemoryAsyncPipeline(self)


def _as_utc_datetime(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    raise ValueError("Unsupported token timestamp")


class TokenService:
    def _get_redis_client(self) -> Redis:
        global _REDIS_CLIENT
        if (settings.REDIS_URL or "").startswith("memory://"):
            return _MemorySyncRedis()  # type: ignore[return-value]
        if _REDIS_CLIENT is None:
            _REDIS_CLIENT = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        return _REDIS_CLIENT

    async def _get_async_redis_client(self) -> AIORedis:
        global _AIOREDIS_CLIENT, _AIOREDIS_LOOP_ID
        if (settings.REDIS_URL or "").startswith("memory://"):
            return _MemoryAsyncRedis()  # type: ignore[return-value]
        current_loop = asyncio.get_running_loop()
        current_loop_id = id(current_loop)
        if _AIOREDIS_CLIENT is not None and _AIOREDIS_LOOP_ID != current_loop_id:
            try:
                await _AIOREDIS_CLIENT.aclose()
            except Exception:
                logger.debug("Failed to close cached async redis client during loop switch", exc_info=True)
            _AIOREDIS_CLIENT = None
            _AIOREDIS_LOOP_ID = None
        if _AIOREDIS_CLIENT is None:
            _AIOREDIS_CLIENT = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
            _AIOREDIS_LOOP_ID = current_loop_id
        return _AIOREDIS_CLIENT

    def _revoked_key(self, jti: str) -> str:
        return f"token:revoked:{jti}"

    def _issued_key(self, user_id: str, jti: str) -> str:
        return f"user:token:{user_id}:{jti}"

    def _issued_index_key(self, user_id: str) -> str:
        return f"user:tokens:{user_id}"

    def _expires_in_seconds(self, exp_value) -> int:
        expires_at = _as_utc_datetime(exp_value)
        remaining = int((expires_at - datetime.now(UTC)).total_seconds())
        return max(1, remaining)

    def _track_access_token(self, user_id: str, jti: str, expires_in_seconds: int) -> None:
        client = self._get_redis_client()
        issued_key = self._issued_key(user_id, jti)
        index_key = self._issued_index_key(user_id)
        try:
            pipe = client.pipeline()
            pipe.set(issued_key, "1", ex=max(1, expires_in_seconds))
            pipe.sadd(index_key, jti)
            pipe.expire(index_key, max(1, expires_in_seconds))
            pipe.execute()
        except RedisError as exc:
            logger.warning("Failed to track issued access token", extra={"user_id": user_id, "jti": jti, "error": str(exc)})

    async def _track_access_token_async(self, user_id: str, jti: str, expires_in_seconds: int) -> None:
        client = await self._get_async_redis_client()
        issued_key = self._issued_key(user_id, jti)
        index_key = self._issued_index_key(user_id)
        try:
            pipe = client.pipeline(transaction=True)
            pipe.set(issued_key, "1", ex=max(1, expires_in_seconds))
            pipe.sadd(index_key, jti)
            pipe.expire(index_key, max(1, expires_in_seconds))
            await pipe.execute()
        except RedisError as exc:
            logger.warning("Failed to track issued access token", extra={"user_id": user_id, "jti": jti, "error": str(exc)})

    def create_access_token(
        self,
        user: User,
        plant_ids: list[str],
        *,
        tenant_entitlements_version: int | None = 0,
    ) -> str:
        if not settings.JWT_SECRET_KEY:
            raise ValueError("JWT_SECRET_KEY must not be empty")

        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        jti = str(uuid4())
        tenant_id = user.tenant_id
        payload = {
            "sub": user.id,
            "email": user.email,
            "tenant_id": tenant_id,
            "role": user.role.value,
            "plant_ids": plant_ids,
            "permissions_version": getattr(user, "permissions_version", 0) or 0,
            "tenant_entitlements_version": tenant_entitlements_version,
            "full_name": user.full_name,
            "type": "access",
            "jti": jti,
            "iat": now,
            "exp": expires_at,
        }
        token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        self._track_access_token(
            user_id=user.id,
            jti=jti,
            expires_in_seconds=max(1, int((expires_at - now).total_seconds())),
        )
        return token

    async def create_access_token_async(
        self,
        user: User,
        plant_ids: list[str],
        *,
        tenant_entitlements_version: int | None = 0,
    ) -> str:
        if not settings.JWT_SECRET_KEY:
            raise ValueError("JWT_SECRET_KEY must not be empty")

        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        jti = str(uuid4())
        tenant_id = user.tenant_id
        payload = {
            "sub": user.id,
            "email": user.email,
            "tenant_id": tenant_id,
            "role": user.role.value,
            "plant_ids": plant_ids,
            "permissions_version": getattr(user, "permissions_version", 0) or 0,
            "tenant_entitlements_version": tenant_entitlements_version,
            "full_name": user.full_name,
            "type": "access",
            "jti": jti,
            "iat": now,
            "exp": expires_at,
        }
        token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        await self._track_access_token_async(
            user_id=user.id,
            jti=jti,
            expires_in_seconds=max(1, int((expires_at - now).total_seconds())),
        )
        return token

    def decode_access_token(self, token: str) -> dict:
        try:
            claims = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_aud": False},
            )
        except JWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            ) from exc

        if claims.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        if claims.get("permissions_version") is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        tenant_id = normalize_tenant_id(claims.get("tenant_id"))
        claims["tenant_id"] = tenant_id

        if tenant_id is not None and claims.get("tenant_entitlements_version") is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        if claims.get("role") != "super_admin" and tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        jti = claims.get("jti")
        if not jti:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )
        try:
            if self.is_access_token_revoked(str(jti)):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "TOKEN_REVOKED", "message": "Token has been revoked"},
                )
        except RedisError as exc:
            logger.warning("Token revocation state unavailable", extra={"error": str(exc)})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "AUTH_STATE_UNAVAILABLE",
                    "message": "Authentication state is temporarily unavailable",
                },
            ) from exc

        return claims

    async def decode_access_token_async(self, token: str) -> dict:
        try:
            claims = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_aud": False},
            )
        except JWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            ) from exc

        if claims.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        if claims.get("permissions_version") is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        tenant_id = normalize_tenant_id(claims.get("tenant_id"))
        claims["tenant_id"] = tenant_id

        if tenant_id is not None and claims.get("tenant_entitlements_version") is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        if claims.get("role") != "super_admin" and tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        jti = claims.get("jti")
        if not jti:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )
        try:
            if await self.is_access_token_revoked_async(str(jti)):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "TOKEN_REVOKED", "message": "Token has been revoked"},
                )
        except RedisError as exc:
            logger.warning("Token revocation state unavailable", extra={"error": str(exc)})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "AUTH_STATE_UNAVAILABLE",
                    "message": "Authentication state is temporarily unavailable",
                },
            ) from exc

        return claims

    def is_access_token_revoked(self, jti: str) -> bool:
        client = self._get_redis_client()
        return client.get(self._revoked_key(jti)) is not None

    async def is_access_token_revoked_async(self, jti: str) -> bool:
        client = await self._get_async_redis_client()
        return await client.get(self._revoked_key(jti)) is not None

    def revoke_access_token(self, jti: str, expires_in_seconds: int) -> None:
        client = self._get_redis_client()
        client.set(self._revoked_key(jti), "1", ex=max(1, expires_in_seconds))

    async def revoke_access_token_async(self, jti: str, expires_in_seconds: int) -> None:
        client = await self._get_async_redis_client()
        await client.set(self._revoked_key(jti), "1", ex=max(1, expires_in_seconds))

    def revoke_access_token_from_claims(self, claims: dict) -> None:
        jti = claims.get("jti")
        exp = claims.get("exp")
        if not jti or exp is None:
            return
        self.revoke_access_token(str(jti), self._expires_in_seconds(exp))

    async def revoke_access_token_from_claims_async(self, claims: dict) -> None:
        jti = claims.get("jti")
        exp = claims.get("exp")
        if not jti or exp is None:
            return
        await self.revoke_access_token_async(str(jti), self._expires_in_seconds(exp))

    def revoke_all_known_access_tokens(self, user_id: str) -> None:
        client = self._get_redis_client()
        index_key = self._issued_index_key(user_id)
        try:
            token_jtis = client.smembers(index_key)
            if not token_jtis:
                return
            pipe = client.pipeline()
            for jti in token_jtis:
                issued_key = self._issued_key(user_id, jti)
                ttl = client.ttl(issued_key)
                if ttl is not None and ttl > 0:
                    pipe.set(self._revoked_key(jti), "1", ex=ttl)
                pipe.delete(issued_key)
                pipe.srem(index_key, jti)
            pipe.execute()
        except RedisError as exc:
            logger.warning("Failed to revoke known access tokens", extra={"user_id": user_id, "error": str(exc)})
            raise

    async def revoke_all_known_access_tokens_async(self, user_id: str) -> None:
        client = await self._get_async_redis_client()
        index_key = self._issued_index_key(user_id)
        try:
            token_jtis = await client.smembers(index_key)
            if not token_jtis:
                return
            pipe = client.pipeline(transaction=True)
            for jti in token_jtis:
                issued_key = self._issued_key(user_id, jti)
                pipe.ttl(issued_key)
                pipe.set(self._revoked_key(jti), "1", ex=max(1, 86400))
                pipe.delete(issued_key)
                pipe.srem(index_key, jti)
            results = await pipe.execute()
            idx = 0
            for jti in token_jtis:
                ttl = results[idx]
                if ttl is not None and ttl > 0:
                    await client.expire(self._revoked_key(jti), ttl)
                idx += 4
        except RedisError as exc:
            logger.warning("Failed to revoke known access tokens", extra={"user_id": user_id, "error": str(exc)})
            raise

    def _hash_token(self, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    def generate_refresh_token_pair(self) -> tuple[str, str]:
        raw_token = secrets.token_urlsafe(64)
        return raw_token, self._hash_token(raw_token)

    async def store_refresh_token(self, db: AsyncSession, user_id: str, token_hash: str) -> RefreshToken:
        now = datetime.now(UTC)
        token = RefreshToken(
            id=str(uuid4()),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            created_at=now,
        )
        db.add(token)
        await db.flush()
        return token

    async def validate_refresh_token(self, db: AsyncSession, raw_token: str) -> RefreshToken:
        token_hash = self._hash_token(raw_token)
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        refresh_token = result.scalar_one_or_none()
        if refresh_token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_REFRESH_TOKEN", "message": "Invalid refresh token"},
            )

        if refresh_token.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "REFRESH_TOKEN_REVOKED", "message": "Refresh token revoked"},
            )

        expires_at = refresh_token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "REFRESH_TOKEN_EXPIRED", "message": "Refresh token expired"},
            )

        return refresh_token

    async def revoke_refresh_token(self, db: AsyncSession, raw_token: str) -> None:
        token_hash = self._hash_token(raw_token)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(revoked_at=datetime.now(UTC))
        )

    async def revoke_all_user_tokens(self, db: AsyncSession, user_id: str) -> None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.revoke_all_known_access_tokens_async(user_id)


def revoke_access_token(jti: str, expires_in_seconds: int) -> None:
    TokenService().revoke_access_token(jti, expires_in_seconds)
