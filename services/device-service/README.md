# Device Service

Device metadata, configuration, health scoring, shifts, runtime trends, and idle-running APIs for FactoryOPS.

## Base URL
- `http://<host>:8000`
- API prefix: `/api/v1`

## Health Endpoints
- `GET /health`
- `GET /ready`

## Device APIs (`/api/v1/devices`)

### Core
- `GET /api/v1/devices`
- `POST /api/v1/devices`
- `GET /api/v1/devices/{device_id}`
- `PUT /api/v1/devices/{device_id}`
- `DELETE /api/v1/devices/{device_id}`

### Dashboard / Properties
- `GET /api/v1/devices/dashboard/summary`
- `GET /api/v1/devices/properties`
- `POST /api/v1/devices/properties/common`
- `GET /api/v1/devices/{device_id}/properties`
- `POST /api/v1/devices/{device_id}/properties/sync`
- `GET /api/v1/devices/{device_id}/dashboard-widgets`
- `PUT /api/v1/devices/{device_id}/dashboard-widgets`

### Shifts
- `POST /api/v1/devices/{device_id}/shifts`
- `GET /api/v1/devices/{device_id}/shifts`
- `GET /api/v1/devices/{device_id}/shifts/{shift_id}`
- `PUT /api/v1/devices/{device_id}/shifts/{shift_id}`
- `DELETE /api/v1/devices/{device_id}/shifts/{shift_id}`

Shift conflict behavior:
- `POST` / `PUT` can return `409` when the candidate shift overlaps existing device shifts.
- Touching boundaries are allowed (`end` is exclusive).
- Validation includes all-days, day-specific, and cross-midnight shifts.
- Rollout hygiene check (repo root): `./scripts/report_shift_overlap_conflicts.sh`

### Uptime / Performance
- `GET /api/v1/devices/{device_id}/uptime`
- `GET /api/v1/devices/{device_id}/performance-trends`
- `POST /api/v1/devices/{device_id}/heartbeat`
- `POST /api/v1/devices/{device_id}/live-update` (internal service-to-service projection sync)

### Parameter Health Configuration
- `POST /api/v1/devices/{device_id}/health-config`
- `GET /api/v1/devices/{device_id}/health-config`
- `GET /api/v1/devices/{device_id}/health-config/validate-weights`
- `GET /api/v1/devices/{device_id}/health-config/{config_id}`
- `PUT /api/v1/devices/{device_id}/health-config/{config_id}`
- `DELETE /api/v1/devices/{device_id}/health-config/{config_id}`
- `POST /api/v1/devices/{device_id}/health-config/bulk`
- `POST /api/v1/devices/{device_id}/health-score`

Delete behavior:
- Health-config delete is idempotent: deleting an already-removed config returns success and does not raise 404.
- Delete request still triggers projection recompute and fleet stream publish for immediate cross-view convergence.
- Projection recompute clears `health_score` to `null` immediately when no active health configs remain, preventing stale health display after delete.

### Idle Running / Load State
- `GET /api/v1/devices/{device_id}/idle-config`
- `POST /api/v1/devices/{device_id}/idle-config`
- `GET /api/v1/devices/{device_id}/current-state`
- `GET /api/v1/devices/{device_id}/idle-stats`

## Health Score Formula (Implemented)
Code: `app/services/health_config.py`

For each configured parameter:
1. Compute parameter score from the configured normal band:
   - inside normal band: `100`
   - within 15% outside either normal boundary: `50`
   - beyond that tolerance: `0`
2. Compute weighted score:
   - `weighted_score = parameter_score * (weight / 100)`
3. Sum weighted scores across included parameters.

Overall:
- `health_score = round(sum(weighted_scores), 2)`
- If no parameters are included (missing telemetry/ignored): `health_score = null`

Machine-state eligibility:
- Health scoring runs for `RUNNING`, `IDLE`, and `UNLOAD`
- Health scoring returns standby / no score for `OFF` and `POWER CUT`

## Idle/Load State Rules
Code: `app/services/idle_running.py`

State detection:
- `unloaded`: `current <= 0 && voltage > 0`
- `idle`: `0 < current < threshold && voltage > 0`
- `running`: `current >= threshold && voltage > 0`
- `unknown`: missing fields, stale conditions, or threshold unavailable where required

Power for idle energy:
- direct power if available
- else derived `power_kw = (current * voltage * pf) / 1000`
- if PF missing -> assume `pf=1.0`, mark `pf_estimated=true`

Idle cost:
- computed from live tariff settings using cached tariff fetch (TTL 60s)

## Runtime/Status Contract
- Runtime `running/stopped` comes from heartbeat freshness logic.
- Load state (`in load/idle/unloaded/unknown`) is separate electrical-state logic.
- UI precedence: if runtime is stopped, load badge should display Unknown.
- Home/fleet dashboards now read from `device_live_state` for low-latency updates.
- Fleet stream fanout is Redis-backed for multi-instance consistency (`FLEET_STREAM_REDIS_CHANNEL`).
- Projection reconciler (`DASHBOARD_RECONCILE_INTERVAL_SECONDS`, default 600s) repairs drift without blocking hot-path reads.

## Dashboard Widget Config Contract
- Widget config is persisted per-device in DB (`device_dashboard_widgets`), not UI-local state.
- `GET /dashboard-widgets` returns:
  - `available_fields`: discovered numeric telemetry fields
  - `selected_fields`: explicit persisted selection
  - `effective_fields`: rendering set (selected, or fallback-all if no selection)
  - `default_applied`: `true` when fallback-all is active
- `PUT /dashboard-widgets` is idempotent full-replace of `selected_fields`.
- Validation: unknown/unavailable fields are rejected with HTTP `422`.
- Display-only filter: backend calculations and ingestion remain full-fidelity across all telemetry fields.

## Storage / Migrations
- Uses MySQL + Alembic migrations.
- Alembic version table in this service is namespaced (`alembic_version_device`) for single-DB deployments.
- Startup applies migrations automatically with a guarded baseline-stamp check for legacy pre-migrated schemas.
- Includes one-time exact-duplicate cleanup for `device_shifts` (keeps oldest row per exact key).
