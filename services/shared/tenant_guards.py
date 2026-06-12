from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .tenant_context import TenantContext
from .request_context import current_http_path

logger = logging.getLogger(__name__)
_AUDIT_ENGINE = None
_AUDIT_SESSION_FACTORY = None


def _get_audit_session_factory():
    global _AUDIT_ENGINE, _AUDIT_SESSION_FACTORY
    if _AUDIT_SESSION_FACTORY is not None:
        return _AUDIT_SESSION_FACTORY

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return None

    _AUDIT_ENGINE = create_async_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
    _AUDIT_SESSION_FACTORY = async_sessionmaker(_AUDIT_ENGINE, expire_on_commit=False)
    return _AUDIT_SESSION_FACTORY


async def _write_cross_tenant_audit(
    ctx: TenantContext,
    resource_tenant_id: Optional[str],
    resource_type: str,
    resource_id: Any,
) -> None:
    try:
        session_factory = _get_audit_session_factory()
        if session_factory is None:
            return

        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO tenant_security_audit_log (
                        event_type,
                        caller_tenant_id,
                        caller_user_id,
                        target_tenant_id,
                        target_resource_type,
                        target_resource_id,
                        http_path,
                        outcome,
                        detail
                    ) VALUES (
                        :event_type,
                        :caller_tenant_id,
                        :caller_user_id,
                        :target_tenant_id,
                        :target_resource_type,
                        :target_resource_id,
                        :http_path,
                        :outcome,
                        :detail
                    )
                    """
                ),
                {
                    "event_type": "CROSS_TENANT_ATTEMPT",
                    "caller_tenant_id": ctx.tenant_id,
                    "caller_user_id": ctx.user_id,
                    "target_tenant_id": resource_tenant_id,
                    "target_resource_type": resource_type,
                    "target_resource_id": str(resource_id),
                    "http_path": current_http_path.get(),
                    "outcome": "BLOCKED",
                    "detail": "Cross-tenant access was blocked by tenant guards.",
                },
            )
            await session.commit()
    except Exception:
        logger.debug(
            "Failed to write tenant security audit entry",
            exc_info=True,
            extra={
                "caller_tenant_id": ctx.tenant_id,
                "target_tenant_id": resource_tenant_id,
                "resource_type": resource_type,
                "resource_id": str(resource_id),
            },
        )


def _schedule_cross_tenant_audit(
    ctx: TenantContext,
    resource_tenant_id: Optional[str],
    resource_type: str,
    resource_id: Any,
) -> None:
    try:
        asyncio.get_running_loop().create_task(
            _write_cross_tenant_audit(ctx, resource_tenant_id, resource_type, resource_id)
        )
    except RuntimeError:
        return


def assert_same_tenant(
    ctx: TenantContext,
    resource_tenant_id: Optional[str],
    resource_type: str,
    resource_id: Any,
) -> None:
    if ctx.is_super_admin:
        return

    if resource_tenant_id is None:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "RESOURCE_MISSING_TENANT",
                "message": (
                    f"{resource_type} '{resource_id}' is missing tenant ownership."
                ),
            },
        )

    if resource_tenant_id != ctx.require_tenant():
        _schedule_cross_tenant_audit(ctx, resource_tenant_id, resource_type, resource_id)
        raise HTTPException(
            status_code=404,
            detail={
                "code": "RESOURCE_NOT_FOUND",
                "message": f"{resource_type} '{resource_id}' was not found.",
            },
        )


def assert_plants_belong_to_tenant(
    plant_ids: list[str], valid_plant_ids: set[str], ctx: TenantContext
) -> None:
    if ctx.is_super_admin:
        return

    foreign = set(plant_ids) - valid_plant_ids
    if foreign:
        rejected_ids = sorted(foreign)
        raise HTTPException(
            status_code=403,
            detail={
                "code": "INVALID_PLANT_IDS",
                "message": "One or more plant IDs do not belong to this tenant.",
                "rejected_ids": rejected_ids,
            },
        )
