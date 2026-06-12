# Shivex Scaling Rollout Plan

This document translates the current Shivex scaling assessment into an operator-facing rollout plan for roughly:

- Stage 1: around `100` devices
- Stage 2: around `500` devices
- Stage 3: `1000+` devices

It is grounded in:

- current compose topology
- current queue and backpressure behavior in code
- `SCALING_READINESS.md`
- `memory.md`

It is intentionally conservative. It does not promise fixed throughput numbers. It is meant to help operators decide when Shivex can stay on its current shape, when infrastructure should be strengthened, and when code/config changes might become justified later.

## Current Baseline

The current production-like Shivex topology already includes:

- `2` dedicated telemetry worker containers
- `2` analytics worker containers with `MAX_CONCURRENT_JOBS=3`
- `1` reporting worker container with `REPORT_WORKER_CONCURRENCY=2`
- Redis-backed durable streams for telemetry, analytics, and reporting
- explicit queue/backpressure protections in `data-service`, `analytics-service`, and `reporting-service`

Current code-backed overload thresholds that matter operationally:

- `data-service`
  - telemetry health lag overload: `90s`
  - projection overload threshold: `20000`
  - outbox overload threshold: `10000`
  - ingest reject threshold: `200000`
- `analytics-service`
  - queue backlog reject threshold: `500`
  - tenant max queued jobs: `25`
  - tenant max active jobs: `8`
- `reporting-service`
  - report queue reject threshold: `5000`
  - tenant max pending jobs: `25`
  - tenant max active jobs: `4`

These are useful watchpoints, but they are not performance guarantees.

## Stage 1: Around 100 Devices

### Target posture

This stage should be treated as the first serious production rollout band, not as a toy environment. The current codebase and worker split are already strong enough that this stage is primarily an infrastructure hygiene and observability problem, not a rewrite problem.

### Infra shape

- A single-node or small multi-service deployment can still be reasonable.
- Redis can still be colocated with the main deployment if the host has healthy memory headroom and persistence latency is under control.
- MySQL can remain modest if it is provisioned for consistent latency, not burst luck.
- InfluxDB and object storage should already be treated as production dependencies with backups and health alerting.

### Redis guidance

- Do not run Redis as an afterthought. Even at this stage it is shared critical infrastructure for:
  - telemetry stage streams
  - analytics queue
  - reporting queue
  - auth token state
  - live/fleet channels
- Keep `noeviction` unless there is a deliberate and accepted queue-loss strategy.
- Do not keep the shared production Redis default at `512mb` on the current all-in-one 8 GB server shape.
- Use `REDIS_MAXMEMORY=1gb` as the safer starting production default on that host size and keep `REDIS_MAXMEMORY_POLICY=noeviction`.
- If Redis memory usage regularly trends high or persistence stalls appear, increase Redis memory before changing worker counts.

### RDS / MySQL guidance

- Shivex currently depends on MySQL for auth, devices, jobs, outbox, checkpoints, and metadata-heavy coordination.
- For this stage, a modest but production-grade MySQL or RDS instance is more important than raw size.
- Priorities:
  - stable IOPS
  - connection visibility
  - backups and point-in-time recovery
  - alerting on CPU, memory, storage, and connection pressure

### Worker / service scaling priorities

Keep the current code path and baseline worker topology unless metrics show stress:

- keep telemetry workers at the current `2` containers
- keep analytics workers at the current `2` containers
- keep reporting worker at the current `1` container
- keep API `uvicorn` worker envs at `1` until a specific API shows CPU saturation or request backlog under real traffic
- when API concurrency does need to increase, raise only the bottlenecked API’s env knob:
  - `DEVICE_SERVICE_UVICORN_WORKERS`
  - `AUTH_SERVICE_UVICORN_WORKERS`
  - `ENERGY_SERVICE_UVICORN_WORKERS`
  - `ANALYTICS_API_UVICORN_WORKERS`
  - `REPORTING_SERVICE_UVICORN_WORKERS`
  - `WASTE_ANALYSIS_SERVICE_UVICORN_WORKERS`
- keep `DEBUGPY_ENABLE=false` anywhere those API worker counts are set above `1`

If one area needs help first, prefer:

1. Redis memory/headroom
2. host CPU/RAM
3. telemetry worker capacity

Do not scale copilot first unless actual human usage justifies it.

### What can stay unchanged

- current telemetry durable pipeline shape
- analytics queue admission and duplicate-job reuse
- reporting queue admission and duplicate-report reuse
- export trigger behavior
- copilot request flow

### What must be monitored

- Redis memory usage
- telemetry projection backlog
- telemetry oldest-stage age
- outbox pending count
- analytics queued job count
- reporting queue depth
- MySQL latency / connections
- InfluxDB write health

