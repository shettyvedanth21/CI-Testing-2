# Pending Implementation Work

This file is the detailed source of truth for what is still missing, what is already strong, and what should be fixed first before larger SaaS rollout pressure lands on the platform.

This is not a brainstorming note.
This is not a future-wishlist file.
This is the current gap log based on the repository state, rollout history, validation work, and production behavior observed so far.

## Active Branch Rule

As of now, all active recovery and permanent-fix work must happen only on:

- branch: `Dev-Testing`
- repo: [Shivex-Main](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main)

The temporary `development-testing` worktree is no longer the working source of truth for forward fixes.
Do not start new recovery work on another branch unless there is an explicit decision recorded here first.

## Current Fix Ledger For Dev-Testing

This section exists so the branch does not drift again and so we do not keep re-arguing whether an item is solved, partially solved, or still pending.

### Solved For Current Branch Scope

These items are considered closed enough for the current `Dev-Testing` release lane:

1. `Org-admin entitlement / auth-state failure`
- Status: `Solved`
- Notes:
  - fixed on branch commit `cd15bca6`
  - scope was the org-admin runtime breakage across reports, settings, analytics, rules, and copilot

2. `Energy Report canonical mismatch correction`
- Status: `Solved`
- Notes:
  - fixed on branch commit `8c9fb04b`
  - Energy Report now uses canonical visible totals by default on this branch

3. `Manual vs system tally for sampled device audit`
- Status: `Solved for sampled validation`
- Notes:
  - manual audit was completed for device `TD00000004`
  - manual vs system output matched closely enough to treat the run as correct
  - this closes the immediate doubt for that audited example

4. `Coarse-counter / reporting telemetry hardening`
- Status: `Solved on current branch scope`
- Notes:
  - fixed on branch commit `12aeb1eb`
  - reporting no longer relies on the old hand-written trapezoid loop for this path
  - reporting now uses bounded shared interval logic with reporting-only overlap subtraction
  - fix is intentionally reporting-bounded; shared-core telemetry behavior was not broadened in this phase

5. `Telemetry/counter regression-test hardening`
- Status: `Solved on current branch scope`
- Notes:
  - reporting telemetry/counter regression coverage was expanded after the runtime hardening work
  - same-day overlap subtraction, mixed-method windows, reset handling, empty/single-sample handling, gap-bound behavior, and defensive timestamp handling are now covered on this branch
  - remaining telemetry work is now deeper financial/runtime hardening, not basic regression coverage

6. `Reporting runtime hardening for H2/H3`
- Status: `Solved on current branch scope`
- Notes:
  - midnight-boundary daily breakdown undercount was fixed in reporting runtime
  - zero-energy all-gap-exceeded reports/days no longer present as healthy/high-confidence
  - uncovered gap-exceeded intervals no longer inflate total_hours/day_hours
  - fix stayed inside reporting runtime and did not broaden into shared telemetry or other services

7. `Cost-drift / tariff-ingestion runtime parity repair`
- Status: `Solved on current branch scope`
- Notes:
  - reporting, energy-service, and dashboard-facing runtime paths now prefer persisted financial semantics where those persisted values already exist
  - today/live overlays now preserve persisted historical cost and apply tariff only to the incremental live delta where applicable
  - this phase closed runtime-visible mismatch risk without introducing schema changes or backfill logic
  - historical persisted-truth backfill is still a separate future lane if required later

8. `Dashboard / report / waste cross-surface cost parity hardening`
- Status: `Solved on current branch scope`
- Notes:
  - monthly calendar today-cost overlay now preserves persisted-plus-delta semantics
  - live dashboard today-cost path now reuses the already-fetched monthly calendar day entry when available
  - fleet loss-view bucket costs now reconcile to persisted total loss cost
  - report summary total_cost now reconciles to summed per-device total_cost values
  - this closed the remaining known runtime parity defects in the current branch scope

### Still Pending As Permanent-Hardening Work

