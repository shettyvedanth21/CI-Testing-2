"""Rule Engine client for asynchronous rule evaluation."""

import asyncio
from typing import Any, Dict, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.models import TelemetryPayload
from src.repositories import DLQRepository
from services.shared.tenant_context import build_internal_headers
from src.utils.circuit_breaker import get_or_create_circuit_breaker
from src.utils import get_logger

logger = get_logger(__name__)


class RuleEngineError(Exception):
    """Raised when rule engine call fails."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class RuleEngineClient:
    """
    Client for asynchronous Rule Engine service calls.
    
    Features:
    - Non-blocking rule evaluation
    - Configurable retries with exponential backoff
    - Circuit breaker pattern support
    - Timeout handling
    - Dynamic field support
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        dlq_repository: DLQRepository | None = None,
    ):
        self.base_url = base_url or settings.rule_engine_url
        self.timeout = timeout or settings.rule_engine_timeout
        self.max_retries = max_retries or settings.rule_engine_max_retries
        self.retry_delay = retry_delay or settings.rule_engine_retry_delay
        self.dlq_repository = dlq_repository
        
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        self.circuit_breaker = get_or_create_circuit_breaker(
            "rule-engine-service",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            success_threshold=settings.circuit_breaker_success_threshold,
            open_timeout_sec=settings.circuit_breaker_open_timeout_sec,
        )
        
        logger.info(
            "RuleEngineClient initialized",
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
    
    async def evaluate_rules(
        self,
        payload: TelemetryPayload,
        projection_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Asynchronously evaluate rules for telemetry payload."""
        try:
            tenant_id = self._resolve_tenant_id(payload)
            request_data = self._build_request_data(payload, projection_state=projection_state)
            success, response = await self.circuit_breaker.call(
                lambda: self._send_evaluation_request(payload, request_data, tenant_id)
            )
            if not success or response is None:
                if self.circuit_breaker.get_state() == "OPEN":
                    logger.warning(
                        "Circuit breaker open, skipping rule evaluation",
                        device_id=payload.device_id,
                    )
                    await self._write_rule_dlq(payload=payload, request_data=request_data, error_message="rule_engine_circuit_open")
                return

            if 400 <= response.status_code < 500:
                raise RuleEngineError(f"HTTP client error: {response.status_code}", retryable=False)

        except RuleEngineError as e:
            if not e.retryable:
                logger.warning(
                    "Non-retryable rule evaluation error",
                    device_id=payload.device_id,
                    error=str(e),
                )
                await self._write_rule_dlq(
                    payload=payload,
                    request_data=self._build_request_data(payload, projection_state=projection_state),
                    error_message=f"non_retryable:{e}",
                )
                return

            logger.warning(
                "Rule evaluation failed (retryable)",
                device_id=payload.device_id,
                error=str(e),
                retry_count=self.max_retries,
            )
            await self._write_rule_dlq(
                payload=payload,
                request_data=self._build_request_data(payload, projection_state=projection_state),
                error_message=f"retryable:{e}",
            )

        except Exception as e:
            logger.error(
                "Rule evaluation failed",
                device_id=payload.device_id,
                error=str(e),
                retry_count=self.max_retries,
            )
            await self._write_rule_dlq(
                payload=payload,
                request_data=self._build_request_data(payload, projection_state=projection_state),
                error_message=f"unexpected:{e}",
            )
    
    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _send_evaluation_request(
        self,
        payload: TelemetryPayload,
        request_data: Dict[str, Any],
        tenant_id: str,
    ) -> httpx.Response:
        """Send rule evaluation request to rule engine."""
        url = f"{self.base_url}/api/v1/rules/evaluate"

        try:
            response = await self.client.post(
                url,
                json=request_data,
                headers=build_internal_headers("data-service", tenant_id),
            )
            if response.status_code >= 500:
                response.raise_for_status()
            
            logger.debug(
                "Rule evaluation request sent",
                device_id=payload.device_id,
                status_code=response.status_code,
            )
            return response

        except httpx.HTTPStatusError as e:
            raise RuleEngineError(f"HTTP server error: {e.response.status_code}", retryable=True) from e
        except httpx.RequestError as e:
            raise RuleEngineError(f"Request error: {e}", retryable=True) from e

    def _resolve_tenant_id(self, payload: TelemetryPayload) -> str:
        payload_tenant_id = self._normalize_tenant_id(payload.tenant_id)
        metadata_tenant_id = self._normalize_tenant_id(
            None if payload.device_metadata is None else payload.device_metadata.tenant_id
        )

        if payload_tenant_id and metadata_tenant_id and payload_tenant_id != metadata_tenant_id:
            raise RuleEngineError(
                "Telemetry tenant scope does not match device metadata tenant.",
                retryable=False,
            )

        tenant_id = payload_tenant_id or metadata_tenant_id
        if tenant_id is None:
            raise RuleEngineError(
                "Telemetry tenant scope is required for rule evaluation.",
                retryable=False,
            )
        return tenant_id

    @staticmethod
    def _normalize_tenant_id(value: object | None) -> str | None:
        if value is None:
            return None
        resolved = str(value).strip()
        return resolved or None

    def _build_request_data(
        self,
        payload: TelemetryPayload,
        *,
        projection_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        dynamic_fields = payload.get_dynamic_fields()
        request_data: Dict[str, Any] = {
            "device_id": payload.device_id,
            "timestamp": payload.timestamp.isoformat(),
            "schema_version": payload.schema_version or "v1",
            "enrichment_status": payload.enrichment_status.value,
        }
        for key, value in dynamic_fields.items():
            request_data[key] = value
        if payload.device_metadata:
            request_data["device_type"] = payload.device_metadata.type
            if payload.device_metadata.location:
                request_data["device_location"] = payload.device_metadata.location
        if projection_state:
            request_data["projected_load_state"] = projection_state.get("load_state")
            request_data["idle_streak_duration_sec"] = projection_state.get("idle_streak_duration_sec")
            request_data["idle_streak_started_at"] = projection_state.get("idle_streak_started_at")
        return request_data

    async def _write_rule_dlq(
        self,
        *,
        payload: TelemetryPayload,
        request_data: Dict[str, Any],
        error_message: str,
    ) -> None:
        if self.dlq_repository is None:
            return
        if error_message.startswith("non_retryable:"):
            error_type = "rule_engine_client_error"
        elif error_message.startswith("retryable:"):
            error_type = "rule_engine_server_error"
        elif error_message == "rule_engine_circuit_open":
            error_type = "rule_engine_circuit_open"
        else:
            error_type = "rule_engine_unexpected_error"
        await asyncio.to_thread(
            self.dlq_repository.send,
            original_payload=request_data,
            error_type=error_type,
            error_message=error_message,
            initial_status="pending",
        )
    
    async def health_check(self) -> bool:
        """Check if rule engine service is healthy."""
        try:
            success, response = await self.circuit_breaker.call(
                lambda: self._health_request()
            )
            if not success or response is None:
                return False
            return response.status_code == 200
        except Exception as e:
            logger.warning(
                "Rule engine health check failed",
                error=str(e),
            )
            return False
    
    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()
        logger.info("RuleEngineClient HTTP client closed")

    async def _health_request(self) -> httpx.Response:
        response = await self.client.get(
            f"{self.base_url}/health",
            timeout=5.0,
            headers=build_internal_headers("data-service"),
        )
        if response.status_code >= 500:
            response.raise_for_status()
        return response