### Alert thresholds / watch metrics

Safe watchpoints at this stage:

- alert if telemetry health begins spending time near the `90s` overload lag threshold
- alert if projection backlog shows sustained growth instead of drain
- alert if outbox pending count trends toward the `10000` overload threshold
- alert if analytics queue backlog approaches the `500` reject threshold
- alert if reporting queue depth rises steadily toward the `5000` reject threshold

Do not wait for hard thresholds to trip repeatedly before acting. The trend matters more than one spike.

### Promotion checklist to leave Stage 1

- Redis memory and latency remain stable during realistic peak windows
- telemetry projection backlog drains after bursts instead of ratcheting upward
- outbox pending counts recover after downstream slowness
- analytics and reporting queues stay comfortably below reject thresholds during normal usage
- MySQL and InfluxDB show stable latency under daily peaks

## Stage 2: Around 500 Devices

### Target posture

This stage should be treated as infrastructure-first scaling. Repo memory already warns not to overclaim upper-range readiness from a small single-node environment. The first real sensitivity remains `data-service` projection, not copilot or generic HTTP throughput.

### Infra shape

- Move away from treating the current all-in-one host as the long-term plan.
- Separate or strongly isolate Redis from general service contention.
- Ensure MySQL/RDS, InfluxDB, and object storage have enough headroom for sustained daily load, not just functional correctness.
- If still running on a single Docker host, increase host RAM/CPU before raising worker counts aggressively.

### Redis guidance

- Redis should now be managed as a primary platform dependency.
- Recommended posture:
  - more memory than the current minimal default
  - explicit persistence validation
  - restart and recovery drills
  - monitoring for stream depths, pending messages, and command latency
- If Redis shows memory pressure, fix Redis capacity before widening worker concurrency.

### RDS / MySQL guidance

- Treat MySQL/RDS as a real control-plane dependency now, not just a backing store.
- Priorities:
  - stronger instance class than the minimal production baseline
  - read/write latency visibility
  - clear limits on connections and pool pressure
  - backup restore verification
- Watch for pressure from:
  - auth
  - device metadata lookups
  - analytics job rows
  - reporting job rows
  - data-export checkpoints
  - telemetry outbox / DLQ metadata

### Worker / service scaling priorities

Priority order remains:

1. telemetry path
2. Redis
3. downstream projection consumers
4. reporting
5. analytics

Practical scaling actions at this stage:

- consider adding another telemetry worker container before touching analytics worker concurrency
- scale reporting workers before allowing reporting demand to build queue latency
- only increase analytics worker count after verifying that export readiness and dataset generation are not the dominant limiter

Do not raise all worker counts at once. Scale the bottlenecked path, then re-measure.

### What can stay unchanged

- analytics queue caps and admission policy
- reporting queue caps and admission policy
- dedup logic for analytics and reporting
- app-level rate limits added in prior hardening phases

### What must be monitored

- telemetry projection backlog and lag trend
- outbox pending trend
- Redis memory and persistence latency
- analytics queue depth and rejection events
- reporting queue depth and worker completion times
- exact-range export completion time
- device-service and energy-service latency under telemetry pressure

### Alert thresholds / watch metrics

Responsible operator watchpoints:

- projection backlog spending meaningful time in the low-thousands is a warning, not an automatic failure, but the trend should be reviewed
- repeated approach toward `20000` projection overload threshold should block further rollout
- outbox pending count climbing without recovery should block further rollout even if hard overload is not yet reached
- repeated analytics queue overload responses at or near the `500` reject threshold should pause analytics-demand growth
- repeated report queue growth toward `5000` should trigger reporting-worker scaling before broader rollout

### Promotion checklist to leave Stage 2

- telemetry bursts recover cleanly without persistent projection lag growth
- Redis has clear memory headroom during peak periods
- MySQL/RDS and InfluxDB remain stable during the same windows
- reporting queue latency is acceptable with current report demand
- analytics queue rejection is rare and explainable, not routine
- export readiness does not become the dominant cause of analytics latency

## Stage 3: 1000+ Devices

### Target posture

This stage should be treated as a production topology exercise, not just a “turn up replicas” step. Shivex should only enter this stage after the 500-device band is already stable and observable.

### Infra shape

- Redis should be treated as dedicated shared coordination infrastructure.
- MySQL/RDS should be sized as a multi-service control-plane dependency.
- InfluxDB should be sized for sustained ingest, query fan-out, and retention behavior.
- Object storage and network path for exports/results should be watched as real analytics dependencies.
- Telemetry, analytics, and reporting workers should be scaled based on measured bottlenecks, not evenly.

### Redis guidance