These are real engineering tracks, but they are not the same thing as "the current branch is broken right now."

1. `Historical persisted-truth cost repair / backfill`
- Status: `Implemented but not yet run`
- Why it still matters:
  - the one-time backfill tooling now exists on this branch
  - it still needs a real dry-run, pilot tenant execution, and post-run validation before this lane can be considered operationally closed
- Notes:
  - backfill tooling was added in commit `bc637ff8`
  - this lane remains operationally open until the script is actually exercised against historical rows and validated

2. `Long-horizon automated parity monitoring`
- Status: `Pending future lane`
- Why it still matters:
  - stronger automated parity checks between dashboard, Energy Report, and Waste Report would reduce future trust gaps
  - this is preventative hardening, not proof that the current branch is wrong

## Immediate Next Approved Path

For the current branch, the order of work should remain:

1. validate the committed `Dev-Testing` fixes cleanly
2. deploy only after that validation is accepted
3. then reopen the permanent-hardening tracks one by one:
   - historical persisted-truth cost repair / backfill execution + validation
   - long-horizon automated parity monitoring
   - machine page latency and state-freshness hardening

## Scope Of This File

This file focuses on the parts that still need follow-up after the recent closure work:

- machine page latency and stale-feeling state
- per-tenant queue visibility and operator dashboards
- production read-path and summary/snapshot hardening
- telemetry/accounting hardening tracks that are not part of the current release fix
- infra/runtime hardening for 100 orgs, then 500 orgs

This file does **not** re-open already closed work unless there is a real remaining gap.

## Current Overall Status

### Strong / Already Done

These areas are in good shape relative to the rest of the platform:

- Broad CI suite coverage is fully mapped and currently at `266 / 266 Covered`.
- Local CI mirror and GitHub Actions broad validation are working and are the normal pre-merge gate.
- MQTT device contract has been aligned to the current SaaS lane:
  - publish `telemetry`
  - publish `status`
  - subscribe `cmd`
  - subscribe `config`
  - subscribe `ota`
- Backend debugger rollout is complete and isolated behind `docker-compose.debug.yml`.
- Analytics async processing is already queue/worker based and much stronger than earlier versions.
- Reporting async processing is queue/worker based with per-tenant active caps, enqueue-failure guards, retry durability, and startup recovery — now at the same fairness maturity as analytics.
- Waste analysis has been upgraded to the same queue/worker quality class as analytics/reporting, with per-tenant active caps, enqueue-failure guards, retry durability, and startup recovery.
- Rules/notifications already use outbox + queue + worker delivery instead of inline best-effort delivery.
- Tenant/org scope enforcement is intentionally designed into shared auth and service-side scoping, and there are many tests around cross-tenant denial.

### Still Weak / Still Pending

These are the real remaining gaps:

1. Machine detail page still loads too much expensive data up front in production.
2. Machine state feels stale until manual refresh in some scenarios because the page mixes:
   - heavy bootstrap
   - websocket telemetry
   - current-state polling
   - status stabilization logic
3. Per-tenant queue/worker observability is not yet first-class in Prometheus/Grafana.
4. Production read-path tuning and summary/snapshot usage are not yet strong enough for the long-term 100 -> 500 org growth target.

## Priority View

## Priority 1: Machine Page Latency And State Freshness

### Current State

The machine detail page currently does too much work during first load.

From code in:
- [ui-web/app/(protected)/machines/[deviceId]/page.tsx](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ui-web/app/(protected)/machines/[deviceId]/page.tsx)

the page bootstraps with:

- dashboard bootstrap
- device details fallback
- telemetry rows
- uptime
- shifts
- health configs
- widget config
- current state
- loss stats
- health score
- idle config
- performance trends
- activity history
- maintenance history on demand

And after initial load it also starts:

- websocket telemetry stream
- periodic `current_state` polling
- periodic loss stats polling
- periodic trend refresh logic
- activity history loading

