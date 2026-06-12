"""InfluxDB repository for telemetry storage and retrieval."""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions
from influxdb_client.client.flux_table import FluxRecord

from src.config import settings
from src.models import (
    EnrichmentStatus,
    TelemetryHistoryQueryError,
    TelemetryHistoryTimeoutError,
    TelemetryPayload,
    TelemetryPoint,
)
from src.repositories.dlq_repository import DLQRepository
from src.utils import get_logger
from services.shared.telemetry_contract import BUSINESS_TELEMETRY_FIELDS, DIAGNOSTIC_PHASE_TELEMETRY_FIELDS

logger = get_logger(__name__)


class InfluxDBRepository:
    """
    Repository for InfluxDB operations.

    Handles:
    - Writing telemetry data with dynamic tags and fields
    - Querying telemetry with time ranges and filters
    - Aggregating statistics
    """

    MEASUREMENT = "device_telemetry"
    DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
    TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
    ALLOWED_TAGS = {"tenant_id"}
    ALLOWED_FIELDS = set(BUSINESS_TELEMETRY_FIELDS) | set(DIAGNOSTIC_PHASE_TELEMETRY_FIELDS)
    ALLOWED_AGGREGATES = {"mean", "max", "min", "sum", "last"}
    ALLOWED_INTERVALS = {"1m", "5m", "15m", "1h", "1d"}

    def __init__(self, client: Optional[InfluxDBClient] = None):
        self.dlq_repository = DLQRepository()
        self._dlq_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="influx-dlq")
        self.client = client or InfluxDBClient(
            url=settings.influxdb_url,
            token=settings.influxdb_token,
            org=settings.influxdb_org,
            timeout=settings.influxdb_timeout,
        )

        self.write_api = self.client.write_api(
            write_options=WriteOptions(
                batch_size=settings.influx_batch_size,
                flush_interval=settings.influx_flush_interval_ms,
                jitter_interval=200,
                retry_interval=5000,
                max_retries=settings.influx_max_retries,
                max_retry_delay=30000,
                exponential_base=2,
            ),
            error_callback=self._on_write_error,
        )
        self.query_api = self.client.query_api()
        self._schedule_tag_audit()

        logger.info(
            "InfluxDBRepository initialized",
            url=settings.influxdb_url,
            org=settings.influxdb_org,
            bucket=settings.influxdb_bucket,
        )

    def _schedule_tag_audit(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.audit_tag_cardinality())

    def _on_write_error(self, details: tuple[Any, ...], data: str, error: Exception) -> None:
        data_lines = [line for line in (data or "").splitlines() if line.strip()]
        logger.error(
            "InfluxDB batch write failed",
            error=str(error),
            details=str(details),
            data_points=len(data_lines),
        )
        error_str = str(error)
        for line in data_lines:
            device_id = self._extract_device_id_from_line_protocol(line)
            payload = {"device_id": device_id} if device_id else {"line_protocol": line}
            self._dlq_executor.submit(self._deferred_dlq_send, payload, error_str)

    def _deferred_dlq_send(self, payload: dict[str, Any], error_message: str) -> None:
        try:
            if hasattr(self.dlq_repository, "write_failed"):
                device_id = payload.get("device_id")
                self.dlq_repository.write_failed(
                    device_id=device_id,
                    error_message=error_message,
                )
            else:
                self.dlq_repository.send(
                    original_payload=payload,
                    error_type="influxdb_batch_write_error",
                    error_message=error_message,
                )
        except Exception as exc:
            logger.warning(
                "Deferred DLQ send from InfluxDB error callback failed",
                error=str(exc),
            )

    @staticmethod
    def _extract_device_id_from_line_protocol(line_protocol: str) -> Optional[str]:
        match = re.search(r"(?:^|,)device_id=([^, ]+)", line_protocol)
        if not match:
            return None
        return match.group(1).replace("\\,", ",").replace("\\ ", " ").replace("\\=", "=")

    def write_telemetry(
        self,
        payload: TelemetryPayload,
        additional_tags: Optional[Dict[str, str]] = None,
    ) -> bool:

        try:
            additional_tags = additional_tags or {}
            safe_tags = {k: v for k, v in additional_tags.items() if k in self.ALLOWED_TAGS}
            rejected = set(additional_tags.keys()) - self.ALLOWED_TAGS

            tags = {
                "device_id": payload.device_id,
            }
            if payload.tenant_id:
                tags["tenant_id"] = payload.tenant_id

            if safe_tags:
                tags.update(safe_tags)

            fields = payload.get_dynamic_fields()

            if rejected:
                ignored_keys = sorted(rejected)
                logger.warning(
                    "influx_tags_ignored",
                    ignored_keys=ignored_keys,
                    device_id=payload.device_id,
                )

            point = Point(self.MEASUREMENT)

            for k, v in tags.items():
                point = point.tag(k, v)

            for k, v in fields.items():
                point = point.field(k, v)

            point = point.time(payload.timestamp)

            self.write_api.write(
                bucket=settings.influxdb_bucket,
                org=settings.influxdb_org,
                record=point,
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to write telemetry to InfluxDB",
                device_id=payload.device_id,
                error=str(e),
            )
            return False

    async def audit_tag_cardinality(self) -> Dict[str, int]:
        """Query InfluxDB for current tag cardinality and log risky series."""
        return await asyncio.to_thread(self._audit_tag_cardinality_sync)

    def _audit_tag_cardinality_sync(self) -> Dict[str, int]:
        tag_cardinality: Dict[str, int] = {}
        audit_tags = sorted(self.ALLOWED_TAGS | {"device_id"})
        for tag_name in audit_tags:
            try:
                flux_query = f'''
import "influxdata/influxdb/schema"
schema.tagValues(
    bucket: "{settings.influxdb_bucket}",
    tag: "{tag_name}",
    predicate: (r) => r._measurement == "{self.MEASUREMENT}"
)
'''
                tables = self.query_api.query(flux_query, org=settings.influxdb_org)
                values = set()
                for table in tables:
                    for record in table.records:
                        value = record.get_value()
                        if value is not None:
                            values.add(value)
                count = len(values)
                tag_cardinality[tag_name] = count
                if count > 1000:
                    logger.warning(
                        "InfluxDB tag cardinality exceeded threshold",
                        tag_name=tag_name,
                        unique_value_count=count,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to audit InfluxDB tag cardinality",
                    tag_name=tag_name,
                    error=str(exc),
                )
        logger.info(
            "InfluxDB tag cardinality audit complete",
            tag_cardinality=tag_cardinality,
        )
        return tag_cardinality

    def query_telemetry(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        fields: Optional[List[str]] = None,
        aggregate: Optional[str] = None,
        interval: Optional[str] = None,
        limit: int = 1000,
        sort_desc: bool = True,
    ) -> List[TelemetryPoint]:

        try:
            if start_time is None:
                # Default to a configurable rolling lookback window rather than
                # midnight-only queries, so historical data remains visible next day.
                start_time = datetime.utcnow() - timedelta(
                    hours=settings.telemetry_default_lookback_hours
                )

            if end_time is None:
                end_time = datetime.utcnow()

            flux_query = self._build_query(
                tenant_id=tenant_id,
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
                fields=fields,
                aggregate=aggregate,
                interval=interval,
                limit=limit,
                sort_desc=sort_desc,
            )

            tables = self.query_api.query(
                flux_query,
                org=settings.influxdb_org,
            )

            points: List[TelemetryPoint] = []

            for table in tables:
                for record in table.records:
                    point = self._parse_record_to_point(record)
                    if point:
                        points.append(point)

            # Influx may return the sorted+limited result set split across
            # multiple tables/chunks. Normalize ordering after parsing so
            # callers consistently receive the requested sort order.
            points.sort(
                key=lambda p: (
                    p.timestamp.replace(tzinfo=timezone.utc).timestamp()
                    if p.timestamp.tzinfo is None
                    else p.timestamp.timestamp()
                ),
                reverse=sort_desc,
            )
            if limit > 0:
                points = points[:limit]

            return points

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                "Failed to query telemetry",
                device_id=device_id,
                error=str(e),
            )
            message = str(e).lower()
            if isinstance(e, TimeoutError) or "timeout" in message or "timed out" in message:
                raise TelemetryHistoryTimeoutError() from e
            raise TelemetryHistoryQueryError(
                code="TELEMETRY_HISTORY_UNAVAILABLE",
                message="Telemetry history is temporarily unavailable.",
                status_code=503,
                source="influx",
                retryable=True,
            ) from e

    async def async_query_telemetry(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        fields: Optional[List[str]] = None,
        aggregate: Optional[str] = None,
        interval: Optional[str] = None,
        limit: int = 1000,
        sort_desc: bool = True,
    ) -> List[TelemetryPoint]:
        return await asyncio.to_thread(
            self.query_telemetry,
            tenant_id,
            device_id,
            start_time,
            end_time,
            fields,
            aggregate,
            interval,
            limit,
            sort_desc,
        )

    def get_stats(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:

        try:
            self._validate_flux_inputs(tenant_id=tenant_id, device_id=device_id)
            if start_time is None:
                start_time = datetime.utcnow() - timedelta(
                    hours=settings.telemetry_default_lookback_hours
                )

            if end_time is None:
                end_time = datetime.utcnow()

            start = start_time.isoformat() + "Z" if start_time.tzinfo is None else start_time.isoformat()
            end = end_time.isoformat() + "Z" if end_time.tzinfo is None else end_time.isoformat()

            flux_query = f'''
            from(bucket: "{settings.influxdb_bucket}")
                |> range(start: time(v: "{start}"), stop: time(v: "{end}"))
                |> filter(fn: (r) => r._measurement == "{self.MEASUREMENT}")
                |> filter(fn: (r) => r.tenant_id == "{tenant_id}")
                |> filter(fn: (r) => r.device_id == "{device_id}")
            '''

            tables = self.query_api.query(
                flux_query,
                org=settings.influxdb_org,
            )

            stats = self._aggregate_stats_dynamic(device_id, tables, start_time, end_time)

            return stats

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                "Failed to get telemetry stats",
                device_id=device_id,
                error=str(e),
            )
            return None

    async def async_get_stats(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(
            self.get_stats,
            tenant_id,
            device_id,
            start_time,
            end_time,
        )

    def get_latest_telemetry(self, tenant_id: str, device_id: str) -> Optional[TelemetryPoint]:
        """Fast-path latest row for a single device."""
        points = self.query_telemetry(tenant_id=tenant_id, device_id=device_id, limit=1)
        return points[0] if points else None

    async def async_get_latest_telemetry(self, tenant_id: str, device_id: str) -> Optional[TelemetryPoint]:
        return await asyncio.to_thread(self.get_latest_telemetry, tenant_id, device_id)

    def get_earliest_telemetry(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
    ) -> Optional[TelemetryPoint]:
        """Fast-path earliest row for a single device."""
        points = self.query_telemetry(
            tenant_id=tenant_id,
            device_id=device_id,
            start_time=start_time,
            limit=1,
            sort_desc=False,
        )
        return points[0] if points else None

    async def async_get_earliest_telemetry(
        self,
        tenant_id: str,
        device_id: str,
        start_time: Optional[datetime] = None,
    ) -> Optional[TelemetryPoint]:
        return await asyncio.to_thread(
            self.get_earliest_telemetry,
            tenant_id,
            device_id,
            start_time,
        )

    def get_latest_telemetry_batch(
        self,
        tenant_id: str,
        device_ids: List[str],
    ) -> Dict[str, Optional[TelemetryPoint]]:
        """Fetch latest telemetry for a list of device IDs."""
        if not device_ids:
            return {}

        unique_device_ids = list(dict.fromkeys(device_ids))
        self._validate_flux_inputs(tenant_id=tenant_id, device_id=unique_device_ids[0])
        for device_id in unique_device_ids:
            self._validate_flux_inputs(tenant_id=tenant_id, device_id=device_id)

        start_time = datetime.utcnow() - timedelta(
            hours=settings.telemetry_default_lookback_hours
        )
        end_time = datetime.utcnow()

        start = start_time.isoformat() + "Z" if start_time.tzinfo is None else start_time.isoformat()
        end = end_time.isoformat() + "Z" if end_time.tzinfo is None else end_time.isoformat()
        device_filter = " or ".join([f'r.device_id == "{device_id}"' for device_id in unique_device_ids])

        flux_query = f'''
        from(bucket: "{settings.influxdb_bucket}")
            |> range(start: time(v: "{start}"), stop: time(v: "{end}"))
            |> filter(fn: (r) => r._measurement == "{self.MEASUREMENT}" and r.tenant_id == "{tenant_id}" and ({device_filter}))
            |> pivot(
                rowKey: ["_time", "device_id"],
                columnKey: ["_field"],
                valueColumn: "_value"
            )
            |> group(columns: ["device_id"])
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: 1)
        '''

        tables = self.query_api.query(
            flux_query,
            org=settings.influxdb_org,
        )

        latest: Dict[str, Optional[TelemetryPoint]] = {device_id: None for device_id in unique_device_ids}
        for table in tables:
            for record in table.records:
                point = self._parse_record_to_point(record)
                if not point:
                    continue
                current = latest.get(point.device_id)
                point_ts = point.timestamp.timestamp() if point.timestamp.tzinfo else point.timestamp.replace(tzinfo=timezone.utc).timestamp()
                current_ts = None
                if current is not None:
                    current_ts = current.timestamp.timestamp() if current.timestamp.tzinfo else current.timestamp.replace(tzinfo=timezone.utc).timestamp()
                if current is None or point_ts > current_ts:
                    latest[point.device_id] = point

        return {device_id: latest.get(device_id) for device_id in device_ids}

    async def async_get_latest_telemetry_batch(
        self,
        tenant_id: str,
        device_ids: List[str],
    ) -> Dict[str, Optional[TelemetryPoint]]:
        return await asyncio.to_thread(
            self.get_latest_telemetry_batch,
            tenant_id,
            device_ids,
        )

    def _build_query(
        self,
        tenant_id: str,
        device_id: str,
        start_time: datetime,
        end_time: datetime,
        fields: Optional[List[str]] = None,
        aggregate: Optional[str] = None,
        interval: Optional[str] = None,
        limit: int = 1000,
        sort_desc: bool = True,
    ) -> str:
        self._validate_flux_inputs(
            tenant_id=tenant_id,
            device_id=device_id,
            fields=fields,
            aggregate=aggregate,
            interval=interval,
        )

        start = start_time.isoformat() + "Z" if start_time.tzinfo is None else start_time.isoformat()
        end = end_time.isoformat() + "Z" if end_time.tzinfo is None else end_time.isoformat()

        query = f'''
        from(bucket: "{settings.influxdb_bucket}")
            |> range(start: time(v: "{start}"), stop: time(v: "{end}"))
            |> filter(fn: (r) => r._measurement == "{self.MEASUREMENT}")
            |> filter(fn: (r) => r.tenant_id == "{tenant_id}")
            |> filter(fn: (r) => r.device_id == "{device_id}")
        '''

        if fields:
            field_filters = " or ".join([f'r._field == "{f}"' for f in fields])
            query += f'|> filter(fn: (r) => {field_filters})\n'

        if aggregate and interval:
            query += f'|> aggregateWindow(every: {interval}, fn: {aggregate}, createEmpty: false)\n'

        query += '''
            |> pivot(
                rowKey: ["_time"],
                columnKey: ["_field"],
                valueColumn: "_value"
            )
        '''

        query += f'|> sort(columns: ["_time"], desc: {str(sort_desc).lower()})\n'
        query += f'|> limit(n: {limit})\n'

        return query

    def _validate_flux_inputs(
        self,
        *,
        tenant_id: str,
        device_id: str,
        fields: Optional[List[str]] = None,
        aggregate: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> None:
        if not tenant_id or not self.TENANT_ID_PATTERN.fullmatch(tenant_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "VALIDATION_ERROR",
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid tenant_id. Only letters, numbers, hyphens, and underscores are allowed.",
                },
            )

        if not device_id or not self.DEVICE_ID_PATTERN.fullmatch(device_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "VALIDATION_ERROR",
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid device_id. Only letters, numbers, hyphens, and underscores are allowed.",
                },
            )

        if fields:
            invalid_fields = sorted({field for field in fields if field not in self.ALLOWED_FIELDS})
            if invalid_fields:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "VALIDATION_ERROR",
                        "code": "VALIDATION_ERROR",
                        "message": f"Invalid fields requested: {', '.join(invalid_fields)}",
                    },
                )

        if aggregate is not None and aggregate not in self.ALLOWED_AGGREGATES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "VALIDATION_ERROR",
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid aggregate. Allowed values: mean, max, min, sum, last.",
                },
            )

        if interval is not None and interval not in self.ALLOWED_INTERVALS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "VALIDATION_ERROR",
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid interval. Allowed values: 1m, 5m, 15m, 1h, 1d.",
                },
            )

    def _parse_record_to_point(self, record: FluxRecord) -> Optional[TelemetryPoint]:
        """Parse pivoted Flux record into TelemetryPoint."""
        try:
            values = record.values
            
            point_data = {
                "timestamp": record.get_time() or datetime.utcnow(),
                "device_id": values.get("device_id", ""),
                "schema_version": "v1",
                "enrichment_status": "pending",
            }
            
            for key, value in values.items():
                if key not in ("_start", "_stop", "_time", "_measurement", "_field", "_value",
                               "device_id", "tenant_id", "table", "result"):
                    if isinstance(value, (int, float)):
                        point_data[key] = value
            
            return TelemetryPoint(**point_data)

        except Exception as e:
            logger.error(
                "Failed to parse Flux record",
                error=str(e),
            )
            return None

    def _aggregate_stats_dynamic(
        self,
        device_id: str,
        tables: List[Any],
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]:
        
        field_values: Dict[str, List[float]] = {}

        for table in tables:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()

                if value is not None and isinstance(value, (int, float)):
                    if field not in field_values:
                        field_values[field] = []
                    field_values[field].append(float(value))

        stats = {
            "device_id": device_id,
            "start_time": start_time,
            "end_time": end_time,
            "data_points": sum(len(v) for v in field_values.values()) if field_values else 0,
        }

        for field, values in field_values.items():
            if values:
                stats[f"{field}_min"] = min(values)
                stats[f"{field}_max"] = max(values)
                stats[f"{field}_avg"] = sum(values) / len(values)
                if field == "power":
                    stats[f"{field}_total"] = sum(values)

        return stats

    def close(self) -> None:
        try:
            self.write_api.flush()
            self.write_api.close()
            self.client.close()
            self._dlq_executor.shutdown(wait=True)
            self.dlq_repository.close()
            logger.info("InfluxDB client closed")
        except Exception as e:
            logger.error("Error closing InfluxDB client", error=str(e))
