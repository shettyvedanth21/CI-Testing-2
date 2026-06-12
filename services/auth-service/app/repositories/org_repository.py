from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import Organization
from app.services.tenant_id_service import TenantIdAllocator
from services.shared.feature_entitlements import DEFAULT_ROLE_DELEGATIONS

UTC = timezone.utc


class OrgRepository:
    async def create(self, db: AsyncSession, name: str, slug: str) -> Organization:
        now = datetime.now(UTC)
        org = Organization(
            id=await TenantIdAllocator(db).allocate(),
            name=name,
            slug=slug,
            created_at=now,
            updated_at=now,
            entitlements_version=0,
            premium_feature_grants_json=[],
            role_feature_matrix_json={role: list(features) for role, features in DEFAULT_ROLE_DELEGATIONS.items()},
        )
        db.add(org)
        await db.flush()
        return org

    async def get_by_id(self, db: AsyncSession, tenant_id: str) -> Organization | None:
        result = await db.execute(select(Organization).where(Organization.id == tenant_id))
        return result.scalar_one_or_none()

    async def get_by_slug(self, db: AsyncSession, slug: str) -> Organization | None:
        result = await db.execute(select(Organization).where(Organization.slug == slug))
        return result.scalar_one_or_none()

    async def list_all(self, db: AsyncSession) -> list[Organization]:
        result = await db.execute(select(Organization).order_by(Organization.created_at.desc()))
        return list(result.scalars().all())

    async def list_by_ids(self, db: AsyncSession, tenant_ids: list[str]) -> list[Organization]:
        if not tenant_ids:
            return []
        result = await db.execute(
            select(Organization).where(Organization.id.in_(tenant_ids)).order_by(Organization.id.asc())
        )
        return list(result.scalars().all())

    async def update(self, db: AsyncSession, tenant_id: str, updates: dict) -> Organization:
        org = await self.get_by_id(db, tenant_id)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
            )

        allowed_keys = {"name", "slug", "is_active"}
        for key, value in updates.items():
            if key in allowed_keys:
                setattr(org, key, value)

        org.updated_at = datetime.now(UTC)
        await db.flush()
        return org

    async def update_entitlements(
        self,
        db: AsyncSession,
        tenant_id: str,
        *,
        premium_feature_grants: list[str] | None = None,
        role_feature_matrix: dict[str, list[str]] | None = None,
    ) -> Organization:
        org = await self.get_by_id(db, tenant_id)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
            )

        if premium_feature_grants is not None:
            org.premium_feature_grants_json = premium_feature_grants

        if role_feature_matrix is not None:
            org.role_feature_matrix_json = role_feature_matrix

        org.entitlements_version = (org.entitlements_version or 0) + 1
        org.updated_at = datetime.now(UTC)
        await db.flush()
        return org
