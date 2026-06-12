# Shivex Test Instance Pipeline

## Goal

- Build a disposable EC2 test instance for hard validation without production RDS/S3 risk.
- Use a server-local `.env.test-instance` derived from `.env.local`.
- Validate worst-case multi-org concurrent load for:
  - reports
  - analytics
  - waste analysis
  - rules/alerts

## Environment Rules

- Do not use production RDS credentials.
- Do not use production S3 credentials.
- Prefer Docker MySQL + MinIO via local-style compose.
- Only change server-specific public URL/IP values in the test env file.

## Load Goal

- Target: `50-100` active device-equivalents across multiple orgs.
- Emphasis: concurrent org activity, not only single-tenant device throughput.

## Validation Phases

1. Instance bootstrap
2. Docker + compose readiness
3. Repo clone / pull
4. `.env.test-instance` creation from `.env.local`
5. Stack bring-up with local Docker infra
6. Baseline health sweep
7. Test tenant/device seeding
8. Controlled ramp load
9. Concurrent feature-pressure scenarios
10. Recovery / cleanup

## Hard-Test Scenarios

- multiple org creation and tenant isolation checks
- concurrent device onboarding
- simulator fan-out across multiple orgs
- concurrent report generation
- concurrent analytics jobs
- concurrent waste-analysis jobs
- rules trigger / alert path under load
- runtime recovery after stopping synthetic load

## Success Criteria

- all core services remain `Up`
- no important service stuck in `Created`
- health endpoints remain healthy
- backlog remains bounded and recoverable
- outbox pending/failed does not grow uncontrollably
- feature APIs complete across multiple orgs without tenant leakage

## Key Watch Metrics

- `docker compose ps --all`
- service health endpoints
- `docker stats --no-stream`
- data-service backlog / outbox / DLQ
- device-service CPU / responsiveness
- analytics and waste job progression
- rule-engine alert delivery health

## Notes

- GHCR is the preferred deploy path.
- `docker-compose.server-build.yml` remains the fallback if GHCR is unavailable.
- The test instance should be treated as disposable and resettable.
- All hard testing should run on the disposable server instance.
- The repo now includes a local k6 HTTP toolkit at `tests/load/k6/`.
- The k6 runner now treats `tests/load/k6/.env` as defaults only, so inline shell env overrides win during server test runs.
- Load generation is intentionally split:
  - telemetry simulator remains responsible for MQTT/device pressure
  - k6 adds concurrent authenticated HTTP/API pressure for analytics, reports, waste, and rules/alerts
- For disposable-server backend load validation, prefer k6 `direct` mode to service ports instead of `ui-web` proxy mode so backend bottlenecks are measured without rewrite-layer ambiguity.
- Intended later execution model on the disposable server:
  - bring the stack up with local-style infra
  - establish a stable `50`-simulator telemetry baseline first
  - run the k6 `mixed` scenario on top of that baseline to measure latency, p95/p99, error rate, throughput, and endpoint-specific failures under concurrent platform activity
- Final validated mixed-load result on the disposable server:
  - with `50` telemetry simulators plus k6 `mixed` direct-mode at `4` VUs for `10m`, the backend stayed functionally stable and the corrected harness passed the failure-rate threshold:
    - `http_req_failed = 1.22%` (`PASS`)
    - `p95 = 5674ms` (`FAIL` vs 2500ms target)
    - `p99 = 8313ms` (`FAIL` vs 5000ms target)
  - primary limiting component remained the `data-service` projection stage:
    - projection backlog spiked from roughly `88` to roughly `4961`
    - projection workers saturated at `100` inflight each
  - conclusion:
    - current system behavior is functionally stable under this combined test
    - current latency SLA is not met on the tested single-node disposable topology
    - next work should shift from broader load escalation to projection/outbox optimization and production-like topology validation
- Important test-truth note:
  - early k6 failures around `68-70%` were not valid capacity conclusions
  - root causes were:
    - missing tenant premium entitlements (`analytics`, `reports`, `waste_analysis`)
    - super-admin auth context not being tenant-scoped for `/auth/me`-driven k6 discovery
  - corrected test execution must use:
    - tenant-scoped org-admin credentials
    - granted premium entitlements on load-test tenants
    - direct-mode k6 for backend measurement on the disposable server
