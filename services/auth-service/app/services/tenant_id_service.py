from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import TENANT_ID_PREFIX, TENANT_ID_SEQUENCE_WIDTH, TenantIdSequence

MAX_TENANT_SEQUENCE_VALUE = 99_999_999
TENANT_ID_ALLOCATION_ATTEMPTS = 20


class TenantIdAllocationError(RuntimeError):
    """Raised when the auth service cannot allocate a canonical tenant ID."""


def format_tenant_id(sequence_value: int) -> str:
    if sequence_value < 1 or sequence_value > MAX_TENANT_SEQUENCE_VALUE:
        raise TenantIdAllocationError(
            f"Sequence value {sequence_value} is outside the supported tenant ID range"
        )
    return f"{TENANT_ID_PREFIX}{sequence_value:0{TENANT_ID_SEQUENCE_WIDTH}d}"


class TenantIdAllocator:
    """Allocates canonical tenant IDs from persistent DB-backed sequence state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def allocate(self) -> str:
        for _ in range(TENANT_ID_ALLOCATION_ATTEMPTS):
            current_value = await self._load_current_value()
            if current_value is None:
                raise TenantIdAllocationError(
                    "Tenant ID sequence is not configured. Run the auth-service tenant ID migration/reset before creating tenants."
                )
            if current_value > MAX_TENANT_SEQUENCE_VALUE:
                raise TenantIdAllocationError("Tenant ID sequence is exhausted")

            result = await self._session.execute(
                update(TenantIdSequence)
                .where(
                    TenantIdSequence.prefix == TENANT_ID_PREFIX,
                    TenantIdSequence.next_value == current_value,
                )
                .values(next_value=current_value + 1)
            )
            if int(result.rowcount or 0) == 1:
                return format_tenant_id(current_value)

        raise TenantIdAllocationError("Unable to allocate a unique tenant ID")

    async def _load_current_value(self) -> int | None:
        result = await self._session.execute(
            select(TenantIdSequence.next_value).where(TenantIdSequence.prefix == TENANT_ID_PREFIX)
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None