- Do not rely on minimal-memory defaults.
- Redis capacity planning should explicitly include:
  - telemetry stream retention pressure
  - analytics/reporting queue depth headroom
  - pending message recovery behavior
  - auth/session state
  - pub/sub or live stream traffic
- If Redis becomes noisy or memory-constrained, rollout should pause before broader feature expansion.

### RDS / MySQL guidance

- At this stage, MySQL/RDS sizing should be reviewed alongside:
  - job table growth
  - report history growth
  - checkpoint growth
  - auth/device metadata traffic
  - background worker claim/update churn
- Operators should have explicit dashboards for:
  - CPU
  - connection saturation
  - read/write latency
  - storage growth
  - slow query rate

### Worker / service scaling priorities

Recommended priority order still starts with telemetry:

1. telemetry workers and downstream projection capacity
2. Redis
3. device-service and energy-service
4. reporting workers
5. analytics workers
6. export-specific support if analytics readiness becomes a dominant cost

At this stage, adding telemetry capacity is more defensible than increasing analytics ML parallelism first.

### What can stay unchanged

Potentially still unchanged if metrics stay healthy:

- core telemetry queue architecture
- analytics duplicate reuse behavior
- reporting duplicate reuse behavior
- queue reject/cap patterns
- copilot shape and rate limits

This stage does not automatically require a rewrite. It requires proof that the current architecture holds under a production-like topology.

### What must be monitored

- every Stage 2 metric, plus
- Redis recovery behavior after maintenance/restart
- telemetry rejection counts
- DLQ retryable backlog
- dead-letter growth across analytics and reporting
- export timeout rate
- projection and outbox recovery time after bursts

### Alert thresholds / watch metrics

Use the code-backed thresholds as hard guardrails, but operate earlier:

- treat repeated approach to projection overload (`20000`) as a stop signal
- treat repeated approach to outbox overload (`10000`) as a stop signal
- treat repeated analytics backlog overload responses near `500` as a stop signal
- treat repeated report queue backlog expansion near `5000` as a stop signal
- treat growing dead-letter inventories as readiness blockers, not cleanup chores

### Promotion checklist inside Stage 3

Before saying Shivex is healthy at `1000+`, operators should be able to show:

- stable Redis headroom under peak load
- stable telemetry projection drain behavior after bursts
- controlled outbox pending counts
- stable MySQL/RDS and InfluxDB latency
- reporting queue latency that does not steadily worsen under normal usage
- analytics queue behavior where rejections are rare and bounded
- export readiness that does not dominate analytics turnaround time

## No-Code Infra Changes

These are the first recommended actions because they do not require backend redesign:

- increase host or cluster CPU/RAM before raising worker counts broadly
- increase Redis memory and validate persistence behavior
- move Redis out of a fragile shared-host posture when sustained load justifies it
- strengthen MySQL/RDS instance sizing and observability
- strengthen InfluxDB sizing and retention planning
- add worker replicas selectively where measured bottlenecks exist
- improve dashboards and alerts before larger customer/device rollout

## Code / Config Changes Only If Later Needed

These should be considered only after infra evidence shows a real need:

- additional telemetry worker replicas beyond the current baseline
- higher reporting worker count or concurrency
- higher analytics worker count
- further tuning of queue reject thresholds after production observation
- cross-instance in-flight export suppression if exact-range export duplication becomes a demonstrated issue
- any deeper projection/outbox optimization if infrastructure-first scaling still leaves `data-service` as the clear bottleneck

Do not do these preemptively without measurements.

## Operator Checklist Before Moving Between Stages

### Before moving to ~100 devices

- production monitoring exists for Redis, MySQL/RDS, InfluxDB, telemetry lag, analytics queue, and reporting queue
- backups and restore paths are verified
- current worker topology is healthy

### Before moving to ~500 devices

- Stage 1 has stable daily peak behavior
- Redis has real headroom
- projection backlog and outbox pending counts recover after bursts
- no routine analytics or reporting queue overload events

### Before moving to 1000+ devices

- Stage 2 is stable on production-like infrastructure
- telemetry path, Redis, and downstream services have been re-measured after scaling
- reporting and analytics worker capacity has been tuned based on real queue behavior
- operators can prove that backlog, lag, and dead-letter growth are controlled rather than merely tolerated

## Final Operator Recommendation

For Shivex, the rollout path to `100 -> 500 -> 1000+` should be infrastructure-first and telemetry-first.

The main lesson from the current codebase and repo memory is:

- do not start by scaling copilot or generic API replicas
- do not start by raising every worker count at once
- do start with Redis headroom, telemetry worker/projection capacity, and downstream stability

If operators follow that order, Shivex can grow without broad backend rewrites. If they skip that order, they are likely to stress the wrong parts of the system first and misread the resulting failures.