- If a defect requires code changes:
  - make the change locally first
  - validate locally first
  - record the reason and change scope in this file
  - only then mirror the same validated change onto the test server
  - retest on the server after the mirrored change

## Phase 2–3 Capacity Engineering Changes

- Projection path (Phase 2, local-approved):
  - per-tenant in-process lock in telemetry_pipeline worker to eliminate same-process same-tenant concurrent projection
  - semaphore raised 4→12, chunk/batch sizes reduced 100→50, HTTP pool raised 120→200
  - savepoint fallback Counter added to device-service monitoring
  - recent telemetry overflow cleanup moved to supplemental background scheduler; immediate 200-row bound preserved inline
- Outbox/retry path (Phase 3, local-implementation):
  - device power-config TTL cache (300s) added to outbox_relay to break feedback loop to device-service
  - retry backoff base decoupled from poll interval: new `outbox_retry_backoff_base_seconds=5` (was misusing `outbox_poll_interval_sec=0.5` which forced base=1s)
  - outbox relay circuit breakers now use `half_open_max_calls=3` (was default 1), allowing faster recovery from transient outages
- Layer 2 Redis tenant lock (Phase 3 continued, local-implementation):
  - `RedisTenantLock` added to `tenant_lock.py` using `SET NX PX` + Lua compare-and-delete release for cross-process per-tenant serialization
  - lock key format: `shivex:tenant_lock:projection:{tenant_id}` — tenant-isolated, no cross-tenant leakage
  - lock value: `{hostname}:{pid}:{uuid4[:8]}:{uuid4[:8]}` — unique per acquire, enables safe release
  - TTL default 30s with auto-expiry on crash; acquisition timeout default 15s with exponential backoff polling (50ms–500ms)
  - `create_tenant_lock` factory selects `InProcessTenantLock` (default) or `RedisTenantLock` based on `tenant_lock_provider` setting
  - `TenantLockTimeoutError` handled as `downstream_overload` defer-class transient in `_handle_projection_batch` — not a permanent failure
  - new settings: `tenant_lock_provider=in_process`, `tenant_lock_redis_ttl_seconds=30`, `tenant_lock_redis_acquire_timeout_seconds=15.0`
  - new metrics: `projection_tenant_lock_redis_acquire_duration_seconds` (Histogram by outcome), reuse existing `projection_tenant_lock_acquire_total` and `projection_tenant_lock_active`
  - docker-compose.yml wired with `TENANT_LOCK_PROVIDER=in_process` on both worker containers (safe default, Redis mode not yet enabled)
  - `with_for_update()` removed from device-service `live_projection.py` batch projection SELECT — server-validated in V6 (Redis lock provides cross-process serialization)
  - batch projection write path now uses a version-guarded compare-and-swap update instead of a blind in-session overwrite:
    - keeps Redis tenant lock as the primary cross-process serializer
    - adds defense in depth if an unexpected concurrent writer slips through
    - surfaces conflicts via `device_live_update_batch_version_conflict_total` instead of silently doing last-writer-wins
    - chunk-level savepoint fallback remains the recovery path for rare conflicts
  - V6 server validation (Redis lock + `with_for_update()` removal): projection backlog 0, outbox failed 0, zero lock timeouts, http_req_failed 4.62%, p95 3.87s, p99 7.74s — PARTIAL PASS
  - V6 remaining k6 check failures (`GET history` 59%, `POST run` 55%) traced to k6 harness bug, not platform: waste-analysis URLs in `workloads.js` used `/api/waste/analysis/...` but service mounts at `/api/v1/waste/analysis/...`, causing 404s that pollute aggregated check names — fix applied locally
2026-05-10: Added analytics duplicate-job protection as the next permanent backend fix for remaining POST /api/v1/analytics/run failures. The route now reuses an existing pending/running job for the same tenant+device+analysis_type+model_name+time-range before tenant admission is enforced; added repository contract/implementation, dedup lookup index, and unit coverage.
2026-05-10: Server validation required one analytics dedup-key correction. Exact date-range matching prevented duplicate reuse because mixed-load requests had different millisecond timestamps. Reduced the dedup key to tenant_id + device_id + analysis_type + model_name + pending/running status, rebuilt analytics service/workers, cleared 25 stale queued jobs from the k6 tenant, and revalidated at 0.00% http_req_failed / 100% checks_succeeded with POST run restored to 100%.
