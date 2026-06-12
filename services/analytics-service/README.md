# Analytics Service

Production ML analytics service for FactoryOPS. This service runs async jobs for anomaly detection and failure prediction, supports fleet orchestration, and returns both raw and formatted results for the UI.

## Base URL
- `http://<host>:8003` (compose default)

## Health Endpoints
- `GET /health/live`
- `GET /health/ready`

## Core API (`/api/v1/analytics`)

### Run Jobs
- `POST /api/v1/analytics/run`
  - Runs single-device analytics (`anomaly`, `failure`, or `both`).
- `POST /api/v1/analytics/run-fleet`
  - Runs fleet analytics for all devices (parent + child jobs).

### Job Tracking
- `GET /api/v1/analytics/status/{job_id}`
- `GET /api/v1/analytics/results/{job_id}`
- `GET /api/v1/analytics/formatted-results/{job_id}`

### Metadata
- `GET /api/v1/analytics/models`
- `GET /api/v1/analytics/jobs`
- `GET /api/v1/analytics/datasets?device_id=<id>`
- `GET /api/v1/analytics/retrain-status`

## Runtime Architecture
- API routes: `src/api/routes/analytics.py`
- Worker queue: `src/workers/job_queue.py`, `src/workers/job_worker.py`
- Admission control and fairness: `src/services/scaling_policy.py`
- Job orchestration: `src/services/job_runner.py`
- Pipelines:
  - `src/services/analytics/anomaly_detection.py`
  - `src/services/analytics/failure_prediction.py`
- Dataset loading: `src/services/dataset_service.py`
- Formatting: `src/services/result_formatter.py`
- Persistence: `src/infrastructure/mysql_repository.py`
- Retrainer: `src/services/analytics/retrainer.py`

## Data Source
- Primary input: parquet datasets in object storage.
- Canonical key format:
  - `datasets/{device_id}/{YYYYMMDD}_{YYYYMMDD}.parquet`
- Data is loaded at execution time (job run), not during UI wizard steps.

## Implemented Model Logic

### 1) Anomaly Detection
- Model: `IsolationForest`
- Preprocessing:
  - timestamp normalization
  - numeric feature filtering
  - 1-minute resampling
  - sanitization for NaN/Inf
- Outputs include:
  - `is_anomaly`, `anomaly_score`, anomaly details, timeline timestamps

### 2) Failure Prediction
- Model: `RandomForestClassifier`
- Preprocessing:
  - timestamp leakage column removal
  - rolling and trend features
  - stress labeling pipeline
- Outputs include:
  - failure probability, risk level, risk factors, recommendations

## Health / Confidence Formulas (Formatter)
Code: `src/services/result_formatter.py`

### Anomaly score
- `raw_score = (high*3 + medium*2 + low*1) / total_points * 1000`
- `anomaly_score = min(100, raw_score / max_possible * 100)`

### Health score (anomaly-only)
- `health_score = clamp(100 - anomaly_score * 0.60, 0, 100)`

### Health score (failure / combined)
- `health_score = clamp(100 - anomaly_score * 0.60 - failure_probability_pct * 0.40, 0, 100)`

## Fleet Behavior
- Fleet run creates parent + child jobs.
- Parent formatted response includes per-device summaries and `child_job_id` for drilldown.
- UI can open child formatted results directly without re-running analytics.

## Error Contract
App-level handlers in `src/main.py` enforce structured errors:
- Validation: `422` with `code=VALIDATION_ERROR`
- HTTP and internal errors return JSON payloads (no raw traceback to client).

## Environment Notes
Main config source: `src/config/settings.py`
Key settings include:
- DB URL
- object storage bucket/key settings
- retrainer enable/schedule flags
- feature flags for formatted/premium behavior

## Operational Notes
- Jobs are async and persisted with progress/stage.
- Formatted results are additive and backward-compatible with raw result endpoints.
- Retrainer is lifecycle-managed and can be toggled by config.
- Schema is managed by Alembic on startup with guarded baseline stamping.
- Docker image optimized: CPU-only torch, removed unused dependencies. Target size: under 2GB. Run docker image prune -f after every deploy.

## Production Scaling Policy
- API and worker roles remain separated with `APP_ROLE=api|worker`; heavy analytics execution stays in workers only.
- Worker concurrency is bounded per replica by `MAX_CONCURRENT_JOBS`.
- Global active work is capped by `GLOBAL_ACTIVE_JOB_LIMIT`.
- Backlog growth is capped by `QUEUE_BACKLOG_REJECT_THRESHOLD`; submissions beyond the safe backlog return an explicit overload response instead of growing the queue indefinitely.
- Redis stream hard capacity is set by `QUEUE_MAX_LENGTH`.
- Tenant fairness is enforced at submission time with:
  - `TENANT_MAX_QUEUED_JOBS`
  - `TENANT_MAX_ACTIVE_JOBS`
- Retries stay bounded with `QUEUE_MAX_ATTEMPTS`.
- Stale worker recovery remains enabled and is scanned on `STALE_SCAN_INTERVAL_SECONDS`.

## Capacity Guidance
- Scale worker replicas first when queue depth rises persistently but CPU and memory headroom remain healthy.
- Increase `MAX_CONCURRENT_JOBS` only when one worker replica has spare CPU, memory, and model-runtime headroom; this is the per-process parallelism knob.
- Increase `GLOBAL_ACTIVE_JOB_LIMIT` only alongside worker replica or per-worker concurrency increases so admission control still reflects real throughput.
- Keep `QUEUE_BACKLOG_REJECT_THRESHOLD` well below `QUEUE_MAX_LENGTH` so the API sheds load before Redis becomes the only protection layer.
- Tenant caps should reflect your fairness policy. A common production posture is to keep tenant queued capacity materially below the global backlog threshold so one org cannot monopolize the service.

## Operational Visibility
- `GET /api/v1/analytics/ops/queue` now reports:
  - queued jobs
  - running jobs
  - failed jobs
  - retry count
  - dead-letter count
  - claimed Redis messages
  - top tenants by active job volume
  - configured capacity thresholds
