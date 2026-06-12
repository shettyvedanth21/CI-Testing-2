"""Tenant notification settings access for rule notification resolution."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import NotificationChannelSetting
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext


class NotificationSettingsRepository(TenantScopedRepository[NotificationChannelSetting]):
    """Read active notification settings from the shared reporting table."""

    model = NotificationChannelSetting

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        super().__init__(session, ctx)

    async def list_active_channel_values(self, channel_type: str) -> list[str]:
        statement = (
            select(NotificationChannelSetting.value)
            .where(
                NotificationChannelSetting.channel_type == channel_type,
                NotificationChannelSetting.is_active.is_(True),
            )
            .order_by(NotificationChannelSetting.id.asc())
        )
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return [str(value).strip() for value in result.scalars().all() if str(value).strip()]
