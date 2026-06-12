# Data Export Service

Continuous telemetry export service for FactoryOPS. It reads InfluxDB telemetry and writes partitioned parquet/CSV datasets to object storage for downstream analytics/reporting.

## Base URL
- `http://<host>:8080` (compose default)

## Health Endpoints
- `GET /health`
- `GET /ready`

## Export API
- `POST /api/v1/exports/run`
  - Triggers export for `device_id` and date range.
- `GET /api/v1/exports/status/{device_id}`
  - Returns current checkpoint/export status for the device.

## Runtime Components
- App entry: `main.py`
- Export orchestration: `worker.py`, `exporter.py`
- Source reader: `data_source.py`
- Storage writer: `s3_writer.py`
- Checkpointing: `checkpoint.py`
- Models: `models.py`

## Export Behavior
- Pulls telemetry from InfluxDB for requested ranges.
- Writes partitioned datasets by device and date range.
- Uses checkpoints for idempotency and restart safety.
- Supports micro-batch execution and continuous operation mode.

## Dataset Contract
Typical analytics path convention:
- `datasets/{device_id}/{YYYYMMDD}_{YYYYMMDD}.parquet`

## Configuration
Primary config in `config.py` and env:
- InfluxDB connection
- MySQL checkpoint DB connection
- S3/MinIO target bucket and credentials
- Export interval/batch size/format

## Reliability Notes
- Ready probe checks dependency readiness.
- Checkpoints prevent duplicate re-export.
- Structured logs support observability and retry diagnostics.
