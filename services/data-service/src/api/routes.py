"""API routes for REST endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from services.shared.tenant_context import TenantContext, require_tenant
from src.config import settings
from src.models import TelemetryHistoryQueryError, TelemetryPoint, TelemetryQuery
from src.services import TelemetryService
from src.services.websocket_ticket_service import (
    WebSocketTicketServiceError,
    get_websocket_ticket_service,
)
from src.utils import get_logger

logger = get_logger(__name__)

_PLANT_SCOPED_TELEMETRY_ROLES = {"plant_manager", "operator", "viewer"}


# -------------------------
# Dependency (PERMANENT FIX)
# -------------------------

def get_telemetry_service() -> TelemetryService:
    from src.main import app_state

    if app_state.telemetry_service is None:
        raise RuntimeError("TelemetryService not initialized")

    return app_state.telemetry_service


# -------------------------
# Response models
# -------------------------

class ApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    timestamp: str


class TelemetryListResponse(BaseModel):
    items: List[TelemetryPoint]
    total: int
    page: int = 1
    page_size: int = 1000


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
    checks: Dict[str, Any] = Field(default_factory=dict)


class LatestBatchRequest(BaseModel):
    device_ids: List[str] = Field(default_factory=list)


def _resolve_accessible_plant_ids(request: Request) -> list[str] | None:
    ctx = TenantContext.from_request(request)
    if ctx.role not in _PLANT_SCOPED_TELEMETRY_ROLES:
        return None
    return [plant_id for plant_id in ctx.plant_ids if plant_id]


def _internal_error_response() -> dict[str, Any]:
    return {
        "success": False,
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "Internal server error",
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


def _telemetry_history_error_response(exc: TelemetryHistoryQueryError) -> dict[str, Any]:
    return {
        "success": False,
        "error": {
            "code": exc.code,
            "message": exc.message,
            "source": exc.source,
            "retryable": exc.retryable,
            "section": "history",
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


# -------------------------
# Router factory
# -------------------------

def create_router() -> APIRouter:
    router = APIRouter(prefix=settings.api_prefix)

    # -------------------------
    # Health
    # -------------------------

    @router.get(
        "/health",
        response_model=HealthResponse,
        tags=["Health"],
    )
    async def health_check() -> HealthResponse:
        from src.main import app_state

        mqtt_state = "connected"
        reasons: list[str] = []
        if settings.app_role == "api":
            mqtt_connected = bool(app_state.mqtt_handler and app_state.mqtt_handler.is_connected)
            if not mqtt_connected:
                mqtt_state = "disconnected"
                reasons.append("mqtt_disconnected")
        status_value = "healthy" if not reasons else "degraded"
        return HealthResponse(
            status=status_value,
            version=settings.app_version,
            timestamp=datetime.utcnow().isoformat(),
            checks={
                "influxdb": "connected",
                "mqtt": mqtt_state,
                "reasons": reasons,
            },
        )

    # -------------------------
    # Telemetry
    # -------------------------

    @router.get(
        "/telemetry/{device_id}",
        response_model=ApiResponse,
        tags=["Telemetry"],
    )
    async def get_telemetry(
        request: Request,
        device_id: str,
        start_time: Optional[datetime] = Query(None),
        end_time: Optional[datetime] = Query(None),
        fields: Optional[str] = Query(None),
        aggregate: Optional[str] = Query(None),
        interval: Optional[str] = Query(None),
        limit: int = Query(default=1000, ge=1, le=10000),
        telemetry_service: TelemetryService = Depends(get_telemetry_service),
    ) -> ApiResponse:
        try:
            tenant_id = require_tenant(request)
            if (
                start_time is not None
                and end_time is not None
                and fields is None
                and aggregate is None
                and interval is None
                and limit <= 10
                and (end_time - start_time).total_seconds() > 72 * 3600
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "success": False,
                        "error": {
                            "code": "QUERY_WINDOW_TOO_WIDE",
                            "message": "For tiny limits (<=10), keep query window <=72h or use /telemetry/{device_id}/latest.",
                        },
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )

            field_list = fields.split(",") if fields else None

            points = await telemetry_service.get_telemetry(
                tenant_id=tenant_id,
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
                fields=field_list,
                aggregate=aggregate,
                interval=interval,
                limit=limit,
                accessible_plant_ids=_resolve_accessible_plant_ids(request),
            )

            return ApiResponse(
                success=True,
                data={
                    "items": [p.to_api_dict() for p in points],
                    "total": len(points),
                    "device_id": device_id,
                },
                timestamp=datetime.utcnow().isoformat(),
            )

        except HTTPException:
            raise
        except TelemetryHistoryQueryError as exc:
            logger.warning(
                "Telemetry history unavailable",
                extra={"device_id": device_id, "tenant_id": request.state.tenant_context.tenant_id, "code": exc.code},
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=_telemetry_history_error_response(exc),
            )
        except Exception:
            logger.exception("Failed to get telemetry", extra={"device_id": device_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=_internal_error_response(),
            )

    @router.get(
        "/telemetry/{device_id}/latest",
        response_model=ApiResponse,
        tags=["Telemetry"],
    )
    async def get_latest_telemetry(
        request: Request,
        device_id: str,
        telemetry_service: TelemetryService = Depends(get_telemetry_service),
    ) -> ApiResponse:
        try:
            tenant_id = require_tenant(request)
            point = await telemetry_service.get_latest(
                tenant_id=tenant_id,
                device_id=device_id,
                accessible_plant_ids=_resolve_accessible_plant_ids(request),
            )
            return ApiResponse(
                success=True,
                data={
                    "item": point.to_api_dict() if point is not None else None,
                    "device_id": device_id,
                },
                timestamp=datetime.utcnow().isoformat(),
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to get latest telemetry", extra={"device_id": device_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=_internal_error_response(),
            )

    @router.get(
        "/telemetry/{device_id}/earliest",
        response_model=ApiResponse,
        tags=["Telemetry"],
    )
    async def get_earliest_telemetry(
        request: Request,
        device_id: str,
        start_time: Optional[datetime] = Query(None),
        telemetry_service: TelemetryService = Depends(get_telemetry_service),
    ) -> ApiResponse:
        try:
            tenant_id = require_tenant(request)
            point = await telemetry_service.get_earliest(
                tenant_id=tenant_id,
                device_id=device_id,
                start_time=start_time,
                accessible_plant_ids=_resolve_accessible_plant_ids(request),
            )
            return ApiResponse(
                success=True,
                data={
                    "item": point.to_api_dict() if point is not None else None,
                    "device_id": device_id,
                },
                timestamp=datetime.utcnow().isoformat(),
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to get earliest telemetry", extra={"device_id": device_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=_internal_error_response(),
            )

    @router.post(
        "/telemetry/{device_id}/ws-ticket",
        response_model=ApiResponse,
        tags=["Telemetry"],
    )
    async def issue_websocket_ticket(
        request: Request,
        device_id: str,
        telemetry_service: TelemetryService = Depends(get_telemetry_service),
    ) -> ApiResponse:
        try:
            tenant_id = require_tenant(request)
            ctx = TenantContext.from_request(request)
            await telemetry_service.assert_device_access(
                tenant_id=tenant_id,
                device_id=device_id,
                accessible_plant_ids=_resolve_accessible_plant_ids(request),
            )
            issued = await get_websocket_ticket_service().issue_ticket(
                user_id=ctx.user_id,
                role=ctx.role,
                tenant_id=tenant_id,
                device_id=device_id,
            )
            return ApiResponse(
                success=True,
                data=issued,
                timestamp=datetime.utcnow().isoformat(),
            )
        except HTTPException:
            raise
        except WebSocketTicketServiceError as exc:
            logger.warning("Failed to issue WebSocket ticket", extra={"device_id": device_id, "error": str(exc)})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "success": False,
                    "error": {
                        "code": "WEBSOCKET_TICKET_UNAVAILABLE",
                        "message": "Live telemetry is temporarily unavailable.",
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                },
            ) from exc
        except Exception:
            logger.exception("Failed to issue WebSocket ticket", extra={"device_id": device_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=_internal_error_response(),
            )

    @router.post(
        "/telemetry/latest-batch",
        response_model=ApiResponse,
        tags=["Telemetry"],
    )
    async def get_latest_telemetry_batch(
        request: Request,
        body: LatestBatchRequest,
        telemetry_service: TelemetryService = Depends(get_telemetry_service),
    ) -> ApiResponse:
        try:
            tenant_id = require_tenant(request)
            device_ids = [d for d in body.device_ids if d]
            latest = await telemetry_service.get_latest_batch(
                tenant_id=tenant_id,
                device_ids=device_ids,
                accessible_plant_ids=_resolve_accessible_plant_ids(request),
            )
            return ApiResponse(
                success=True,
                data={
                    "items": {
                        device_id: (point.to_api_dict() if point is not None else None)
                        for device_id, point in latest.items()
                    },
                    "total": len(device_ids),
                },
                timestamp=datetime.utcnow().isoformat(),
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to get latest telemetry batch")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=_internal_error_response(),
            )

    # -------------------------
    # Stats
    # -------------------------

    @router.get(
        "/stats/{device_id}",
        response_model=ApiResponse,
        tags=["Telemetry"],
    )
    async def get_stats(
        request: Request,
        device_id: str,
        start_time: Optional[datetime] = Query(None),
        end_time: Optional[datetime] = Query(None),
        telemetry_service: TelemetryService = Depends(get_telemetry_service),
    ) -> ApiResponse:
        try:
            tenant_id = require_tenant(request)
            stats = await telemetry_service.get_stats(
                tenant_id=tenant_id,
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
                accessible_plant_ids=_resolve_accessible_plant_ids(request),
            )

            if stats is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "success": False,
                        "error": {
                            "code": "NO_DATA",
                            "message": f"No data found for device {device_id}",
                        },
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )

            return ApiResponse(
                success=True,
                data=stats if isinstance(stats, dict) else stats.model_dump(),
                timestamp=datetime.utcnow().isoformat(),
            )

        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to get stats", extra={"device_id": device_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=_internal_error_response(),
            )

    # -------------------------
    # Custom query
    # -------------------------

    @router.post(
        "/query",
        response_model=ApiResponse,
        tags=["Telemetry"],
    )
    async def custom_query(
        request: Request,
        query: TelemetryQuery,
        telemetry_service: TelemetryService = Depends(get_telemetry_service),
    ) -> ApiResponse:
        try:
            tenant_id = require_tenant(request)
            points = await telemetry_service.get_telemetry(
                tenant_id=tenant_id,
                device_id=query.device_id,
                start_time=query.start_time,
                end_time=query.end_time,
                fields=query.fields,
                aggregate=query.aggregate,
                interval=query.interval,
                limit=query.limit,
                accessible_plant_ids=_resolve_accessible_plant_ids(request),
            )

            return ApiResponse(
                success=True,
                data={
                    "items": [p.to_api_dict() for p in points],
                    "total": len(points),
                },
                timestamp=datetime.utcnow().isoformat(),
            )

        except HTTPException:
            raise
        except Exception:
            logger.exception("Custom query failed", extra={"device_id": query.device_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=_internal_error_response(),
            )

    return router
