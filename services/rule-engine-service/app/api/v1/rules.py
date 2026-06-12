"""API endpoints for rule management."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.rule import (
    RuleCreate,
    RuleUpdate,
    RuleStatusUpdate,
    RuleResponse,
    RuleListResponse,
    RuleSingleResponse,
    RuleStatusResponse,
    RuleDeleteResponse,
    ErrorResponse,
    RuleStatus,
    TelemetryPayload,
)
from app.services.rule import DuplicateRuleError, RuleService
from app.services.evaluator import RuleEvaluator
from app.services.device_scope import DeviceScopeService
from app.notifications.adapter import NotificationAdapter
from app.models.rule import NotificationDeliveryStatus
from app.services.notification_delivery import NotificationDeliveryAuditService
from services.shared.tenant_context import TenantContext
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _summarize_selected_devices(device_ids: list[str], limit: int = 6) -> str:
    cleaned = [str(device_id).strip() for device_id in device_ids if str(device_id).strip()]
    if not cleaned:
        return "Not specified"
    if len(cleaned) <= limit:
        return ", ".join(cleaned)
    head = ", ".join(cleaned[:limit])
    return f"{head} (+{len(cleaned) - limit} more)"


def _rule_scope_email_display(scope_value: str, device_ids: list[str]) -> tuple[str, str, str]:
    normalized_scope = str(scope_value or "").strip().lower()
    if normalized_scope == "all_devices":
        return ("All Machines", "All accessible machines", "All Machines")
    if normalized_scope == "selected_devices":
        selected = _summarize_selected_devices(device_ids)
        return ("Selected Machines", selected, selected)
    selected = _summarize_selected_devices(device_ids)
    return ("Scoped Machines", selected, selected)


def _tenant_context(request: Request) -> TenantContext:
    ctx = TenantContext.from_request(request)
    ctx.require_tenant()
    return ctx


async def _resolve_accessible_device_ids(ctx: TenantContext) -> list[str] | None:
    try:
        return await DeviceScopeService(ctx).resolve_accessible_device_ids()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DEVICE_SCOPE_UNAVAILABLE",
                "message": "Unable to validate device access right now.",
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DEVICE_SCOPE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc


@router.get(
    "/{rule_id}",
    response_model=RuleSingleResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Rule not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_rule(
    rule_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RuleSingleResponse:
    """Get a rule by ID.
    
    - **rule_id**: Unique rule identifier (UUID)
    """
    ctx = _tenant_context(request)
    service = RuleService(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    rule = await service.get_rule(rule_id, accessible_device_ids=accessible_device_ids)
    
    if not rule:
        logger.warning("Rule not found", extra={"rule_id": str(rule_id)})
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_NOT_FOUND",
                    "message": f"Rule with ID '{rule_id}' not found",
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    
    return RuleSingleResponse(data=rule)


@router.get(
    "",
    response_model=RuleListResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_rules(
    request: Request,
    status: Optional[RuleStatus] = Query(None, description="Filter by rule status"),
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
) -> RuleListResponse:
    """List all rules with optional filtering and pagination."""
    ctx = _tenant_context(request)
    service = RuleService(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    rules, total = await service.list_rules(
        status=status,
        device_id=device_id,
        page=page,
        page_size=page_size,
        accessible_device_ids=accessible_device_ids,
    )
    
    total_pages = (total + page_size - 1) // page_size
    
    return RuleListResponse(
        data=rules,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post(
    "",
    response_model=RuleSingleResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        409: {"model": ErrorResponse, "description": "Duplicate rule"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_rule(
    rule_data: RuleCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RuleSingleResponse:
    """Create a new rule."""
    ctx = _tenant_context(request)
    service = RuleService(db, ctx)
    rule_data.tenant_id = ctx.require_tenant()
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    
    try:
        rule = await service.create_rule(rule_data, accessible_device_ids=accessible_device_ids)
        
        await db.commit()
        await db.refresh(rule)
        
        if rule.notification_channels and "email" in rule.notification_channels:
            try:
                device_ids = rule.device_ids or []
                scope_value = str(rule.scope.value) if hasattr(rule.scope, "value") else str(rule.scope)
                scope_label, devices_display, device_id_display = _rule_scope_email_display(scope_value, device_ids)
                
                status_value = rule.status.value if hasattr(rule.status, 'value') else str(rule.status)
                
                adapter = NotificationAdapter(
                    audit_service=NotificationDeliveryAuditService(db, ctx)
                )
                dispatch_result = await adapter.dispatch_alert(
                    channel="email",
                    subject=f"Rule Created: {rule.rule_name}",
                    message=f"Your rule '{rule.rule_name}' has been successfully created and is now {status_value}.",
                    rule=rule,
                    device_id=device_id_display,
                    device_names=devices_display,
                    scope_label=scope_label,
                    alert_type="rule_created"
                )
                sent_count = sum(
                    1
                    for recipient_result in dispatch_result.recipient_results
                    if recipient_result.status in {
                        NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
                        NotificationDeliveryStatus.DELIVERED.value,
                    }
                )
                skipped_count = sum(
                    1
                    for recipient_result in dispatch_result.recipient_results
                    if recipient_result.status == NotificationDeliveryStatus.SKIPPED.value
                )
                if sent_count > 0:
                    logger.info(
                        "Rule creation notification sent",
                        extra={
                            "rule_id": str(rule.rule_id),
                            "rule_name": rule.rule_name,
                            "channels": rule.notification_channels,
                            "sent_count": sent_count,
                        }
                    )
                elif skipped_count > 0:
                    logger.warning(
                        "Rule creation notification skipped",
                        extra={
                            "rule_id": str(rule.rule_id),
                            "rule_name": rule.rule_name,
                            "channels": rule.notification_channels,
                            "skipped_count": skipped_count,
                        }
                    )
                else:
                    logger.error(
                        "Rule creation notification failed",
                        extra={
                            "rule_id": str(rule.rule_id),
                            "rule_name": rule.rule_name,
                            "channels": rule.notification_channels,
                        }
                    )
                await db.commit()
            except Exception as e:
                logger.error(
                    "Failed to send rule creation notification",
                    extra={
                        "rule_id": str(rule.rule_id),
                        "error": str(e)
                    }
                )
        
        return RuleSingleResponse(data=rule)
    except PermissionError as e:
        logger.warning(
            "Rule creation forbidden by plant scope",
            extra={
                "rule_name": rule_data.rule_name,
                "error": str(e),
            }
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_SCOPE_FORBIDDEN",
                    "message": str(e),
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    except DuplicateRuleError as e:
        logger.warning(
            "Duplicate rule creation blocked",
            extra={
                "rule_name": rule_data.rule_name,
                "existing_rule_id": e.existing_rule_id,
            }
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_ALREADY_EXISTS",
                    "message": str(e),
                    "existing_rule_id": e.existing_rule_id,
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    except ValueError as e:
        logger.warning(
            "Rule creation failed",
            extra={
                "rule_name": rule_data.rule_name,
                "error": str(e),
            }
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": str(e),
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )


@router.put(
    "/{rule_id}",
    response_model=RuleSingleResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        404: {"model": ErrorResponse, "description": "Rule not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_rule(
    rule_id: UUID,
    rule_data: RuleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RuleSingleResponse:
    """Update an existing rule."""
    ctx = _tenant_context(request)
    service = RuleService(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    
    try:
        rule = await service.update_rule(rule_id, rule_data, accessible_device_ids=accessible_device_ids)
    except DuplicateRuleError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_ALREADY_EXISTS",
                    "message": str(e),
                    "existing_rule_id": e.existing_rule_id,
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_SCOPE_FORBIDDEN",
                    "message": str(e),
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": str(e),
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_NOT_FOUND",
                    "message": f"Rule with ID '{rule_id}' not found",
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    
    return RuleSingleResponse(data=rule)


@router.patch(
    "/{rule_id}/status",
    response_model=RuleStatusResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        404: {"model": ErrorResponse, "description": "Rule not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_rule_status(
    rule_id: UUID,
    status_update: RuleStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RuleStatusResponse:
    """Update rule status."""
    ctx = _tenant_context(request)
    service = RuleService(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    try:
        rule = await service.update_rule_status(rule_id, status_update.status, accessible_device_ids=accessible_device_ids)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_SCOPE_FORBIDDEN",
                    "message": str(e),
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_NOT_FOUND",
                    "message": f"Rule with ID '{rule_id}' not found",
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    
    status_messages = {
        RuleStatus.ACTIVE: "Rule activated successfully",
        RuleStatus.PAUSED: "Rule paused successfully",
        RuleStatus.ARCHIVED: "Rule archived successfully",
    }
    
    return RuleStatusResponse(
        message=status_messages.get(status_update.status, "Status updated"),
        rule_id=rule_id,
        status=status_update.status,
    )


@router.delete(
    "/{rule_id}",
    response_model=RuleDeleteResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Rule not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_rule(
    rule_id: UUID,
    request: Request,
    soft: bool = Query(True, description="Perform soft delete"),
    db: AsyncSession = Depends(get_db),
) -> RuleDeleteResponse:
    """Delete a rule."""
    ctx = _tenant_context(request)
    service = RuleService(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    try:
        deleted = await service.delete_rule(rule_id, soft=soft, accessible_device_ids=accessible_device_ids)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_SCOPE_FORBIDDEN",
                    "message": str(e),
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "RULE_NOT_FOUND",
                    "message": f"Rule with ID '{rule_id}' not found",
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    
    return RuleDeleteResponse(
        message="Rule deleted successfully" if soft else "Rule permanently deleted",
        rule_id=rule_id,
    )


# ----------------------------------------------------------------------
# ✅ FIXED evaluate endpoint (streaming / data-service entrypoint)
# ----------------------------------------------------------------------

@router.post(
    "/evaluate",
    status_code=200,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def evaluate_rules(
    payload: TelemetryPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Evaluate full telemetry payload against active rules.

    This endpoint is used by the data-service and expects a complete
    telemetry payload (voltage, current, power, temperature, etc).
    """
    ctx = _tenant_context(request)
    evaluator = RuleEvaluator(db, ctx)

    try:
        total, triggered, results = await evaluator.evaluate_telemetry(payload)

        logger.info(
            "Rule evaluation completed",
            extra={
                "device_id": payload.device_id,
                "rules_evaluated": total,
                "rules_triggered": triggered,
            },
        )

        return {
            "rules_evaluated": total,
            "rules_triggered": triggered,
            "results": results,
        }

    except ValueError as e:
        logger.warning(
            "Evaluation failed",
            extra={
                "device_id": payload.device_id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "EVALUATION_ERROR",
                    "message": str(e),
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    except Exception as e:
        logger.error(
            "Unexpected error during rule evaluation",
            extra={
                "device_id": payload.device_id,
                "error": str(e),
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred during evaluation",
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
