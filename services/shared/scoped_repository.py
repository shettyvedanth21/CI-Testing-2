from __future__ import annotations

from typing import Any, Generic, Optional, Sequence, Type, TypeVar

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.dml import Delete, Update
from sqlalchemy.sql.selectable import Select

from services.shared.tenant_context import TenantContext

ModelT = TypeVar("ModelT")


class TenantScopedRepository(Generic[ModelT]):
    model: Type[ModelT]

    def __init__(
        self,
        session: AsyncSession,
        ctx: TenantContext,
        allow_cross_tenant: bool = False,
    ):
        self._session = session
        self._ctx = ctx
        self._allow_cross_tenant = allow_cross_tenant
        if allow_cross_tenant:
            self._tenant_id = ctx.tenant_id
        else:
            self._tenant_id = ctx.require_tenant()
        self._assert_valid_scope_configuration()

    def _assert_valid_scope_configuration(self) -> None:
        if not self._has_tenant_column():
            return
        if self._allow_cross_tenant and not self._ctx.is_super_admin:
            raise ValueError(
                "Cross-tenant access to tenant-owned repositories requires explicit system context."
            )

    def _has_tenant_column(self) -> bool:
        return hasattr(self.model, "tenant_id")

    def _tenant_filter(self) -> ColumnElement[bool]:
        return getattr(self.model, "tenant_id") == self._tenant_id

    def _apply_tenant_scope_select(self, statement: Select[Any]) -> Select[Any]:
        if self._tenant_id is not None and self._has_tenant_column():
            statement = statement.where(self._tenant_filter())
        return statement

    def _apply_tenant_scope_dml(self, statement: Update | Delete) -> Update | Delete:
        if self._tenant_id is not None and self._has_tenant_column():
            statement = statement.where(self._tenant_filter())
        return statement

    async def get_by_id(
        self,
        resource_id: Any,
        *,
        id_field: str = "id",
        extra_filters: Optional[Sequence[ColumnElement[bool]]] = None,
    ) -> Optional[ModelT]:
        statement = select(self.model).where(getattr(self.model, id_field) == resource_id)
        for extra_filter in extra_filters or []:
            statement = statement.where(extra_filter)
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_all(self, **filters: Any) -> list[ModelT]:
        statement = select(self.model)
        for key, value in filters.items():
            if value is None:
                continue
            statement = statement.where(getattr(self.model, key) == value)
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def create(self, instance: ModelT) -> ModelT:
        if self._tenant_id is not None and hasattr(instance, "tenant_id"):
            setattr(instance, "tenant_id", self._tenant_id)
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def update_by_id(
        self,
        resource_id: Any,
        values: dict[str, Any],
        *,
        id_field: str = "id",
        extra_filters: Optional[Sequence[ColumnElement[bool]]] = None,
    ) -> int:
        if not values:
            return 0
        statement: Update = update(self.model).where(getattr(self.model, id_field) == resource_id)
        for extra_filter in extra_filters or []:
            statement = statement.where(extra_filter)
        statement = self._apply_tenant_scope_dml(statement)
        result = await self._session.execute(statement.values(**values))
        await self._session.flush()
        return int(result.rowcount or 0)

    async def delete_by_id(
        self,
        resource_id: Any,
        *,
        id_field: str = "id",
        extra_filters: Optional[Sequence[ColumnElement[bool]]] = None,
    ) -> int:
        statement: Delete = delete(self.model).where(getattr(self.model, id_field) == resource_id)
        for extra_filter in extra_filters or []:
            statement = statement.where(extra_filter)
        statement = self._apply_tenant_scope_dml(statement)
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def exists(
        self,
        resource_id: Any,
        *,
        id_field: str = "id",
        extra_filters: Optional[Sequence[ColumnElement[bool]]] = None,
    ) -> bool:
        statement = select(self.model).where(getattr(self.model, id_field) == resource_id)
        for extra_filter in extra_filters or []:
            statement = statement.where(extra_filter)
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None
