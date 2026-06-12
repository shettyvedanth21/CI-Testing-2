"""WebSocket endpoint for live telemetry."""

import asyncio
import json
from datetime import datetime
from typing import Dict, Set

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy import text

from src.config import settings
from src.services.websocket_ticket_service import get_websocket_ticket_service
from src.utils import get_logger
from shared.auth_middleware import (
    _JWT_ALG,
    _JWT_SECRET,
    _assert_token_not_revoked,
    _load_current_auth_state,
    normalize_tenant_id,
)

logger = get_logger(__name__)


class _WsAuthError(Exception):
    def __init__(self, code: int, reason: str):
        self.code = code
        self.reason = reason


async def _validate_ws_ticket(websocket: WebSocket, device_id: str) -> dict:
    ticket = str(websocket.query_params.get("ticket") or "").strip()
    if not ticket:
        raise _WsAuthError(4401, "Authentication required")

    payload = await get_websocket_ticket_service().consume_ticket(ticket)
    if payload is None:
        raise _WsAuthError(4401, "Invalid or expired ticket")

    ticket_device_id = str(payload.get("device_id") or "").strip()
    ticket_tenant_id = normalize_tenant_id(payload.get("tenant_id"))
    if ticket_device_id != device_id or not ticket_tenant_id:
        raise _WsAuthError(4403, "Ticket scope invalid")

    return {
        "user_id": str(payload.get("user_id") or "").strip(),
        "role": str(payload.get("role") or "").strip(),
        "tenant_id": ticket_tenant_id,
    }


async def _validate_ws_auth(websocket: WebSocket, device_id: str) -> dict:
    if websocket.query_params.get("ticket"):
        return await _validate_ws_ticket(websocket, device_id)

    token = websocket.query_params.get("token")
    if not token:
        raise _WsAuthError(4401, "Authentication required")

    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALG])
    except JWTError:
        raise _WsAuthError(4401, "Invalid token")

    if payload.get("type") != "access":
        raise _WsAuthError(4401, "Invalid token type")

    try:
        _assert_token_not_revoked(payload)
    except Exception:
        raise _WsAuthError(4401, "Token revoked")

    try:
        auth_state = await _load_current_auth_state(payload)
    except Exception:
        raise _WsAuthError(4401, "Auth state unavailable")

    role = auth_state.get("role")
    if not role:
        raise _WsAuthError(4401, "Invalid auth state")

    if role == "super_admin":
        tenant_id = normalize_tenant_id(websocket.query_params.get("tenant_id"))
        if not tenant_id:
            raise _WsAuthError(4403, "Tenant selection required")
    else:
        tenant_id = normalize_tenant_id(auth_state.get("tenant_id"))
        if not tenant_id:
            raise _WsAuthError(4403, "Tenant scope required")

    if not await _device_belongs_to_tenant(device_id, tenant_id):
        raise _WsAuthError(4403, "Device not found in tenant scope")

    return {
        "user_id": auth_state.get("user_id"),
        "role": role,
        "tenant_id": tenant_id,
    }


