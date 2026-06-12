"""Device metadata enrichment service."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.models import DeviceMetadata, EnrichmentStatus, TelemetryPayload
from src.utils.circuit_breaker import get_or_create_circuit_breaker
from src.utils import get_logger
from services.shared.tenant_context import build_internal_headers

logger = get_logger(__name__)
_MYSQL_ENGINE = None
_MYSQL_SESSION_FACTORY: async_sessionmaker | None = None


def _get_mysql_session_factory() -> async_sessionmaker:
    global _MYSQL_ENGINE, _MYSQL_SESSION_FACTORY
    if _MYSQL_SESSION_FACTORY is None:
        _MYSQL_ENGINE = create_async_engine(
            settings.mysql_async_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=settings.db_pool_recycle,
            pool_timeout=settings.db_pool_timeout,
            future=True,
        )
        _MYSQL_SESSION_FACTORY = async_sessionmaker(
            _MYSQL_ENGINE,
            expire_on_commit=False,
            autoflush=False,
        )
    return _MYSQL_SESSION_FACTORY


class EnrichmentServiceError(Exception):
    """Raised when enrichment service encounters an error."""
    pass


class EnrichmentService:
    """
    Service for enriching telemetry with device metadata.

    Features:
    - Non-blocking enrichment with async HTTP calls
    - Configurable retries with exponential backoff
    - Timeout handling
    - Enrichment status tracking
    """

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None):
        """
        Initialize enrichment service.

        Args:
            base_url: Device service base URL
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or settings.device_service_url
        self.timeout = timeout or settings.device_service_timeout
        self.max_retries = settings.device_service_max_retries
        self._metadata_cache_ttl = timedelta(seconds=60)
        self._metadata_cache: dict[tuple[str, str], tuple[datetime, DeviceMetadata]] = {}

        # Create async HTTP client
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        self.circuit_breaker = get_or_create_circuit_breaker(
            "device-service",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            success_threshold=settings.circuit_breaker_success_threshold,
            open_timeout_sec=settings.circuit_breaker_open_timeout_sec,
        )

        logger.info(
            "EnrichmentService initialized",
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

    async def enrich_telemetry(
        self,
        payload: TelemetryPayload,
    ) -> TelemetryPayload:
        """
        Enrich telemetry payload with device metadata.

        This method performs non-blocking enrichment. If the device service
        is unavailable or times out, the payload is marked with the
        appropriate enrichment_status and returned.

        Args:
            payload: Telemetry payload to enrich

        Returns:
            Enriched payload with metadata and status
        """
        try:
            if payload.tenant_id is None:
                payload.tenant_id = await self._resolve_tenant_id_from_catalog(payload.device_id)

            device_metadata = await self._get_cached_device_metadata(payload.device_id, payload.tenant_id)

            payload.device_metadata = device_metadata
            if payload.tenant_id is None and getattr(device_metadata, "tenant_id", None):
                payload.tenant_id = str(device_metadata.tenant_id)
            payload.enrichment_status = EnrichmentStatus.SUCCESS
            payload.enriched_at = datetime.utcnow()

            logger.debug(
                "Telemetry enriched successfully",
                device_id=payload.device_id,
                device_name=device_metadata.name,
                device_type=device_metadata.type,
            )

        except asyncio.TimeoutError:
            logger.warning(
                "Enrichment timeout",
                device_id=payload.device_id,
                timeout=self.timeout,
            )
            payload.enrichment_status = EnrichmentStatus.TIMEOUT

        except Exception as e:
            logger.error(
                "Enrichment failed",
                device_id=payload.device_id,
                error=str(e),
            )
            payload.enrichment_status = EnrichmentStatus.FAILED

        return payload

    async def _get_cached_device_metadata(
        self,
        device_id: str,
        tenant_id: Optional[str],
    ) -> DeviceMetadata:
        cache_key = (str(tenant_id or "").strip(), device_id)
        now = datetime.now(timezone.utc)
        cached = self._metadata_cache.get(cache_key)
        if cached is not None:
            expires_at, metadata = cached
            if now < expires_at:
                return metadata

        metadata = await self._fetch_device_metadata(device_id, tenant_id)
        self._metadata_cache[cache_key] = (now + self._metadata_cache_ttl, metadata)
        return metadata

    async def _resolve_tenant_id_from_catalog(self, device_id: str) -> Optional[str]:
        """Resolve tenant_id directly from the shared MySQL device catalog.

        Legacy MQTT topics do not carry tenant scope, so we need a safe fallback
        before calling device-service. If the same device_id exists in multiple
        tenants, treat it as ambiguous and return None rather than guessing.
        """
        session_factory = _get_mysql_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT DISTINCT tenant_id
                    FROM devices
                    WHERE device_id = :device_id
                      AND deleted_at IS NULL
                      AND tenant_id IS NOT NULL
                    ORDER BY tenant_id ASC
                    """
                ),
                {"device_id": device_id},
            )
            tenant_ids = [str(row[0]).strip() for row in result.all() if row[0] is not None]

        unique_tenant_ids = [tenant_id for tenant_id in dict.fromkeys(tenant_ids) if tenant_id]
        if len(unique_tenant_ids) == 1:
            return unique_tenant_ids[0]
        if len(unique_tenant_ids) > 1:
            logger.warning(
                "Ambiguous tenant resolution for telemetry device",
                device_id=device_id,
                tenant_ids=unique_tenant_ids,
            )
        return None

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _fetch_device_metadata(self, device_id: str, tenant_id: Optional[str] = None) -> DeviceMetadata:
        """
        Fetch device metadata from device service.

        Args:
            device_id: Device identifier

        Returns:
            Device metadata
        """
        url = f"{self.base_url}/api/v1/devices/{device_id}"

        try:
            async def _request():
                params = {"tenant_id": tenant_id} if tenant_id else None
                response = await self.client.get(
                    url,
                    params=params,
                    headers=build_internal_headers("data-service", tenant_id),
                )
                if response.status_code >= 500:
                    response.raise_for_status()
                return response

            success, response = await self.circuit_breaker.call(_request)
            if not success or response is None:
                raise EnrichmentServiceError("Device service circuit open or request failed")
            if response.status_code >= 400:
                response.raise_for_status()

            data = response.json()

            # Handle nested response structure
            if "data" in data:
                device_data = data["data"]
            else:
                device_data = data

            # ---- FIX: map device-service fields to DeviceMetadata ----
            # Note: device-service now returns legacy_status instead of status
            # Use legacy_status for backward compatibility, or runtime_status for dynamic status
            metadata = DeviceMetadata(
                id=device_data["device_id"],
                tenant_id=device_data.get("tenant_id"),
                name=device_data["device_name"],
                type=device_data["device_type"],
                location=device_data.get("location"),
                status=device_data.get("legacy_status", "active"),
                metadata={
                    "manufacturer": device_data.get("manufacturer"),
                    "model": device_data.get("model"),
                    "runtime_status": device_data.get("runtime_status"),
                    "last_seen_timestamp": device_data.get("last_seen_timestamp"),
                },
            )

            logger.debug(
                "Device metadata fetched",
                device_id=device_id,
                device_name=metadata.name,
            )

            return metadata

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(
                    "Device not found",
                    device_id=device_id,
                    url=url,
                )

                return DeviceMetadata(
                    id=device_id,
                    name=f"Unknown Device ({device_id})",
                    type="unknown",
                    status="unknown",
                )

            raise EnrichmentServiceError(
                f"HTTP error: {e.response.status_code}"
            ) from e

        except httpx.RequestError as e:
            raise EnrichmentServiceError(f"Request error: {e}") from e

    async def health_check(self) -> bool:
        """
        Check if device service is healthy.

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            async def _request():
                response = await self.client.get(
                    f"{self.base_url}/health",
                    timeout=5.0,
                    headers=build_internal_headers("data-service"),
                )
                if response.status_code >= 500:
                    response.raise_for_status()
                return response

            success, response = await self.circuit_breaker.call(_request)
            if not success or response is None:
                return False
            return response.status_code == 200
        except Exception as e:
            logger.warning(
                "Device service health check failed",
                error=str(e),
            )
            return False

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()
        logger.info("EnrichmentService HTTP client closed")
