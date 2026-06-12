from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuthActionToken, AuthActionType

UTC = timezone.utc


def _now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ActionTokenService:
    def _hash_token(self, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def generate_token(self) -> tuple[str, str]:
        raw_token = secrets.token_urlsafe(48)
        return raw_token, self._hash_token(raw_token)

    async def create_token(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        action_type: AuthActionType,
        expires_in_minutes: int,
        created_by_user_id: str | None,
        created_by_role: str | None,
        tenant_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        raw_token, token_hash = self.generate_token()
        expires_at = _now_utc_naive() + timedelta(minutes=expires_in_minutes)
        record = AuthActionToken(
            user_id=user_id,
            action_type=action_type,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by_user_id=created_by_user_id,
            created_by_role=created_by_role,
            tenant_id=tenant_id,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        db.add(record)
        await db.flush()
        return raw_token

    async def invalidate_open_tokens(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        action_type: AuthActionType,
    ) -> None:
        now = _now_utc_naive()
        await db.execute(
            update(AuthActionToken)
            .where(
                AuthActionToken.user_id == user_id,
                AuthActionToken.action_type == action_type,
                AuthActionToken.used_at.is_(None),
            )
            .values(used_at=now)
        )

    async def get_token_status(
        self,
        db: AsyncSession,
        raw_token: str,
    ) -> AuthActionToken | None:
        token_hash = self._hash_token(raw_token)
        result = await db.execute(
            select(AuthActionToken).where(AuthActionToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def consume_token(
        self,
        db: AsyncSession,
        *,
        raw_token: str,
        expected_action_type: AuthActionType,
    ) -> AuthActionToken:
        token = await self.get_token_status(db, raw_token)
        if token is None or token.action_type != expected_action_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ACTION_TOKEN", "message": "Invalid or expired link"},
            )

        now = _now_utc_naive()
        if token.used_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "ACTION_TOKEN_USED", "message": "This link has already been used"},
            )
        if _as_utc_datetime(token.expires_at) <= datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "ACTION_TOKEN_EXPIRED", "message": "This link has expired"},
            )

        token.used_at = now
        await db.flush()
        return token


action_token_svc = ActionTokenService()