This is why localhost feels acceptable while production feels slower. The page is asking too much from the read path before the operator feels the page is "ready".

### What Is Already Good

- The page already has bootstrap truthfulness and retry behavior.
- There is status-stability logic in:
  - [ui-web/lib/deviceStatus.ts](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ui-web/lib/deviceStatus.ts)
- `last_seen_timestamp` and `data_freshness_ts` already exist in the UI/device model.
- WebSocket telemetry is already present, so the platform is not polling everything blindly.

### What Is Missing

#### Missing 1: Fast first-paint shell

The top of the page should render from a much lighter summary payload than the current dashboard bootstrap.

The page should first show:

- machine name
- device id
- last seen
- current running/stopped/basic operational state
- a small health/uptime summary if already available cheaply

Heavy panels should not block that first operator impression.

#### Missing 2: Split summary from deep hydration

These parts should be treated as secondary hydration, not first-paint blockers:

- performance trends
- detailed loss stats
- maintenance log summary and records
- richer telemetry history sections
- heavy chart hydration

#### Missing 3: Status path is still too entangled

Running/stopped/unknown currently sits inside a mixed model of:

- bootstrap state
- stream updates
- current state polling
- stabilization against transient unknowns

That is why an operator can see:

- device looked running
- manual refresh corrected it

This is not static data, but it is not yet a clean lightweight "truth lane" either.

#### Missing 4: More precomputed summary reads

The device page still leans too much on live/derived computation at open time.

For production scale, more of this should come from:

- snapshot tables
- cached summary rows
- precomputed health/uptime/loss summaries

instead of being recomputed on every page open.

### What Needs To Happen First

1. Define a lightweight machine-summary contract for first paint.
2. Move heavy sections behind secondary hydration.
3. Separate "fast current state / freshness summary" from the full dashboard bootstrap.
4. Reduce the number of required first-load cross-service reads before the page becomes interactive.

### Expected Result

This is the main path to move production machine page open from roughly `7-8s` toward `3-4s` without removing features.

---

## Priority 2: Heavy Async Workloads

### Current State

Analytics, reporting, waste analysis, and rules/notifications are now at the same queue/worker maturity level.

Priority 2 is no longer an active implementation gap. It stays in this file as historical context and as a reminder of the fairness/observability contracts that should not regress.

### Analytics

Analytics is already in comparatively good shape.

Confirmed from code:
- Redis-backed queue
- separate worker containers
- bounded worker concurrency
- tenant active/queued limits
- global active job limits
- stale worker recovery and retention

Relevant code/config:
- [services/analytics-service/src/config/settings.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/analytics-service/src/config/settings.py)
- [services/analytics-service/src/main.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/analytics-service/src/worker_main.py)

Important current knobs:

- `max_concurrent_jobs = 3`
- `global_active_job_limit = 48`
- `tenant_max_queued_jobs = 25`
- `tenant_max_active_jobs = 8`

### Reporting

Reporting is now at the same fairness maturity as analytics.

Confirmed from code:
- Redis-backed queue
- dedicated report worker
- active worker tracking
- queue metrics
- timeout handling
- scheduler
- retention
- per-tenant active job cap (429 rejection when saturated)
- enqueue-failure guard (503 on queue.enqueue failure, truthful `enqueue_failed` status)
- retry durability (enqueue retry BEFORE ack — no message loss on crash)
- startup recovery for `enqueue_failed` rows
- stale `processing` claim recovery (existing) + `enqueue_failed` claim recovery (new)

Relevant code/config:
- [services/reporting-service/src/config.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/reporting-service/src/config.py)
- [services/reporting-service/src/queue/report_queue.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/reporting-service/src/queue/report_queue.py)
- [services/reporting-service/src/workers/report_worker.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/reporting-service/src/workers/report_worker.py)
- [services/reporting-service/src/handlers/energy_reports.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/reporting-service/src/handlers/energy_reports.py)
- [services/reporting-service/src/main.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/reporting-service/src/main.py)

