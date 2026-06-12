"""Client for synchronous device live projection updates."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from src.config import settings
from src.models import TelemetryPayload
from src.utils import get_logger
from src.utils.circuit_breaker import get_or_create_circuit_breaker
from services.shared.tenant_context import build_internal_headers

logger = get_logger(__name__)


class DeviceProjectionSyncError(Exception):
    """Raised when live projection sync fails."""

    def __init__(
        self,
        message: str,
        retryable: bool = True,
        *,
        code: str | None = None,
        category: str = "unexpected_internal_error",
    ):
        super().__init__(message)
        self.retryable = retryable
        self.code = code or message
        self.category = category


class DeviceProjectionClient:
    """Synchronously updates device-service live projection for accepted telemetry."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.base_url = (base_url or settings.device_service_url).rstrip("/")
        self.timeout = timeout or settings.device_service_timeout
        self._transport_retries = max(1, int(settings.device_projection_transport_retries))
        self._retry_backoff_base = max(0.05, float(settings.device_projection_retry_backoff_base_seconds))
        self._retry_backoff_max = max(self._retry_backoff_base, float(settings.device_projection_retry_backoff_max_seconds))
        self._request_semaphore = asyncio.Semaphore(max(1, int(settings.device_projection_max_inflight_requests)))
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=min(2.0, self.timeout),
                read=self.timeout,
                write=min(2.0, self.timeout),
                pool=max(0.1, float(settings.device_projection_http_pool_timeout_seconds)),
            ),
            limits=httpx.Limits(
                max_keepalive_connections=max(1, int(settings.device_projection_http_max_keepalive_connections)),
                max_connections=max(1, int(settings.device_projection_http_max_connections)),
            ),
        )
        self.circuit_breaker = get_or_create_circuit_breaker(
            "device-service-live-update",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            success_threshold=settings.circuit_breaker_success_threshold,
            open_timeout_sec=settings.circuit_breaker_open_timeout_sec,
        )

    async def sync_projection(self, payload: TelemetryPayload) -> dict[str, Any]:
        result = (await self.sync_projection_batch([payload]))[0]
        if not result.get("success"):
            error = str(result.get("error") or "device_projection_invalid_response")
            retryable = bool(result.get("retryable", True))
            raise DeviceProjectionSyncError(error, retryable=retryable)
        device_payload = result.get("device")
        if not isinstance(device_payload, dict):
            raise DeviceProjectionSyncError("device_projection_invalid_response", retryable=True)
        return device_payload

    async def sync_projection_batch(self, payloads: list[TelemetryPayload]) -> list[dict[str, Any]]:
        if not payloads:
            return []
        tenant_ids = {self._resolve_tenant_id(payload) for payload in payloads}
        if len(tenant_ids) != 1:
            raise DeviceProjectionSyncError(
                "device_projection_batch_cross_tenant",
                retryable=False,
                code="DEVICE_PROJECTION_BATCH_CROSS_TENANT",
                category="invalid_device_metadata",
            )
        tenant_id = next(iter(tenant_ids))
        request_body = {
            "tenant_id": tenant_id,
            "updates": [
                {
                    "device_id": payload.device_id,
                    "telemetry": payload.model_dump(mode="json"),
                    "dynamic_fields": payload.get_dynamic_fields(),
                }
                for payload in payloads
            ],
        }
        acquired = await self.circuit_breaker.try_acquire()
        if acquired is None:
            raise DeviceProjectionSyncError(
                "device_projection_circuit_open",
                retryable=True,
                code="DEVICE_PROJECTION_CIRCUIT_OPEN",
                category="downstream_overload",
            )

        try:
            response = await self._send_projection_batch_request(tenant_id, request_body)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            await self.circuit_breaker.record_failure(acquired_half_open_slot=bool(acquired))
            detail = str(exc).strip() or exc.__class__.__name__
            raise DeviceProjectionSyncError(
                f"device_projection_transport_error:{detail}",
                retryable=True,
                code="DEVICE_PROJECTION_TRANSPORT_ERROR",
                category="transient_dependency_failure",
            ) from exc

        if response.status_code in {408, 429, 503, 504}:
            await self.circuit_breaker.record_failure(acquired_half_open_slot=bool(acquired))
            raise DeviceProjectionSyncError(
                f"device_projection_overloaded:{response.status_code}",
                retryable=True,
                code="DEVICE_PROJECTION_OVERLOADED",
                category="downstream_overload",
            )

        if response.status_code >= 500:
            await self.circuit_breaker.record_failure(acquired_half_open_slot=bool(acquired))
            raise DeviceProjectionSyncError(
                f"device_projection_server_error:{response.status_code}",
                retryable=True,
                code="DEVICE_PROJECTION_SERVER_ERROR",
                category="transient_dependency_failure",
            )

        await self.circuit_breaker.record_success(acquired_half_open_slot=bool(acquired))

        if response.status_code >= 400:
            raise DeviceProjectionSyncError(
                f"device_projection_client_error:{response.status_code}",
                retryable=False,
                code="DEVICE_PROJECTION_CLIENT_ERROR",
                category="invalid_device_metadata" if response.status_code == 422 else "unexpected_internal_error",
            )

        payload_json = response.json()
        results = payload_json.get("results") if isinstance(payload_json, dict) else None
        if not isinstance(results, list) or len(results) != len(payloads):
            raise DeviceProjectionSyncError(
                "device_projection_invalid_batch_response",
                retryable=True,
                code="DEVICE_PROJECTION_INVALID_BATCH_RESPONSE",
                category="unexpected_internal_error",
            )
        return [
            dict(item)
            if isinstance(item, dict)
            else {
                "success": False,
                "error": "device_projection_invalid_batch_item",
                "error_code": "DEVICE_PROJECTION_INVALID_BATCH_ITEM",
                "retryable": True,
            }
            for item in results
        ]

    async def _send_projection_batch_request(
        self,
        tenant_id: str,
        request_body: dict[str, Any],
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._request_semaphore:
                    return await self.client.post(
                        f"{self.base_url}/api/v1/devices/live-update/batch",
                        json=request_body,
                        headers=build_internal_headers("data-service", tenant_id),
                    )
            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.RemoteProtocolError,
                asyncio.TimeoutError,
            ):
                if attempt >= self._transport_retries:
                    raise
                backoff = min(
                    self._retry_backoff_max,
                    self._retry_backoff_base * (2 ** (attempt - 1)),
                )
                await asyncio.sleep(backoff)

    @staticmethod
    def _resolve_tenant_id(payload: TelemetryPayload) -> str:
        payload_tenant_id = DeviceProjectionClient._normalize_tenant_id(payload.tenant_id)
        metadata_tenant_id = DeviceProjectionClient._normalize_tenant_id(
            None if payload.device_metadata is None else payload.device_metadata.tenant_id
        )

        if payload_tenant_id and metadata_tenant_id and payload_tenant_id != metadata_tenant_id:
            raise DeviceProjectionSyncError(
                "Telemetry tenant scope does not match device metadata tenant.",
                retryable=False,
            )

        tenant_id = payload_tenant_id or metadata_tenant_id
        if tenant_id is None:
            raise DeviceProjectionSyncError(
                "Telemetry tenant scope is required for projection sync.",
                retryable=False,
            )
        return tenant_id

    @staticmethod
    def _normalize_tenant_id(value: object | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    async def close(self) -> None:
        await self.client.aclose()
        logger.info("DeviceProjectionClient HTTP client closed")
