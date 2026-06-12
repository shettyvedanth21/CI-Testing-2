from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import Plant, User, UserPlantAccess, UserRole

UTC = timezone.utc


class UserRepository:
    async def get_by_email(self, db: AsyncSession, email: str) -> User | None:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, db: AsyncSession, user_id: str) -> User | None:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_tenant(self, db: AsyncSession, user_id: str, tenant_id: str) -> User | None:
        result = await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        db: AsyncSession,
        email: str,
        hashed_password: str,
        role: UserRole,
        tenant_id: str | None,
        full_name: str | None,
    ) -> User:
        now = datetime.now(UTC)
        user = User(
            id=str(uuid4()),
            email=email,
            hashed_password=hashed_password,
            role=role,
            tenant_id=tenant_id,
            full_name=full_name,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.flush()
        return user

    async def update(self, db: AsyncSession, user_id: str, updates: dict) -> User:
        user = await self.get_by_id(db, user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found"},
            )

        allowed_keys = {
            "email",
            "hashed_password",
            "full_name",
            "role",
            "tenant_id",
            "is_active",
            "invited_at",
            "activated_at",
            "deactivated_at",
        }
        for key, value in updates.items():
            if key not in allowed_keys:
                continue
            if key == "role" and value is not None and not isinstance(value, UserRole):
                value = UserRole(value)
            setattr(user, key, value)

        user.updated_at = datetime.now(UTC)
        await db.flush()
        return user

    async def increment_permissions_version(self, db: AsyncSession, user_id: str) -> User:
        user = await self.get_by_id(db, user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found"},
            )

        user.permissions_version = (user.permissions_version or 0) + 1
        user.updated_at = datetime.now(UTC)
        await db.flush()
        return user

    async def list_by_tenant(self, db: AsyncSession, tenant_id: str) -> list[User]:
        result = await db.execute(
            select(User).where(User.tenant_id == tenant_id).order_by(User.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_plant_ids(self, db: AsyncSession, user_id: str) -> list[str]:
        result = await db.execute(
            select(UserPlantAccess.plant_id)
            .where(UserPlantAccess.user_id == user_id)
            .order_by(UserPlantAccess.plant_id.asc())
        )
        return list(result.scalars().all())

    async def set_plant_access(self, db: AsyncSession, user_id: str, plant_ids: list[str]) -> None:
        await db.execute(select(User.id).where(User.id == user_id).with_for_update())
        await db.execute(delete(UserPlantAccess).where(UserPlantAccess.user_id == user_id))

        normalized_plant_ids = list(dict.fromkeys(plant_ids))
        if normalized_plant_ids:
            now = datetime.now(UTC)
            rows = [
                {"user_id": user_id, "plant_id": plant_id, "granted_at": now}
                for plant_id in normalized_plant_ids
            ]
            await db.execute(insert(UserPlantAccess), rows)