Important current knobs:

- `REPORT_WORKER_CONCURRENCY = 2`
- `REPORT_TENANT_MAX_PENDING_JOBS = 25`
- `REPORT_TENANT_MAX_ACTIVE_JOBS = 4`
- queue depth / pending metrics already exposed internally

Validation: 11/11 queue/worker tests pass, 117/119 full suite (2 pre-existing CSV-path failures unrelated).

### Rules / Notifications

Rules/notifications now has full per-tenant fairness controls and durability hardening.

Confirmed from code:
- durable outbox
- notification queue
- notification worker
- audit ledger
- per-tenant pending cap (429 rejection when saturated)
- global backlog guard (503 when queue overloaded)
- enqueue-failure guard (row requeued for recovery on queue.enqueue failure)
- stale-ATTEMPTED claim recovery (including null processing_started_at)
- retry durability (enqueue retry BEFORE ack — no message loss on crash)
- startup recovery for stale ATTEMPTED rows
- tenant-fair requeue ordering (list_due_queued ordered by tenant_id first, then next_attempt_at)

Relevant code:
- [services/rule-engine-service/app/config.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/rule-engine-service/app/config.py)
- [services/rule-engine-service/app/services/notification_outbox.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/rule-engine-service/app/services/notification_outbox.py)
- [services/rule-engine-service/app/repositories/notification_outbox.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/rule-engine-service/app/repositories/notification_outbox.py)
- [services/rule-engine-service/app/workers/notification_worker.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/rule-engine-service/app/workers/notification_worker.py)
- [services/rule-engine-service/app/queue/notification_queue.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/rule-engine-service/app/queue/notification_queue.py)

Important current knobs:

- `NOTIFICATION_TENANT_MAX_PENDING_NOTIFICATIONS = 50`
- `NOTIFICATION_QUEUE_REJECT_THRESHOLD = 500`
- `NOTIFICATION_OUTBOX_MAX_RETRIES = 4`
- `NOTIFICATION_WORKER_CONCURRENCY = 4`

Validation: 109/109 rule-engine tests pass (14 new fairness+durability tests).

### Waste Analysis

Waste analysis has been upgraded to the same queue/worker quality class as analytics/reporting.

What it now has:

- job timeout
- retention
- quality gate
- concurrency knobs
- UI truthfulness around queued/running/result/download state
- Redis-backed queue (InMemoryWasteQueue for tests)
- dedicated waste worker with claim-based processing
- per-tenant active job cap
- enqueue-failure guard (truthful `enqueue_failed` status)
- retry durability (enqueue retry BEFORE ack — no message loss on crash)
- startup recovery for `enqueue_failed` + stale `running` rows
- stale-`running` claim with worker-lease expiry (not just `processing_started_at`)

Relevant config:
- [services/waste-analysis-service/src/config.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/waste-analysis-service/src/config.py)

Important current knobs:

- `WASTE_JOB_TIMEOUT_SECONDS = 600`
- `WASTE_DEVICE_CONCURRENCY = 16`
- `WASTE_DB_BATCH_SIZE = 500`
- `WASTE_PDF_MAX_DEVICES = 200`
- `WASTE_TENANT_MAX_ACTIVE_JOBS = 4`

Validation: 67/67 tests pass (19 queue/worker tests including 6 new fairness+durability tests).

---

## Priority 3: Tenant Fairness Under Burst

### Current State

Tenant isolation appears intentionally designed, and tenant fairness is now standardized across all four heavy-workload services (analytics, reporting, waste analysis, and rules/notifications).

What is already good:

- shared tenant context exists
- service-side tenant scoping exists
- many tenant isolation tests exist
- direct cross-tenant denial has been a strong theme in the validation work
- analytics, reporting, waste analysis, and rules/notifications all now have explicit per-tenant caps, enqueue-failure guards, retry durability, and startup recovery

What is not equally mature:

