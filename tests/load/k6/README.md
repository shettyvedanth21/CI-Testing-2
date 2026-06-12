# Shivex k6 HTTP Load Toolkit

This toolkit adds reusable k6 coverage for tenant-scoped HTTP pressure on top of the existing MQTT telemetry simulator. It is designed for long-run validation against a disposable server, not production.

## What it targets

- Auth flow used by protected APIs:
  - `POST /api/v1/auth/login`
  - `POST /api/v1/auth/refresh`
  - `GET /api/v1/auth/me`
- Analytics service:
  - `GET /api/v1/analytics/models`
  - `POST /api/v1/analytics/preflight`
  - `POST /api/v1/analytics/run`
  - `POST /api/v1/analytics/run-fleet`
  - `GET /api/v1/analytics/status/{job_id}`
  - `GET /api/v1/analytics/results/{job_id}`
  - `GET /api/v1/analytics/jobs`
- Reporting service:
  - `POST /api/reports/energy/consumption`
  - `GET /api/reports/history`
  - `GET /api/reports/{report_id}/status`
  - `GET /api/reports/{report_id}/result`
  - `GET /api/reports/{report_id}/download`
  - `POST /api/reports/schedules`
  - `GET /api/reports/schedules`
  - `DELETE /api/reports/schedules/{schedule_id}`
- Waste analysis service:
  - `POST /api/waste/analysis/run`
  - `GET /api/waste/analysis/history`
  - `GET /api/waste/analysis/{job_id}/status`
  - `GET /api/waste/analysis/{job_id}/result`
  - `GET /api/waste/analysis/{job_id}/download`
  - `GET /api/waste/analysis/{job_id}/file`
- Rules and alerts service:
  - `GET /api/v1/rules`
  - `GET /api/v1/rules/{rule_id}`
  - `POST /api/v1/rules`
  - `PATCH /api/v1/rules/{rule_id}/status`
  - `DELETE /api/v1/rules/{rule_id}`
  - `GET /api/v1/alerts`
  - `GET /api/v1/alerts/events`
  - `GET /api/v1/alerts/events/unread-count`
  - `GET /api/v1/alerts/events/summary`

## Layout

- `lib/config.js`: env parsing, URL construction, shared k6 options, thresholds
- `lib/http.js`: JSON/raw request wrappers plus endpoint failure metric
- `lib/auth.js`: login, refresh-cookie reuse, bearer header construction
- `lib/discovery.js`: tenant, plant, and device discovery from `/auth/me` and `/devices`
- `lib/workloads.js`: reusable domain flows used by all scenarios
- `scenarios/*.js`: runnable k6 entrypoints
- `run-k6.sh`: local runner wrapper

## Environment model

Copy `.env.example` to `.env` in this folder and fill in credentials plus scope.
The runner treats `.env` as defaults only, so explicit shell/CLI environment variables take precedence.

Mode guidance:

- `proxy` mode:
  - use when you intentionally want to validate the `ui-web` rewrite/gateway behavior
  - useful for browser-contract checks
- `direct` mode:
  - preferred for disposable-server backend load validation
  - avoids conflating backend bottlenecks with UI rewrite behavior

Core variables:

- `K6_ROUTE_MODE=proxy|direct`
- `K6_BASE_URL` for proxy mode
- `K6_LOGIN_EMAIL`
- `K6_LOGIN_PASSWORD`
- `K6_TENANT_ID`
- `K6_PLANT_ID`
- `K6_DURATION`
- `K6_VUS` or `K6_RATE`
- `K6_STRICT_FEATURE_PREFLIGHT=true|false`

Optional discovery overrides:

- `K6_DEVICE_IDS=device-a,device-b`
- `K6_SELECTED_DEVICE_COUNT=3`

Direct-mode service URLs:

- `K6_AUTH_BASE_URL`
- `K6_DEVICE_BASE_URL`
- `K6_ANALYTICS_BASE_URL`
- `K6_REPORTING_BASE_URL`
- `K6_WASTE_BASE_URL`
- `K6_RULES_BASE_URL`

Proxy mode assumes the repo’s `ui-web` rewrite contracts:

- auth via `/backend/auth`
- device via `/backend/device`
- analytics via `/backend/analytics`
- rules via `/backend/rule-engine`
- reporting via `/api/reports`
- waste via `/api/waste`

