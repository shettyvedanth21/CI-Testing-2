from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import Plant

UTC = timezone.utc


class PlantRepository:
    async def create(
        self,
        db: AsyncSession,
        tenant_id: str,
        name: str,
        location: str | None,
        timezone: str,
    ) -> Plant:
        now = datetime.now(UTC)
        plant = Plant(
            id=str(uuid4()),
            tenant_id=tenant_id,
            name=name,
            location=location,
            timezone=timezone,
            created_at=now,
            updated_at=now,
        )
        db.add(plant)
        await db.flush()
        return plant

    async def get_by_id(self, db: AsyncSession, plant_id: str) -> Plant | None:
        result = await db.execute(select(Plant).where(Plant.id == plant_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_tenant(self, db: AsyncSession, tenant_id: str, plant_id: str) -> Plant | None:
        result = await db.execute(
            select(Plant).where(
                Plant.id == plant_id,
                Plant.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(self, db: AsyncSession, tenant_id: str) -> list[Plant]:
        result = await db.execute(select(Plant).where(Plant.tenant_id == tenant_id).order_by(Plant.created_at.desc()))
        return list(result.scalars().all())

    async def list_active_by_tenant(self, db: AsyncSession, tenant_id: str) -> list[Plant]:
        result = await db.execute(
            select(Plant)
            .where(
                Plant.tenant_id == tenant_id,
                Plant.is_active.is_(True),
            )
            .order_by(Plant.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_by_ids_for_tenant(self, db: AsyncSession, tenant_id: str, plant_ids: list[str]) -> list[Plant]:
        if not plant_ids:
            return []
        result = await db.execute(
            select(Plant)
            .where(Plant.tenant_id == tenant_id)
            .where(Plant.id.in_(plant_ids))
        )
        return list(result.scalars().all())

    async def update(self, db: AsyncSession, plant_id: str, updates: dict) -> Plant:
        plant = await self.get_by_id(db, plant_id)
        if plant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLANT_NOT_FOUND", "message": "Plant not found"},
            )

        allowed_keys = {"name", "location", "timezone", "is_active"}
        for key, value in updates.items():
            if key in allowed_keys:
                setattr(plant, key, value)

        plant.updated_at = datetime.now(UTC)
        await db.flush()
        return plant
