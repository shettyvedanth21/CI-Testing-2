import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, List

from influxdb_client.client.flux_table import FluxTable
from influxdb_client import InfluxDBClient
from influxdb_client.client.flux_table import TableList

from src.config import settings


logger = logging.getLogger(__name__)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class InfluxReader:
    def __init__(self):
        self.client = InfluxDBClient(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG
        )
        self.bucket = settings.INFLUXDB_BUCKET
        self.measurement = settings.INFLUXDB_MEASUREMENT

    async def query_telemetry(
        self,
        device_id: str,
        start_dt: datetime,
        end_dt: datetime,
        fields: List[str],
        aggregation_window: str | None = None,
    ) -> List[dict]:
        logger.info(
            "reporting_influx_query_started",
            extra={
                "device_id": device_id,
                "field_count": len(fields),
                "duration_seconds": (end_dt - start_dt).total_seconds(),
            },
        )
        
        if not device_id:
            logger.warning("query_telemetry called with empty device_id")
            return []
        
        if not fields:
            logger.warning("query_telemetry called with empty fields list")
            return []
        
        if start_dt >= end_dt:
            logger.warning(f"query_telemetry called with invalid date range: {start_dt} >= {end_dt}")
            return []
        
        try:
            return await asyncio.to_thread(
                self._query_sync, device_id, start_dt, end_dt, fields, aggregation_window
            )
        except Exception as e:
            logger.error("reporting_influx_query_failed", extra={"device_id": device_id, "error": str(e)})
            return []

    def _query_sync(
        self,
        device_id: str,
        start_dt: datetime,
        end_dt: datetime,
        fields: List[str],
        aggregation_window: str | None = None,
    ) -> List[dict]:
        safe_device_id = str(device_id).replace('"', '\\"')
        safe_fields = [str(f).replace('"', '\\"') for f in fields]
        field_parts = [f'r._field == "{f}"' for f in safe_fields]
        field_filter = " or ".join(field_parts)
        
        aggregation_window = aggregation_window or getattr(settings, 'INFLUX_AGGREGATION_WINDOW', '1m')
        
        start_str = _to_utc(start_dt).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = _to_utc(end_dt).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        flux_query = f'''
from(bucket: "{self.bucket}")
|> range(start: time(v: "{start_str}"), stop: time(v: "{end_str}"))
|> filter(fn: (r) => r._measurement == "{self.measurement}")
|> filter(fn: (r) => r.device_id == "{safe_device_id}")
|> filter(fn: (r) => {field_filter})
|> aggregateWindow(every: {aggregation_window}, fn: mean, createEmpty: false)
|> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
|> sort(columns: ["_time"])
|> limit(n: {max(1, int(settings.INFLUX_MAX_POINTS))})
'''
        
        logger.info(
            "reporting_influx_query_built",
            extra={"device_id": device_id, "query_length": len(flux_query)},
        )
        
        try:
            result: TableList = self.client.query_api().query(flux_query)
        except Exception as e:
            logger.error("reporting_influx_query_execution_failed", extra={"device_id": device_id, "error": str(e)})
            raise
        
        if result is None:
            logger.warning("InfluxDB returned None result")
            return []
        
        rows = []
        for table in result:
            if not table or not hasattr(table, 'records'):
                continue
                
            for record in table.records:
                if record is None:
                    continue
                    
                try:
                    ts = record.get_time()
                    if isinstance(ts, str):
                        from datetime import datetime
                        ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    row = {"timestamp": ts}
                    for field in fields:
                        if hasattr(record.values, '__getitem__'):
                            try:
                                row[field] = record.values.get(field)
                            except Exception:
                                pass
                    
                    if any(k in row for k in fields):
                        rows.append(row)
                except Exception as e:
                    logger.warning("reporting_influx_record_parse_failed", extra={"device_id": device_id, "error": str(e)})
                    continue
        
        logger.info("reporting_influx_query_completed", extra={"device_id": device_id, "row_count": len(rows)})

        return rows

    def close(self):
        try:
            self.client.close()
        except Exception as e:
            logger.warning("reporting_influx_client_close_failed", extra={"error": str(e)})


influx_reader = InfluxReader()
