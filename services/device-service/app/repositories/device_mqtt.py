"""Repository helpers for device MQTT credentials and ACL rows."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.models.device import DeviceMQTTACL, DeviceMQTTCredential
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext


class DeviceMQTTCredentialRepository(TenantScopedRepository[DeviceMQTTCredential]):
    model = DeviceMQTTCredential

    def __init__(self, session, ctx: TenantContext):
        super().__init__(session, ctx)

    async def get_for_device(self, device_id: str) -> DeviceMQTTCredential | None:
        statement = (
            select(DeviceMQTTCredential)
            .where(DeviceMQTTCredential.device_id == device_id)
            .options(selectinload(DeviceMQTTCredential.acl_entries))
        )
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()


class DeviceMQTTACLRepository(TenantScopedRepository[DeviceMQTTACL]):
    model = DeviceMQTTACL

    def __init__(self, session, ctx: TenantContext):
        super().__init__(session, ctx)

    async def list_for_device(self, device_id: str) -> list[DeviceMQTTACL]:
        statement = select(DeviceMQTTACL).where(DeviceMQTTACL.device_id == device_id)
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def replace_for_credential(self, credential_id: int, rows: list[DeviceMQTTACL]) -> list[DeviceMQTTACL]:
        delete_stmt = delete(DeviceMQTTACL).where(DeviceMQTTACL.credential_id == credential_id)
        delete_stmt = self._apply_tenant_scope_dml(delete_stmt)
        await self._session.execute(delete_stmt)
        for row in rows:
            await self.create(row)
        return rows
