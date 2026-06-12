# Waste Analysis Service

FactoryOPS premium waste analysis backend.

Generates asynchronous waste-analysis jobs and PDFs from telemetry with strict quality gating enabled by default, idle-threshold enforcement, and tariff-based cost calculations.

## Base URL
- `http://<host>:8087`
- API prefix: `/api/v1/waste`

## Health Endpoints
- `GET /health`
- `GET /ready`

## APIs
- `POST /api/v1/waste/analysis/run`
- `GET /api/v1/waste/analysis/{job_id}/status`
- `GET /api/v1/waste/analysis/{job_id}/result`
- `GET /api/v1/waste/analysis/{job_id}/download`
- `GET /api/v1/waste/analysis/history`

## Runtime Components
- App: `src/main.py`
- Handler: `src/handlers/waste_analysis.py`
- Job orchestration: `src/tasks/waste_task.py`
- Calculation engine: `src/services/waste_engine.py`
- External clients/cache: `src/services/remote_clients.py`
- PDF builder: `src/pdf/builder.py`

## External Dependencies
- device-service (device list/details, idle threshold config)
- reporting-service settings API (`/api/v1/settings/tariff`)
- InfluxDB telemetry reader (same pattern class as reporting telemetry reads)
- MinIO for PDF storage + presigned download URL

## Calculation Model (Deterministic)
Code: `src/services/waste_engine.py`

### Energy priority order
1. `energy_kwh` delta
2. integrate normalized `power_kw` over timestamp intervals
3. derive `power_kw = (V * I * PF) / 1000`, then integrate
4. PF missing -> PF=1.0, set `pf_estimated=true`, quality `low`
5. insufficient telemetry -> quality `insufficient`

### Idle-state logic
- Uses configured idle threshold per device.
- If threshold missing:
  - `idle_status=needs_configuration`
  - idle metrics are not estimated
  - warning code includes `IDLE_THRESHOLD_NOT_CONFIGURED`

### Quality dimensions
- `energy_quality`, `idle_quality`, `standby_quality`
- `overall_quality = min(energy,idle,standby)` based on severity ordering

### Cost model
- Tariff fetched from settings API (cached by service with TTL)
- `cost = kwh * tariff_rate`
- If tariff missing/unavailable -> cost fields are `null`, not guessed

## Strict Quality Gate
Code: `src/tasks/waste_task.py`

Default behavior:
- If selected device quality is low/insufficient for required metrics, job fails with:
  - `error_code=QUALITY_GATE_FAILED`
  - structured `quality_failures[]`
- Normal waste PDF is suppressed on gate failure.

Result metadata includes:
- `quality_gate_passed`
- `quality_failures`
- `estimation_used=false` (strict semantics)

## Status Stages
Typical `status` stage messages:
- `Fetching device list...`
- `Validating configuration...`
- `Fetching tariff configuration...`
- `Loading telemetry for <device>...`
- `Generating PDF...`
- `Uploading report...`
- `Complete ✓`
- `Quality gate failed`

## Storage Model
- `waste_analysis_jobs`
  - job config/status/progress/result/s3 metadata
- `waste_device_summary`
  - per-device waste metrics and quality/warnings

## Download Contract
`GET /analysis/{job_id}/download` returns JSON containing a presigned URL for direct PDF download.

## Config
File: `src/config.py`
Important settings include:
- DB URL
- service dependency URLs
- MinIO endpoint/bucket
- strict gate toggle (default strict)
- retention controls for bounded job/artifact cleanup
- tariff cache TTL (default 60s)

## Operational Notes
- Service is read-only to other services (no cross-service writes).
- Historical jobs remain immutable; corrected logic applies to new runs.