For disposable-server runs, prefer:

```bash
cp tests/load/k6/.env.server-direct.example tests/load/k6/.env
```

## Scenarios

- `analytics`: model lookup, telemetry preflight, async analytics submission, status polling, result fetch
- `reports`: report history read, consumption report creation, async polling, result and download contract check
- `waste`: waste history read, async waste job creation, status polling, result and download contract check
- `rules-alerts`: rules CRUD pressure plus alert and activity read paths
- `mixed`: weighted mix of analytics, reports, waste, and rules/alerts flows

The mixed scenario is the intended later companion to a stable `50`-simulator telemetry baseline. Start modestly and ramp HTTP pressure without changing MQTT volume during the same run.

## Running

Proxy mode against a local UI gateway:

```bash
cd /Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main
cp tests/load/k6/.env.example tests/load/k6/.env
tests/load/k6/run-k6.sh mixed
```

Direct mode against service ports:

```bash
cd /Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main
K6_ROUTE_MODE=direct \
K6_AUTH_BASE_URL=http://localhost:8090 \
K6_DEVICE_BASE_URL=http://localhost:8000 \
K6_ANALYTICS_BASE_URL=http://localhost:8003 \
K6_REPORTING_BASE_URL=http://localhost:8085 \
K6_WASTE_BASE_URL=http://localhost:8087 \
K6_RULES_BASE_URL=http://localhost:8002 \
tests/load/k6/run-k6.sh reports
```

Arrival-rate example for the disposable test server:

```bash
K6_SCENARIO=mixed \
K6_EXECUTOR=constant-arrival-rate \
K6_RATE=6 \
K6_TIME_UNIT=1s \
K6_PREALLOCATED_VUS=12 \
K6_MAX_VUS=40 \
K6_DURATION=30m \
tests/load/k6/run-k6.sh
```

Recommended first disposable-server smoke run:

```bash
cp tests/load/k6/.env.server-direct.example tests/load/k6/.env
# Fill login credentials and tenant scope, then:
tests/load/k6/run-k6.sh mixed
```

If you want to override values from `.env` for a single run, pass them inline:

```bash
K6_SCENARIO=mixed K6_DURATION=3m K6_VUS=4 tests/load/k6/run-k6.sh
```

## Metrics and tags

All requests carry tags for:

- `suite=shivex-k6`
- `scenario`
- `domain`
- `endpoint`
- `route_mode`
- `name`

Built-in thresholds:

- `http_req_failed`
- `http_req_duration` with explicit `p95` and `p99` guards

Custom metric:

- `endpoint_failures`

This is intended to make Grafana and Prometheus correlation easier when telemetry simulators are already active.

## Entitlement preflight

By default, the toolkit checks `/api/v1/auth/me` and fails fast if the selected tenant lacks the premium features needed for the chosen scenario.

Examples:

- `analytics` requires `analytics`
- `reports` requires `reports`
- `waste` requires `waste_analysis`
- `rules-alerts` requires `rules`
- `mixed` requires all of the above

This avoids misreading feature-gate rejections as load-test failures.

## Long-run guidance

- Keep MQTT load stable first. The current known telemetry ceiling remains:
  - `50` clean
  - `60` functional but not clean
  - `100` not safe
- Add k6 on top of the clean `50` baseline first.
- For disposable-server backend load validation, prefer `direct` mode over `proxy` mode.
- Prefer the `mixed` scenario for server validation because it better represents concurrent platform behavior than isolated endpoint loops.
- Leave `K6_FETCH_ARTIFACTS=false` unless you explicitly want PDF/file transfer pressure included in the run.

## Validation

If `k6` is present, validate each scenario with `k6 archive` or `k6 run`.

If `k6` is not present, you can still do a basic syntax pass locally:

```bash
node --check tests/load/k6/lib/config.js
node --check tests/load/k6/lib/http.js
node --check tests/load/k6/lib/auth.js
node --check tests/load/k6/lib/discovery.js
node --check tests/load/k6/lib/workloads.js
node --check tests/load/k6/scenarios/analytics.js
node --check tests/load/k6/scenarios/reports.js
node --check tests/load/k6/scenarios/waste.js
node --check tests/load/k6/scenarios/rules-alerts.js
node --check tests/load/k6/scenarios/mixed.js
```