- observability into queue health is not yet first-class
- shared fairness module extraction could simplify knob standardization across services

### What Is Missing

#### Missing 1: Per-tenant fairness controls are now standardized

All four heavy-workload services now have explicit tenant pending/active limits.

Remaining fairness work:
- shared fairness module extraction for knob standardization
- operational visibility improvements

#### Missing 2: Clear operator visibility into queue health

For enterprise confidence, you should be able to answer:

- how many jobs are queued
- which tenants are dominating
- whether a tenant is being throttled
- whether workers are saturated

without guessing from logs.

This is now partially addressed: all heavy-workload services expose Prometheus-format `/metrics` with queue depth, dead letter depth, active workers, timeout/retry event Counters. SLO alert rules exist for all services and use window-based `increase()` for event metrics (not cumulative lifetime totals).

Still missing:
- per-tenant breakdown in `/metrics` (analytics has this in the ops/queue-status JSON endpoint, but not in Prometheus metrics)
- Grafana dashboards for the new metrics

#### Missing 3: Strong shared-capacity protection

One org with 200 devices should not make smaller tenants feel slow or starved.

### What Needs To Happen First

1. Keep fairness knobs explicit and environment-tunable across all services.
2. Add per-tenant queue and worker visibility to Prometheus metrics.
3. Build Grafana dashboards for queue depth, worker saturation, retries, timeouts, and tenant pressure.

### Expected Result

Multi-tenant behavior becomes:

- isolated by data
- fair by workload
- predictable under load

---

## Priority 4: Infra And Runtime Hardening

### Current State

The base architecture direction is reasonable:

- RDS
- S3
- Redis
- EMQX
- service split by responsibility

But the platform is not yet fully tuned for the "100 org now, 500 org later" target.

### What Is Missing

#### Missing 1: Better production read-path optimization

Page load speed depends too much on live heavy service hops instead of fast summary/snapshot reads.

#### Missing 2: Better queue/worker observability

The platform should surface:

- queue depth
- active workers
- dead-letter volume
- timeout counts
- backlog trend

as first-class operational signals.

This is now addressed: all heavy-workload services expose Prometheus-format `/metrics` with these signals. SLO alert rules exist for all services in `monitoring/prometheus/rules/queue-worker-slo-alerts.yml`. Event-style metrics (retry, timeout, dead_letter) use Prometheus Counters and window-based `increase()` alerting. Rule-engine worker liveness is now exposed via Redis heartbeats and `rule_engine_active_workers` Gauge.

Still missing:
- per-tenant breakdown in Prometheus metrics (analytics has per-tenant visibility via its ops/queue-status endpoint)
- Grafana dashboards

#### Missing 3: More deliberate service resource separation

Even with a large instance, shared Docker runtime can still cause contention between:

- UI builds
- heavy report jobs
- analytics workers
- telemetry ingest
- EMQX
- Redis

This is now partially addressed: all containers in docker-compose.yml have `deploy.resources.limits.memory` set, and all worker containers have healthchecks.

### What Needs To Happen First

1. ~~Make queue depth / worker utilization visible and monitored.~~ Done — Prometheus metrics + SLO alerts.
2. Tune production around read-heavy machine/dashboard flows, not only background jobs.
3. Treat Redis, MySQL, and Influx latency as release metrics, not only "it works" checks.
4. Continue moving expensive work away from the request path.
5. Add per-tenant breakdown to Prometheus metrics for all services.
6. Build Grafana dashboards for the new metrics.

---

## Specific Gap Assessment Against The Earlier Priority Discussion

### "Is Priority 2 already done?"

Answer:

- `analytics`: yes
- `reporting`: yes (upgraded with tenant active cap, enqueue_failed, retry durability, startup recovery)
- `waste-analysis`: yes (upgraded to full queue/worker with tenant active cap, enqueue_failed, retry durability, startup recovery)
- `rules notifications`: yes (upgraded with tenant pending cap, global backlog guard, stale-ATTEMPTED claim, retry durability, startup recovery, tenant-fair requeue)

