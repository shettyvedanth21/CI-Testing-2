from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.settings import NotificationChannel
from services.shared.tenant_context import TenantContext


class SettingsRepository:
    def __init__(self, db: AsyncSession, ctx: TenantContext):
        self.db = db
        self._tenant_id = ctx.require_tenant()

    async def list_active_channels(self, channel_type: str) -> list[NotificationChannel]:
        result = await self.db.execute(
            select(NotificationChannel).where(
                NotificationChannel.tenant_id == self._tenant_id,
                NotificationChannel.channel_type == channel_type,
                NotificationChannel.is_active.is_(True),
            ).order_by(NotificationChannel.id.asc())
        )
        return list(result.scalars().all())

    async def add_email_channel(self, email: str) -> NotificationChannel:
        normalized = email.strip().lower()
        result = await self.db.execute(
            select(NotificationChannel).where(
                NotificationChannel.tenant_id == self._tenant_id,
                NotificationChannel.channel_type == "email",
                NotificationChannel.value == normalized,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.is_active = True
            await self.db.commit()
            await self.db.refresh(existing)
            return existing

        row = NotificationChannel(
            tenant_id=self._tenant_id,
            channel_type="email",
            value=normalized,
            is_active=True,
            created_at=datetime.utcnow(),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def disable_email_channel(self, channel_id: int) -> bool:
        result = await self.db.execute(
            select(NotificationChannel).where(
                NotificationChannel.id == channel_id,
                NotificationChannel.tenant_id == self._tenant_id,
                NotificationChannel.channel_type == "email",
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            return False
        row.is_active = False
        await self.db.commit()
        return True
