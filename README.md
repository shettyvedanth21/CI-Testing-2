# FactoryOPS / Cittagent Platform

FactoryOPS is a multi-service industrial monitoring platform with:

- `ui-web` for the operator dashboard
- `device-service` for onboarding, shifts, health config, runtime state, and live projections
- `data-service` for MQTT ingestion and telemetry queries
- `analytics-service` for anomaly and failure jobs
- `rule-engine-service` for rules and alerts
- `reporting-service` for energy reports and PDFs
- `waste-analysis-service` for waste and idle analysis
- `copilot-service` for assistant-style queries
- `auth-service` for authentication and org/tenant scope
- supporting services like MySQL, Redis, InfluxDB, MinIO, and EMQX

## What You Need

- Docker Engine with Docker Compose v2
- Access to the repository root
- Optional for local UI work: Node.js 20+
- Optional for simulator CLI testing: Python 3 and `mosquitto_pub`

Ports used by the default compose stack:

| Service | Port |
|---|---|
| UI | `3000` |
| Device Service | `8000` |
| Data Service | `8081` |
| Rule Engine Service | `8002` |
| Analytics Service | `8003` |
| Data Export Service | `8080` |
| Reporting Service | `8085` |
| Waste Analysis Service | `8087` |
| Copilot Service | `8007` |
| Energy Service | `8010` |
| Auth Service | `8090` |
| EMQX MQTT | `1883` |
| MySQL | `3306` |
| Redis | `6379` |
| InfluxDB | `8086` |
| MinIO API / Console | `9000` / `9001` |

Optional monitoring profile ports:

| Service | Port |
|---|---|
| Prometheus | `9090` |
| Alertmanager | `9093` |
| Grafana | `3001` |

## First-Time Setup

1. Create your environment file if you do not already have one.

```bash
cp .env.example .env.local
```

2. Fill in the values that matter for your environment.

At minimum, check:

- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`
- `JWT_SECRET_KEY`
- `INTERNAL_SERVICE_SHARED_SECRET`
- `AUTH_SERVICE_URL`
- `MINIO_EXTERNAL_URL`
- email/SMTP settings if you want notifications
- `REDIS_MAXMEMORY` and `REDIS_MAXMEMORY_POLICY` if you want to override the production-safe Redis defaults

3. Start the default local app stack with the local override.

```bash
docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

To include the optional local monitoring stack later, run:

```bash
docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml --profile monitoring up -d --build
```

For the permanent deployment path, the repository now supports GHCR image publishing on `main`, and the base production compose is image-based for first-party app services. Local development remains build-based through the local override file. See `implementation-docs/ghcr-phase1.md`.

### Production deployment modes

Normal production releases should use the GHCR pull-based path:

```bash
docker compose --env-file .env pull
docker compose --env-file .env up -d
```

#### Server runbook: GHCR deploy

1. SSH to the server and move into the repo:

```bash
ssh -i ~/Downloads/Cittagent-Mar12.pem ubuntu@32.193.53.87
cd ~/Shivex-main-1
```

2. One-time only, log Docker into GHCR with a GitHub token that can pull private packages:

```bash
export GHCR_PAT='your_token_here'
export GHCR_USERNAME='your_github_username'
echo "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
unset GHCR_PAT
```

3. Update the release tag in `.env`:

```env
GHCR_OWNER_LOWER=cittagent
GHCR_REPO_LOWER=shivex-main-1
APP_IMAGE_TAG=sha-<merge-sha>
```

4. Pull and start the release:

```bash
docker compose --env-file .env pull
docker compose --env-file .env up -d
docker compose ps
```

5. Roll back by setting `APP_IMAGE_TAG` back to an older `sha-*` and rerunning the same `pull` and `up -d` commands.

