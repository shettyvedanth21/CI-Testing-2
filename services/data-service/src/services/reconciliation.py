"""Background reconciliation between InfluxDB telemetry and MySQL live state."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import settings
from src.models import OutboxTarget
from src.repositories import InfluxDBRepository, OutboxRepository
from src.utils.circuit_breaker import get_or_create_circuit_breaker
from src.utils import get_logger
from services.shared.tenant_context import build_internal_headers, build_tenant_scoped_internal_headers

logger = get_logger(__name__)
FLEET_SNAPSHOT_PAGE_SIZE = 200


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


class ReconciliationService:
    """Detects sustained projection drift and re-enqueues resync messages."""

    def __init__(
        self,
        *,
        influx_repository: InfluxDBRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.influx_repository = influx_repository or InfluxDBRepository()
        self.outbox_repository = outbox_repository or OutboxRepository()
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._task: asyncio.Task | None = None
        self.device_circuit_breaker = get_or_create_circuit_breaker(
            "device-service",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            success_threshold=settings.circuit_breaker_success_threshold,
            open_timeout_sec=settings.circuit_breaker_open_timeout_sec,
        )
        self._last_mysql_state: list[dict[str, Any]] = []

    async def start(self) -> None:
        await self.outbox_repository.ensure_schema()
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._run(), name="telemetry-reconciliation")
        logger.info("Reconciliation service started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("Reconciliation service stopped")

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Reconciliation loop failed", error=str(exc))
            await asyncio.sleep(max(5, settings.reconciliation_interval_sec))

    async def run_once(self) -> None:
        fleet_devices = await self._fetch_mysql_state()
        if not fleet_devices:
            return
        devices_by_tenant: dict[str, list[str]] = {}
        for item in fleet_devices:
            tenant_id = str(item.get("tenant_id") or "").strip()
            device_id = str(item.get("device_id") or "").strip()
            if not tenant_id or not device_id:
                continue
            devices_by_tenant.setdefault(tenant_id, []).append(device_id)
        if not devices_by_tenant:
            return
        influx_latest: dict[str, Any] = {}
        for tenant_id, device_ids in devices_by_tenant.items():
            tenant_latest = await asyncio.to_thread(
                self.influx_repository.get_latest_telemetry_batch,
                tenant_id,
                device_ids,
            )
            influx_latest.update(tenant_latest)
        checked_at = datetime.now(timezone.utc)
        warn_seconds = int(settings.reconciliation_drift_warn_minutes) * 60
        resync_seconds = int(settings.reconciliation_drift_resync_minutes) * 60

        for device in fleet_devices:
            device_id = device["device_id"]
            mysql_ts = _parse_ts(device.get("last_seen_timestamp"))
            latest_point = influx_latest.get(device_id)
            influx_ts = latest_point.timestamp if latest_point is not None else None
            drift_seconds, action_taken = await self._evaluate_device(
                device_id=device_id,
                tenant_id=str(device.get("tenant_id") or "").strip(),
                influx_ts=influx_ts,
                mysql_ts=mysql_ts,
                checked_at=checked_at,
                warn_seconds=warn_seconds,
                resync_seconds=resync_seconds,
                latest_payload=latest_point.model_dump(mode="json") if latest_point is not None else None,
            )
            await self.outbox_repository.insert_reconciliation_log(
                device_id=device_id,
                checked_at=checked_at,
                influx_ts=influx_ts,
                mysql_ts=mysql_ts,
                drift_seconds=drift_seconds,
                action_taken=action_taken,
            )

    async def _evaluate_device(
        self,
        *,
        device_id: str,
        tenant_id: str,
        influx_ts: datetime | None,
        mysql_ts: datetime | None,
        checked_at: datetime,
        warn_seconds: int,
        resync_seconds: int,
        latest_payload: dict[str, Any] | None,
    ) -> tuple[int | None, str]:
        if influx_ts is None and mysql_ts is None:
            return None, "missing_both"
        if influx_ts is None:
            return None, "missing_influx"

        if mysql_ts is None:
            drift_seconds = int(max(resync_seconds + 1, (checked_at - influx_ts).total_seconds()))
        else:
            drift_seconds = int(abs((influx_ts - mysql_ts).total_seconds()))

        if drift_seconds > warn_seconds:
            logger.critical(
                "Telemetry reconciliation drift detected",
                device_id=device_id,
                drift_seconds=drift_seconds,
                influx_ts=influx_ts.isoformat(),
                mysql_ts=mysql_ts.isoformat() if mysql_ts else None,
            )

        if drift_seconds > resync_seconds and latest_payload is not None:
            payload = dict(latest_payload)
            if tenant_id:
                payload.setdefault("tenant_id", tenant_id)
            await self.outbox_repository.enqueue_telemetry(
                device_id=device_id,
                telemetry_payload=payload,
                targets=self._targets(),
                max_retries=settings.outbox_max_retries,
            )
            return drift_seconds, "resync_enqueued"

        return drift_seconds, "warned" if drift_seconds > warn_seconds else "none"

    def _targets(self) -> list[OutboxTarget]:
        targets = [OutboxTarget.DEVICE_SERVICE]
        if settings.energy_sync_enabled:
            targets.append(OutboxTarget.ENERGY_SERVICE)
        return targets

    async def _fetch_mysql_state(self) -> list[dict[str, Any]]:
        assert self._http_client is not None
        base_url = (settings.device_service_url or "http://device-service:8000").rstrip("/")
        devices: list[dict[str, Any]] = []
        tenant_ids = await self._fetch_active_tenant_ids(base_url)

        if not tenant_ids:
            self._last_mysql_state = []
            return []

        for tenant_id in tenant_ids:
            page = 1
            while True:
                async def _request():
                    response = await self._http_client.get(
                        f"{base_url}/api/v1/devices/dashboard/fleet-snapshot",
                        params={"page": page, "page_size": FLEET_SNAPSHOT_PAGE_SIZE, "sort": "device_name"},
                        headers=build_tenant_scoped_internal_headers("data-service", tenant_id),
                    )
                    if response.status_code >= 500:
                        response.raise_for_status()
                    return response

                success, response = await self.device_circuit_breaker.call(_request)
                if not success or response is None:
                    logger.warning("Reconciliation skipped due to open device-service circuit")
                    return list(self._last_mysql_state)
                response.raise_for_status()
                payload = response.json()
                raw_batch = payload.get("devices") or []
                batch = []
                for item in raw_batch:
                    if isinstance(item, dict):
                        annotated = dict(item)
                        annotated.setdefault("tenant_id", tenant_id)
                        batch.append(annotated)
                devices.extend(batch)
                total_pages = int(payload.get("total_pages") or 1)
                if page >= total_pages:
                    break
                page += 1

        self._last_mysql_state = list(devices)
        return devices

    async def _fetch_active_tenant_ids(self, base_url: str) -> list[str]:
        assert self._http_client is not None

        async def _request():
            response = await self._http_client.get(
                f"{base_url}/api/v1/devices/internal/active-tenant-ids",
                headers=build_internal_headers("data-service"),
            )
            if response.status_code >= 500:
                response.raise_for_status()
            return response

        success, response = await self.device_circuit_breaker.call(_request)
        if not success or response is None:
            logger.warning("Reconciliation skipped due to open device-service circuit")
            return []
        response.raise_for_status()
        payload = response.json()
        return [str(value) for value in payload.get("tenant_ids") or [] if value]