Priority 2 is **complete** across all heavy-workload services.

### "What is the biggest real gap today?"

Answer:

Priority 1.

The machine page is still the most visible production weakness for the user.

### "What is the biggest scale gap after that?"

Answer:

Per-tenant queue observability in Prometheus/Grafana, followed by shared fairness module extraction if the repeated service knobs become costly to maintain.

---

## Ordered Work List

## Fix First

1. Machine page first-paint and heavy-panel split
2. Lightweight current-state / freshness summary path for machine detail
3. Reduce expensive first-load derived reads on machine detail

## Fix Second

4. ~~Expose operational queue/worker metrics more clearly across all services~~ Done — Prometheus-format `/metrics` on all services with SLO alerts
5. Consider extracting shared tenant_fairness module once all services have same conceptual knobs

## Fix Third

7. Expand summary/snapshot/cached read strategy for dashboard and machine surfaces
8. Tune production service resources around read-path + job-path contention

## Done

- Upgrade waste-analysis into stronger queue-worker architecture
- Standardize per-tenant fairness controls across analytics, reporting, and waste analysis
- Reporting: per-tenant active cap, enqueue_failed status, retry durability, startup recovery
- Waste: per-tenant active cap, enqueue_failed status, retry durability, startup recovery, stale-claim with worker-lease expiry
- Rules/notifications: per-tenant pending cap, global backlog guard, stale-ATTEMPTED claim recovery, retry durability, startup recovery, tenant-fair requeue ordering, enqueue-failure guard
- Observability: Prometheus-format `/metrics` endpoints on all services (analytics, reporting, waste, rule-engine, data-service, device-service)
- Observability: `prometheus-client` added to all service requirements
- Observability: Event metrics (retry, timeout, dead_letter) are truly event-driven — workers increment Redis counters at event time; API `/metrics` reads and emits as Prometheus `counter` type. No `_last_*` delta-tracking from DB aggregates.
- Observability: Same-process Counters (analytics tenant_cap/overloaded rejections) use `prometheus_client.Counter.inc()` directly at the rejection site — no delta tracking.
- Observability: Alert rules use `increase(metric[5m])` for event Counters — alerts clear when events stop
- Observability: Rule-engine worker liveness uses bounded Redis Sorted Set (`notification_worker_heartbeats` ZSET with ZREMRANGEBYSCORE + ZCARD) instead of unbounded KEYS scan
- Observability: `RuleEngineNoActiveWorkers` alert added
- SLO alerts: `monitoring/prometheus/rules/queue-worker-slo-alerts.yml` — 18 alert rules across analytics, reporting, waste, rule-engine, data-service
- Prometheus config: scrape targets added for all services
- Docker: resource limits (memory) added to all containers
- Docker: healthchecks added/updated for all worker containers (Redis-based for analytics/reporting/waste/rule-engine workers)
- Waste-analysis: `count_by_status()` and `aggregate_runtime_counters()` added to WasteRepository

---

## What Does Not Need Rework Right Now

These do not look like the first thing to change:

- broad CI model
- analytics worker foundation
- reporting worker foundation
- waste-analysis worker foundation
- debugger rollout
- MQTT device contract work

These should stay stable while the remaining production-readiness gaps are addressed.

---

## Final Practical Interpretation

If the platform were frozen today:

- it is already much stronger than an MVP-only system
- it already has real queue/worker foundations in analytics, reporting, waste analysis, and rules/notifications — all with per-tenant fairness
- it already has meaningful tenant isolation patterns
- but it is not yet at the "fully rollout-hardened for 100+ org operator experience" level

The two most important remaining truths are:

1. machine detail reads are still too heavy for production UX
2. per-tenant queue observability in Prometheus is not yet available (only analytics has it via JSON ops endpoint)

That is where the next round of work should begin.