If GitHub Actions or GHCR publishing are temporarily unavailable, use the
server-build fallback override:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.server-build.yml up -d --build
```

#### Server runbook: fallback deploy

Use this path only when a new GHCR image set is unavailable, for example:

- GitHub Actions minutes are exhausted
- GHCR publish failed
- you need an emergency source-based deploy from the server

Server steps:

```bash
ssh -i ~/Downloads/Cittagent-Mar12.pem ubuntu@32.193.53.87
cd ~/Shivex-main-1
git pull origin main
docker compose --env-file .env -f docker-compose.yml -f docker-compose.server-build.yml up -d --build
docker compose ps
```

This fallback reintroduces `build:` for first-party app services only. It does not add local-only MySQL, MinIO, or simulator services to production.

Important:

- choose one deployment path per release, not both
- GHCR path is the default and preferred path
- server-build fallback is the emergency backup path
- the fallback does not add local-only infrastructure like MySQL, MinIO, or the simulator

Redis defaults in the base compose now set bounded memory explicitly:

- `REDIS_MAXMEMORY=512mb`
- `REDIS_MAXMEMORY_POLICY=noeviction`

`noeviction` is intentional for this platform because Redis carries streams, queue state, and auth/runtime coordination data. Failing writes loudly when the bound is reached is safer than silently evicting active queue or revocation keys.

4. Confirm the services are healthy.

```bash
docker compose ps
curl -s http://localhost:8000/health
curl -s http://localhost:8081/api/v1/data/health
curl -s http://localhost:8002/health
curl -s http://localhost:8003/health
curl -s http://localhost:8085/health
curl -s http://localhost:8087/health
```

5. Open the UI.

```text
http://localhost:3000
```

## Pre-Production Validation

Use the single repo-native runner before deployment:

```bash
python3 scripts/preprod_validation.py --mode current-live
```

For credentials, modes, reset behavior, artifacts, and GO / NO-GO semantics, see [docs/preprod_validation.md](/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/docs/preprod_validation.md).

## CI Validation

GitHub Actions runs the broad validation workflow on:

- pull requests to `main`
- pushes to `main`
- pushes to `Dev-Testing`

The workflow entrypoint is [.github/workflows/validation.yml](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/.github/workflows/validation.yml).

### How Broad CI works

Broad CI is organized by **business suites**, not by a single giant test command.

- A **suite** is a logical validation group such as `Auth And Identity`, `Device Onboarding And Provisioning`, or `Platform Maintenance`.
- The suite-to-test-file mapping lives in [scripts/ci_broad_validation.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/scripts/ci_broad_validation.py).
- The workflow first prepares a **suite matrix**, which is just the list of suites GitHub Actions should run in parallel.
- Those suites are split into 3 categories:
  - `python`
  - `browser`
  - `stateful`

The actual runner script used by GitHub Actions and local validation is [scripts/run-broad-ci-validation.sh](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/scripts/run-broad-ci-validation.sh).

### Local broad CI mirror

Run the same broad validation orchestrator locally with:

```bash
./scripts/run-broad-ci-validation.sh
```

Useful local variants:

```bash
./scripts/run-broad-ci-validation.sh --list-suites
./scripts/run-broad-ci-validation.sh --suite "Auth And Identity"
./scripts/run-broad-ci-validation.sh --suite "Premium Feature Gating"
```

CI coverage is aligned to the suite table in [CI.md](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/implementation-docs/CI.md). The suite manifest in [scripts/ci_broad_validation.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/scripts/ci_broad_validation.py) is validated against that file before execution.

### Human-readable CI coverage index

The broad CI plan tracks `266 / 266` business checklist items. Those `266` items are grouped into 22 readable suites below.

#### Broad CI suite catalog

| Suite | Items | What it validates | Main test files |
|---|---:|---|---|
| `Auth And Identity` | 18 | Invite lifecycle, password reset, session revocation, and cookie security | `services/auth-service/tests/test_invite_and_reset_lifecycle.py`, `services/auth-service/tests/test_token_version_revocation.py`, `services/auth-service/tests/test_auth_cookie_security.py` |
| `Org Plant And User Management` | 21 | Org user scope, plant lifecycle, and admin management boundaries | `services/auth-service/tests/test_org_user_scope.py`, `services/auth-service/tests/test_org_plant_lifecycle.py` |
| `Premium Feature Gating` | 15 | Premium entitlement checks across analytics, reporting, waste, copilot, and UI navigation | `services/analytics-service/tests/integration/test_feature_gate_api.py`, `services/reporting-service/tests/test_feature_gate_api.py`, `services/waste-analysis-service/tests/test_feature_gate_api.py`, `services/copilot-service/tests/test_feature_gate_api.py`, `ui-web/tests/e2e/premium-feature-gating.spec.js` |
| `Org Suspension And Access Enforcement` | 9 | Suspended-org protections and access denial behavior | `services/auth-service/tests/test_org_plant_lifecycle.py`, `services/auth-service/tests/test_org_user_scope.py` |
| `Device Onboarding And Provisioning` | 27 | Device create flow, plant guardrails, duplicate protection, and generated IDs | `services/device-service/tests/test_device_onboarding_phase2.py`, `services/device-service/tests/test_plant_lifecycle_guards.py`, `services/device-service/tests/test_device_id_generation.py` |
| `MQTT Auth And Telemetry Ingestion` | 17 | MQTT credential lifecycle, telemetry contract handling, and ingestion backpressure | `services/device-service/tests/test_device_mqtt_credentials.py`, `services/data-service/tests/test_telemetry_phase2.py`, `services/data-service/tests/test_mqtt_handler.py`, `services/data-service/tests/test_backpressure.py` |
| `Machine Runtime States` | 7 | Live runtime state consistency, unknown devices, and startup reconciliation | `services/device-service/tests/test_live_projection_service.py`, `services/device-service/tests/test_load_state_consistency.py`, `services/device-service/tests/test_live_update_unknown_device.py`, `services/device-service/tests/test_startup_reconcile.py` |
| `Machine Dashboard Calculations` | 12 | Dashboard summary math, loss stats, and snapshot correctness | `services/device-service/tests/test_live_dashboard_summary.py`, `services/device-service/tests/test_device_loss_stats.py`, `services/device-service/tests/test_snapshot_storage.py` |
| `Parameter Config And Health Score` | 14 | Health config validation, uniqueness, dashboard scope, and trend parameter resolution | `services/device-service/tests/test_phase3_machine_api_validation.py`, `services/device-service/tests/test_health_config_uniqueness.py`, `services/device-service/tests/test_dashboard_health_scope.py`, `services/device-service/tests/test_health_trend_parameter_resolution.py` |
| `Shift And Uptime` | 12 | Shift overlap logic, shift/calendar runtime correctness, and bootstrap latency guard | `services/device-service/tests/test_phase3_shift_dashboard_calendar.py`, `services/device-service/tests/test_dashboard_bootstrap_latency_guard.py` |
| `Maintenance Records` | 8 | Maintenance log CRUD and machine validation around maintenance operations | `services/device-service/tests/test_maintenance_log_api.py`, `services/device-service/tests/test_phase3_machine_api_validation.py` |
| `Calendar And Consumption Consistency` | 5 | Calendar rollovers and consumption snapshot consistency | `services/device-service/tests/test_phase3_shift_dashboard_calendar.py`, `services/device-service/tests/test_snapshot_storage.py` |
| `Analytics Job Flow` | 16 | Analytics job orchestration, result scope, worker heartbeat, and restart cleanup | `services/analytics-service/tests/integration/test_phase4_truthfulness_scope.py`, `services/analytics-service/tests/unit/test_job_runner.py`, `services/analytics-service/tests/unit/test_job_status_route_payload.py`, `services/analytics-service/tests/unit/test_result_scope.py`, `services/analytics-service/tests/unit/test_worker_heartbeat.py`, `services/analytics-service/tests/unit/test_worker_restart_cleanup.py` |
| `Reports And Scheduled Reports` | 11 | Report generation truthfulness, history scope, and scheduler reliability | `services/reporting-service/tests/test_phase4_truthfulness_scope.py`, `services/reporting-service/tests/test_long_running_job_contract.py`, `services/reporting-service/tests/test_report_history_scope.py`, `services/reporting-service/tests/test_report_device_scope.py`, `services/reporting-service/tests/test_scheduler_reliability.py` |
| `Waste Analysis` | 6 | Waste run truthfulness and long-running waste job behavior | `services/waste-analysis-service/tests/test_phase4_truthfulness_scope.py`, `services/waste-analysis-service/tests/test_long_running_job_contract.py` |
| `Factory Copilot` | 8 | Chat gate enforcement and provider-optional fallback behavior | `services/copilot-service/tests/test_phase4_chat_gate.py`, `services/copilot-service/tests/test_chat_provider_optional.py` |
| `Rules And Notifications` | 17 | Rule lifecycle, plant scope, and notification audit behavior | `services/rule-engine-service/tests/test_phase5_rules_notifications.py`, `services/rule-engine-service/tests/test_rule_plant_scope.py`, `services/rule-engine-service/tests/test_notification_audit_ledger.py` |
| `Notification Usage And Delivery Accounting` | 8 | Admin notification usage accounting and delivery ledger correctness | `services/rule-engine-service/tests/test_admin_notification_usage_api.py`, `services/rule-engine-service/tests/test_notification_audit_ledger.py` |
| `Tariff And Tenant Isolation` | 5 | Tariff validation, revision boundaries, and reporting tenant isolation | `services/reporting-service/tests/test_phase5_tariff_validation.py`, `services/reporting-service/tests/test_revision_and_tariff_foundation.py`, `tests/test_reporting_tariff_resolver.py`, `tests/test_reporting_settings_tenant_isolation.py` |
| `Platform Maintenance` | 8 | Maintenance scheduling, overlap rejection, and status/delivery contracts | `services/auth-service/tests/test_platform_maintenance_phase5.py`, `services/auth-service/tests/test_platform_maintenance.py`, `services/auth-service/tests/test_platform_maintenance_status.py`, `services/auth-service/tests/test_platform_maintenance_delivery.py` |
| `Runtime Stability And Recovery` | 14 | Auth/data runtime degradation truthfulness, token cleanup, hard-cut behavior, and circuit-breaker recovery | `services/auth-service/tests/test_phase6_runtime_truthfulness.py`, `services/auth-service/tests/test_token_cleanup_service.py`, `services/auth-service/tests/test_tenant_identity_hard_cut.py`, `services/data-service/tests/test_phase6_runtime_truthfulness.py`, `services/data-service/tests/test_mqtt_handler.py`, `services/data-service/tests/test_circuit_breaker.py` |
| `Database Integrity And Concurrency` | 8 | Outbox integrity and optimistic locking under concurrent writes | `services/data-service/tests/test_outbox_integrity.py`, `services/device-service/tests/test_optimistic_lock.py` |

### Where the tests live

- Backend and service validation:
  - top-level [tests](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/tests)
  - service-local `tests/` folders under `services/*`
- Browser/UI validation:
  - [ui-web/tests/e2e](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ui-web/tests/e2e)

Important distinction:

- top-level `tests/e2e` is Python/pytest-based backend or contract-style validation
- `ui-web/tests/e2e` is Playwright browser automation

### Playwright local commands

Run from `ui-web`:

```bash
cd /Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ui-web
./node_modules/.bin/playwright test --reporter=line
./node_modules/.bin/playwright test --headed --reporter=line
./node_modules/.bin/playwright test --ui --ui-host 127.0.0.1 --ui-port 9323
```

For a slow visual walkthrough:

```bash
cd /Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ui-web
PW_SLOW_MO=1200 ./node_modules/.bin/playwright test --headed --reporter=line
```

### Playwright browser suite catalog

The browser suite lives in [ui-web/tests/e2e](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ui-web/tests/e2e). Below is the human-readable purpose of each spec:

| Playwright spec | What it covers |
|---|---|
| `admin-org-create.spec.js` | Super admin creates an organisation from the UI and sees it in the admin directory |
| `admin-org-hardware.spec.js` | Org hardware inventory lifecycle from admin detail pages, plus non-super-admin denial |
| `auth-protected-recovery.spec.js` | Protected requests refresh auth correctly, and expired refresh returns users to login truthfully |
| `auth-session-negative.spec.js` | Login failures, native form validation, forgot/reset-password flows, invite acceptance, and logout contracts |
| `closure-surface-completion.spec.js` | Machine-detail MQTT/health/shift/alert gaps plus settings, reports, calendar, and dashboard truthfulness closures |
| `dashboard-health-shift-calendar-depth.spec.js` | Dashboard zero-state, health config validation, shift overlap/delete behavior, and calendar stale-cost truthfulness |
| `deploy-recovery.spec.js` | Automatic/manual deploy recovery behavior after stale bundle mismatch or generic runtime errors |
| `device-mqtt-credential-rotate.spec.js` | Machine detail MQTT rotate/revoke lifecycle and post-onboarding status presentation |
| `device-onboard-generated-id.spec.js` | Device onboarding flow generates and displays the device ID after create |
| `device-onboard-negative.spec.js` | Onboarding conflict handling, ID allocation failure handling, and required-field validation |
| `device-onboard-no-plants.spec.js` | Device onboarding remains blocked when the org has no plants |
| `journey-happy-path.spec.js` | Happy-path operator journey from sign-in through dashboard validation |
| `machine-activity-history-resilience.spec.js` | Activity-history retry behavior and truthful degradation when the backend really fails |
| `machine-dashboard-bootstrap-recovery.spec.js` | Machine detail bootstrap timeout recovery and unrecoverable failure behavior |
| `machine-recent-telemetry-pagination.spec.js` | Recent telemetry pagination stays correct with buffered live rows |
| `machines-auth-refresh-reconnect.spec.js` | Fleet stream refreshes auth before reconnecting after idle disconnect |
| `machines-empty-tenant-live.spec.js` | Empty-tenant machines page stays connected without false degraded state |
| `machines-empty-tenant-no-flicker.spec.js` | Empty-tenant machines page avoids reconnect flicker during clean stream recycle |
| `machines-reconnect.spec.js` | Machines page reconnects truthfully after device-service restart |
| `maintenance-log.local.spec.js` | Machine maintenance log add/edit/delete and truthful missing-record delete errors |
| `org-admin-invite-lifecycle.spec.js` | Super admin invites an org admin, who accepts the invite and signs in |
| `org-scope-and-rule-variants.spec.js` | Tenant scope enforcement, viewer read-only behavior, plant empty state, and rule-type variant validation |
| `plant-edit-completion.spec.js` | Plant edit persistence and downstream use during onboarding |
| `platform-maintenance-ui.spec.js` | Admin maintenance targeting/suspension handling plus tenant-banner and overlap-error truthfulness |
| `premium-feature-gating.spec.js` | Premium module visibility/blocking in navigation and direct UI access |
| `premium-ops-truthfulness.spec.js` | Analytics, tariff settings, reports, waste analysis, and copilot truthfulness under premium workflows |
| `preprod-scoped-ui-smoke.spec.js` | Org admin, plant manager, operator, and viewer scoped UI smoke behavior |
| `rules-maintenance-phase3.spec.js` | Machine-rule edit/delete/out-of-scope denial and maintenance mutation stability |
| `tenant-role-plant-lifecycle.spec.js` | Role-change relogin effects and plant create/duplicate/deactivate/reactivate lifecycle |

### Artifacts and debugging

- Broad CI artifacts are written under [artifacts/broad-ci-validation](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/artifacts/broad-ci-validation).
- Each suite writes command stdout/stderr logs and a `summary.md` / `report.json`.
- Playwright local artifacts typically appear under `ui-web/test-results`.
- On GitHub Actions, suite artifacts are uploaded with names like `broad-ci-<suite-slug>`.

CI intentionally covers deterministic regression slices and leaves live-stack checks to the pre-production validation path. The exact coverage contract is documented in [docs/ci-validation.md](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/docs/ci-validation.md).

## Object Storage Lifecycle

Where bucket lifecycle is managed outside application code, use the repo-tracked policies under [ops/s3/lifecycle.energy-platform-datasets.json](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ops/s3/lifecycle.energy-platform-datasets.json) and [ops/s3/lifecycle.dashboard-snapshots.json](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ops/s3/lifecycle.dashboard-snapshots.json).

These mirror the in-app retention windows:

- `reports/` and `waste-reports/`: 90 days
- `datasets/`: 30 days
- dashboard snapshots bucket: 1 day

Apply them with your S3-compatible control plane during bucket provisioning so orphaned objects remain bounded even if a row-level cleanup cycle is delayed.

## Device Onboarding

Onboarding is tenant-scoped and auth-aware.

- In normal usage, onboard devices from the UI after logging in.
- If you call the API directly, send a valid JWT in `Authorization: Bearer ...` and the tenant scope expected by the shared auth middleware.
- The middleware reads tenant scope from the authenticated org context, and also supports `X-Tenant-Id` / `X-Target-Tenant-Id` for the appropriate flows.

Device records live in `device-service` and require:

- `device_id`
- `device_name`
- `device_type`
- `data_source_type` set to `metered` or `sensor`
- optional metadata like `manufacturer`, `model`, `location`, `phase_type`, and `metadata_json`

Important behavior:

- `phase_type` is still accepted for backward compatibility.
- Runtime status starts as `stopped`.
- The device becomes `running` only after telemetry or a heartbeat arrives.
- `tenant_id` is assigned from the request context, not from the public create payload.

## Starting the Simulator

Use the simulator control script when you want one container per onboarded device.
It is the preferred path for onboarding demos and multi-device testing.

The script will:

- verify the device exists in `device-service`
- build the simulator image on first use
- attach the container to the compose network
- keep the container restarting with `unless-stopped`

### Start one simulator

```bash
./scripts/simulatorctl.sh start COMPRESSOR-001
```

### Start one simulator with the normal authenticated MQTT path

```bash
./scripts/simulatorctl.sh start AD00000009
```

### Start with a custom tenant

```bash
./scripts/simulatorctl.sh start --tenant-id ORG-123 COMPRESSOR-001
```

### Change the publish interval

```bash
./scripts/simulatorctl.sh start COMPRESSOR-001 2
```

### Other commands

```bash
./scripts/simulatorctl.sh list
./scripts/simulatorctl.sh status COMPRESSOR-001
./scripts/simulatorctl.sh logs COMPRESSOR-001
./scripts/simulatorctl.sh stop COMPRESSOR-001
./scripts/simulatorctl.sh restart COMPRESSOR-001
./scripts/simulatorctl.sh purge
```

Notes:

- If you omit `--tenant-id`, the script defaults to `SH00000001`.
- `--secure` is accepted only as a legacy no-op for older scripts.
- The simulator publishes to a tenant-prefixed topic.
- The data service expects tenant-prefixed telemetry topics.
- You must start the main compose stack first, because the script attaches to the compose network and talks to `device-service`.
- `docker compose down -v --remove-orphans` does not remove these standalone simulator containers. Use `./scripts/simulatorctl.sh purge`, or run `python3 scripts/preprod_validation.py --mode full-reset`, to get a truly clean local reset.

### Optional demo profile

If you just want the single compose-managed demo simulator, the stack also defines a `telemetry-simulator` service under the `demo` profile.

```bash
docker compose --profile demo up -d telemetry-simulator
```

That service uses the default device ID from the environment and is best for quick smoke tests, not for per-device simulation.

## Telemetry Contract

The simulator and firmware should publish JSON telemetry with numeric values only.

MQTT basics:

- Broker host in compose: `localhost` from the host, `emqx` inside containers
- Port: `1883`
- TLS: not used for the device MQTT path
- Username/password: required
- Anonymous MQTT access: denied
- QoS: `1`
- Default subscription pattern in `data-service`: `devices/+/telemetry`
- Tenant-aware subscription also supported: `+/devices/+/telemetry`

Simulator topic format:

```text
<tenant_id>/devices/<device_id>/telemetry
```

Payload requirements:

- `device_id` must match the onboarded device
- `timestamp` must be UTC ISO-8601
- `schema_version` should be `v1`
- all measurement values must be numbers, not strings

Canonical field names:

- `voltage` in volts
- `current` in amperes
- `power` in watts
- `power_factor` from `0.0` to `1.0`
- `energy_kwh` as a cumulative meter reading when available

Common aliases accepted by the backend:

- current: `current_l1`, `current_l2`, `current_l3`, `phase_current`, `i_l1`
- voltage: `voltage_l1`, `voltage_l2`, `voltage_l3`, `v_l1`
- power factor: `pf`
- power: `active_power`, `kw`

Example payload:

```json
{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-04T12:00:00Z",
  "schema_version": "v1",
  "voltage": 231.4,
  "current": 0.86,
  "power": 198.7,
  "power_factor": 0.98,
  "energy_kwh": 1245.337
}
```

## Simulator Reliability

The device simulator is designed to survive transient broker or session issues:

- reconnects with exponential backoff and jitter
- buffers telemetry while MQTT is unavailable
- flushes buffered messages after reconnect
- sends fallback heartbeats to `device-service` so runtime status can remain `running` while the simulator process is alive

## Verification

Useful checks after onboarding or telemetry changes:

```bash
./scripts/report_shift_overlap_conflicts.sh
```

For a manual telemetry smoke test, publish one sample message to the same topic your simulator uses with a valid per-device MQTT username/password:

```bash
mosquitto_pub -h localhost -p 1883 \
  -u 'device:SH00000001:COMPRESSOR-001' \
  -P '<one-time-device-password>' \
  -t SH00000001/devices/COMPRESSOR-001/telemetry -m '{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-04T12:00:00Z",
  "schema_version": "v1",
  "voltage": 230.8,
  "current": 0.88,
  "power": 203.0,
  "power_factor": 0.98
}'
```

After that, check the UI or your authenticated API client to confirm the device card and telemetry views update.

For the UI, run the hook-order and production build checks before shipping changes:

```bash
cd ui-web
npm run lint:hooks
npm run build
```

## Safety Notes

- Do not run `docker compose down -v` unless you intentionally want to remove the named volumes.
- The default stack uses persistent volumes for MySQL, InfluxDB, and MinIO.
- The optional `monitoring` profile adds persistent volumes for Prometheus, Alertmanager, and Grafana.
- If you change secrets or service URLs, keep `.env` and `docker-compose.yml` aligned.
- Migrations run automatically in the services that manage Alembic on startup.






#Simulator 
cd ~/Shivex-main-1/tools/device-simulator

nohup python3 main.py \
  --device-id VD00000003 \
  --tenant-id SH00000001 \
  --broker shivex.ai \
  --port 1883 \
  --mqtt-username 'device:SH00000001:VD00000003' \
  --mqtt-password 't0aV3L6hWNcH5Pm7cytzM1VRjro_lWEtZVBuYd4kkis' \
  > simulator-vd00000003.log 2>&1 < /dev/null &