async def _device_belongs_to_tenant(device_id: str, tenant_id: str) -> bool:
    from src.services.enrichment_service import _get_mysql_session_factory

    session_factory = _get_mysql_session_factory()
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT 1
                    FROM devices
                    WHERE device_id = :device_id
                      AND tenant_id = :tenant_id
                      AND deleted_at IS NULL
                    LIMIT 1
                    """
                ),
                {"device_id": device_id, "tenant_id": tenant_id},
            )
            return result.first() is not None
    except Exception:
        logger.warning(
            "WebSocket device-ownership check failed",
            device_id=device_id,
            tenant_id=tenant_id,
        )
        return False


class ConnectionManager:
    """
    Manage WebSocket connections for live telemetry.
    
    Features:
    - Device-specific subscription management
    - Connection limiting
    - Heartbeat/ping support
    - Broadcast capability
    """
    
    def __init__(self):
        """Initialize connection manager."""
        self._device_connections: Dict[str, Set[WebSocket]] = {}
        self._connection_devices: Dict[WebSocket, str] = {}
        self._connection_tenants: Dict[WebSocket, str] = {}
        self._total_connections = 0
        
        logger.info("ConnectionManager initialized")
    
    async def connect(self, websocket: WebSocket, device_id: str, tenant_id: str) -> bool:
        """
        Accept and track a new WebSocket connection.
        
        Args:
            websocket: WebSocket connection
            device_id: Device to subscribe to
            tenant_id: Authenticated tenant context for the connection
        """
        if self._total_connections >= settings.ws_max_connections:
            logger.warning(
                "Max WebSocket connections reached",
                max_connections=settings.ws_max_connections,
            )
            return False
        
        await websocket.accept()
        
        if device_id not in self._device_connections:
            self._device_connections[device_id] = set()
        
        self._device_connections[device_id].add(websocket)
        self._connection_devices[websocket] = device_id
        self._connection_tenants[websocket] = tenant_id
        self._total_connections += 1
        
        logger.info(
            "WebSocket connected",
            device_id=device_id,
            tenant_id=tenant_id,
            total_connections=self._total_connections,
        )
        
        return True
    
    def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove and clean up a WebSocket connection.
        
        Args:
            websocket: WebSocket connection to remove
        """
        device_id = self._connection_devices.get(websocket)
        
        if device_id:
            if device_id in self._device_connections:
                self._device_connections[device_id].discard(websocket)
                
                if not self._device_connections[device_id]:
                    del self._device_connections[device_id]
            
            del self._connection_devices[websocket]
            self._connection_tenants.pop(websocket, None)
            self._total_connections -= 1
            
            logger.info(
                "WebSocket disconnected",
                device_id=device_id,
                total_connections=self._total_connections,
            )
    
    async def send_telemetry(
        self,
        device_id: str,
        telemetry_data: Dict,
    ) -> None:
        """
        Send telemetry data to all subscribers of a device.
        
        Args:
            device_id: Device identifier
            telemetry_data: Telemetry data to send
        """
        if device_id not in self._device_connections:
            return
        
        # Prepare message
        message = {
            "type": "telemetry",
            "device_id": device_id,
            "timestamp": datetime.utcnow().isoformat(),
            "data": telemetry_data,
        }
        
        # Send to all subscribers
        disconnected = []
        for websocket in self._device_connections[device_id]:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.warning(
                    "Failed to send to WebSocket",
                    device_id=device_id,
                    error=str(e),
                )
                disconnected.append(websocket)
        
        # Clean up disconnected clients
        for websocket in disconnected:
            self.disconnect(websocket)
    
    async def send_heartbeat(self, websocket: WebSocket) -> None:
        """
        Send heartbeat message to a WebSocket.
        
        Args:
            websocket: WebSocket connection
        """
        try:
            await websocket.send_json({
                "type": "heartbeat",
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            logger.warning(
                "Failed to send heartbeat",
                error=str(e),
            )
    
    def get_subscriber_count(self, device_id: str) -> int:
        """
        Get number of subscribers for a device.
        
        Args:
            device_id: Device identifier
            
        Returns:
            Number of subscribers
        """
        return len(self._device_connections.get(device_id, set()))
    
    @property
    def total_connections(self) -> int:
        """Get total number of active connections."""
        return self._total_connections


# Global connection manager instance
connection_manager = ConnectionManager()


def create_websocket_router() -> APIRouter:
    """
    Create WebSocket router.
    
    Returns:
        Configured API router
    """
    router = APIRouter()
    
    @router.websocket("/ws/telemetry/{device_id}")
    async def telemetry_websocket(websocket: WebSocket, device_id: str):
        """
        WebSocket endpoint for live telemetry.

        Preferred auth path uses a short-lived single-use ``ticket`` query parameter
        issued by the authenticated REST API. Legacy ``token`` query auth remains as
        a compatibility fallback for older clients during rollout.
        """
        try:
            auth = await _validate_ws_auth(websocket, device_id)
        except _WsAuthError as exc:
            await websocket.close(code=exc.code, reason=exc.reason)
            return

        tenant_id = auth["tenant_id"]

        connected = await connection_manager.connect(websocket, device_id, tenant_id=tenant_id)
        
        if not connected:
            await websocket.close(code=1008, reason="Max connections reached")
            return
        
        try:
            # Send initial connection confirmation
            await websocket.send_json({
                "type": "connected",
                "device_id": device_id,
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            # Keep connection alive and handle messages
            while True:
                try:
                    # Wait for message with timeout (for heartbeat)
                    message = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=settings.ws_heartbeat_interval,
                    )
                    
                    # Handle client messages
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type")
                        
                        if msg_type == "ping":
                            await websocket.send_json({
                                "type": "pong",
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                        elif msg_type == "subscribe":
                            # Client can request subscription confirmation
                            await websocket.send_json({
                                "type": "subscribed",
                                "device_id": device_id,
                            })
                        else:
                            logger.debug(
                                "Unknown WebSocket message type",
                                type=msg_type,
                                device_id=device_id,
                            )
                            
                    except json.JSONDecodeError:
                        logger.warning(
                            "Invalid JSON received on WebSocket",
                            device_id=device_id,
                        )
                        
                except asyncio.TimeoutError:
                    # Send heartbeat
                    await connection_manager.send_heartbeat(websocket)
                    
        except WebSocketDisconnect:
            logger.info(
                "WebSocket disconnected",
                device_id=device_id,
            )
        except Exception as e:
            logger.error(
                "WebSocket error",
                device_id=device_id,
                error=str(e),
            )
        finally:
            connection_manager.disconnect(websocket)
    
    @router.get("/ws/stats")
    async def websocket_stats(request: Request):
        """
        Get WebSocket connection statistics for the caller's tenant scope.
        """
        tenant_id = normalize_tenant_id(
            request.headers.get("X-Tenant-Id")
            or request.headers.get("X-Target-Tenant-Id")
        )
        if tenant_id:
            scoped_devices = {
                did: count
                for did, conns in connection_manager._device_connections.items()
                for count in [len(conns)]
                if any(
                    connection_manager._connection_tenants.get(ws) == tenant_id
                    for ws in conns
                )
            }
        else:
            scoped_devices = {}

        return {
            "total_connections": connection_manager.total_connections,
            "device_subscriptions": scoped_devices,
            "max_connections": settings.ws_max_connections,
        }
    
    return router


async def broadcast_telemetry(
    device_id: str,
    telemetry_data: Dict,
) -> None:
    """
    Broadcast telemetry to all subscribers.
    
    This function can be called from the telemetry service
    to broadcast new telemetry data to connected WebSockets.
    
    Args:
        device_id: Device identifier
        telemetry_data: Telemetry data to broadcast
    """
    await connection_manager.send_telemetry(device_id, telemetry_data)
