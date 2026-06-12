# Shivex 1000+ Device Scaling Readiness

This document is a Shivex-specific infrastructure readiness checklist grounded in the current codebase and compose topology as of May 2026. It is intentionally conservative. It does not claim that Shivex has already been proven at 1000+ active devices end to end. It separates:

- protections already present in code
- bottlenecks likely to show up first
- changes that are mainly infrastructure and topology work
- issues that would still need code changes later

## 1. Current Architecture Summary

### Core runtime topology

The current production-like compose topology includes:

- `emqx` for MQTT device ingest
- `data-service` API for MQTT bridge, telemetry APIs, and health/overload reporting
- `data-telemetry-worker` and `data-telemetry-worker-2` for durable telemetry pipeline execution
- `influxdb` for telemetry time-series storage
- `redis` for streams, queues, locks, counters, and pub/sub-style coordination
- `device-service` for device inventory, projections, and live/fleet state
- `energy-service` for energy stream and derived energy views
- `rule-engine-service` and `rule-engine-worker` for alert evaluation and notification delivery
- `analytics-service` API plus `analytics-worker` and `analytics-worker-2` for queued ML/analytics execution
- `reporting-service` API plus `reporting-worker` for queued report generation
- `data-export-service` for continuous and forced export of telemetry to object storage
- `copilot-service` for tenant-scoped AI queries over readonly data paths
- `auth-service` and `ui-web`

### High-level data paths

The main runtime flows relevant to 1000+ readiness are:

1. Device telemetry path:
   `device -> MQTT/EMQX -> data-service -> Redis stage streams -> InfluxDB -> projection/broadcast/energy/rules downstreams`

2. Analytics path:
   `ui/mobile -> analytics-service API -> Redis analytics job stream -> analytics workers -> MySQL/result rows/object storage`

3. Reporting path:
   `ui/mobile -> reporting-service API -> Redis reporting job stream -> reporting worker -> Influx/device/energy/object storage`

4. Export path:
   `continuous worker loop or forced export -> Influx query -> object storage dataset -> analytics readiness polling`

5. Copilot path:
   `ui/mobile -> copilot-service -> readonly MySQL + internal service lookups + external AI provider`

## 2. What Is Already Strong In The Codebase

The codebase already has several scale-relevant protections that reduce the need for major rewrites before the next infrastructure phase.

### Durable queueing and worker separation

- Telemetry stage execution is already separated from the API/MQTT bridge into dedicated data workers.
- Analytics and reporting already use separate API and worker roles.
- Redis streams and consumer groups are already used for durable analytics, reporting, and telemetry stage work.
- API process concurrency is now production-configurable without changing local/dev defaults.
- The API containers can be driven by per-service env knobs:
  - `DEVICE_SERVICE_UVICORN_WORKERS`
  - `AUTH_SERVICE_UVICORN_WORKERS`
  - `ENERGY_SERVICE_UVICORN_WORKERS`
  - `ANALYTICS_API_UVICORN_WORKERS`
  - `REPORTING_SERVICE_UVICORN_WORKERS`
  - `WASTE_ANALYSIS_SERVICE_UVICORN_WORKERS`
- Default remains `1` worker per API container. That is intentional:
  - it preserves current local developer behavior
  - it avoids overcommitting CPU/RAM by default
  - it keeps worker count an operator sizing decision instead of a code guess
- Multi-worker API startup should not be combined with `DEBUGPY_ENABLE=true`; the repo’s shared debug bootstrap uses a single debugpy listener and the startup scripts now guard against that combination.

### Backpressure and overload signaling

- `data-service` already has stage-specific backlog thresholds, stream max lengths, overload thresholds, and explicit ingestion rejection behavior.
- `analytics-service` already rejects new submissions when queue backlog is above the safe threshold and enforces tenant queue and active-job caps.
- `reporting-service` already checks worker availability, queue depth, tenant pending counts, and tenant active job counts before accepting new report work.

### Duplicate-work reduction

- Analytics already reuses active duplicate jobs for the same tenant + device + analysis type + model.
- Reporting consumption and comparison submissions now reuse active duplicates instead of creating redundant work.
- Analytics exact-range export triggering already includes cooldown/suppression behavior so readiness checks do not blindly hammer export submission.

### Retry and dead-letter behavior

- Telemetry stage failures already have DLQ and retry handling.
- Reporting workers already retry transient failures and dead-letter terminal failures.
- Analytics workers already have retry/dead-letter patterns and stale job recovery.
- Reporting has startup recovery for reports previously marked `enqueue_failed`.

### Abuse protection already added

- Copilot, reporting submit routes, analytics expensive routes, and export trigger routes now have app-level rate limits.

## 3. What Is Already Protected In Queue / Backpressure Terms

### `data-service`

Current telemetry/data protections visible in code and compose:

- bounded Redis stream lengths for ingest, projection, broadcast, energy, and rules streams
- ingest rejection threshold at `200000`
- stage backlog thresholds at `200000`
- overload signaling thresholds:
  - projection backlog `20000`
  - energy backlog `5000`
  - rules backlog `10000`
- outbox degraded/overload thresholds:
  - warn `2000`
  - overload `10000`
- DLQ degraded/overload thresholds:
  - retryable warn `500`
  - retryable overload `2000`
- projection defer/backoff logic before terminal failure
- dedicated telemetry workers with internal parallelism:
  - persistence workers `8`
  - projection workers `4`
  - broadcast workers `4`
  - energy workers `6`
  - rules workers `8`

### `analytics-service`

Current analytics protections:

- queue backlog reject threshold `500`
- queue max length `10000`
- tenant max queued jobs `25`
- tenant max active jobs `8`
- global active job limit `48`
- duplicate active-job reuse before queue admission
- stale queued/running job recovery on restart
- exact-range export readiness trigger suppression and bounded polling
- current worker topology:
  - `2` worker containers
  - `MAX_CONCURRENT_JOBS=3` each

### `reporting-service`

Current reporting protections:

- worker-availability check before enqueue
- queue reject threshold `5000`
- tenant max pending jobs `25`
- tenant max active jobs `4`
- duplicate active report reuse for consumption and comparison submissions
- enqueue failure marking and startup recovery
- retry/dead-letter behavior in worker
- current worker topology:
  - `1` reporting worker container
  - `REPORT_WORKER_CONCURRENCY=2`

### `data-export-service`

Current export protections:

- app-level rate limit on forced export endpoint
- tenant/device scoping for export requests
- forced range validation and max window enforcement
- checkpointing to avoid re-export ambiguity in the continuous path
- analytics readiness suppression/cooldown to reduce repeated exact-range trigger spam

### `copilot-service`

Current copilot protections:

- app-level rate limit on chat route
- readonly MySQL access pattern
- provider timeout and row-count guardrails
- no internal queue that can silently grow unbounded in the service itself

## 4. What Is Likely To Become Infra Bottlenecks First

Based on the current code, compose topology, and repo memory, the first likely bottlenecks are not evenly distributed.

### 1. `data-service` projection stage

This is the clearest first bottleneck.

Evidence:

- repo memory explicitly records that the first real scaling bottleneck remained the `data-service` projection stage
- projection backlog was observed rising materially under load while other paths stayed comparatively healthier
- projection is on the hot path between ingestion and downstream live state correctness

Operationally, this means the first 1000+ readiness work should focus on telemetry workers, Redis headroom, and downstream projection write capacity before assuming analytics or copilot are the main blockers.

### 2. `data-service` outbox/downstream retry pressure

The next likely bottleneck after projection is downstream outbox pressure.

Evidence:

- repo memory explicitly calls out outbox/downstream retry pressure as the next follow-on concern
- data-service has dedicated outbox overload thresholds and circuit-breaker tuning, which usually means this path was already a real operational sensitivity
- device-service and energy-service sit behind this path, so downstream slowness can amplify queue pressure

### 3. Redis as a shared coordination dependency

Redis is already central to:

- telemetry stage streams
- analytics job queue
- reporting job queue
- auth token state
- live/fleet streaming channels
- locks/counters/consumer groups

At 1000+ active devices, Redis should be treated as a shared critical dependency, not a lightweight convenience service.

### 4. Reporting worker capacity

Reporting is unlikely to be the very first bottleneck for continuous device ingest, but its current topology is modest:

- `1` worker container
- concurrency `2`

If report generation usage rises alongside a 1000+ device deployment, reporting can become a queue-latency problem even if telemetry remains healthy.

### 5. Analytics readiness + export dependency chain

Analytics execution depends on more than just ML worker slots. Exact-range readiness can involve:

- export trigger
- export completion
- object availability
- readiness polling

This means analytics latency under scale is partly an export/object-storage/readiness topology issue, not just an analytics worker issue.

## 5. What Should Be Scaled First For 1000+ Devices

Recommended first scaling order for Shivex:

1. `data-service` telemetry worker capacity and downstream projection path
2. Redis memory, persistence, and latency headroom
3. device-service and energy-service capacity behind telemetry outbox/projection
4. reporting workers
5. analytics workers and readiness/export support path
6. copilot only if actual human usage justifies it

This order reflects the current architecture. It should not be inverted based on generic SaaS instincts.

## 6. What Can Remain Unchanged In Code For Now

The following do not currently require major rewrites before the next infra phase:

- analytics queue admission logic
- analytics duplicate-job reuse behavior
- reporting queue admission logic
- reporting duplicate report reuse behavior
- telemetry durable stage pipeline design
- export request validation and scope model
- copilot request path shape

That does not mean these code paths are perfect forever. It means the next blocking work for 1000+ readiness is more likely:

- worker count and placement
- Redis sizing
- MySQL/Influx sizing
- object storage latency
- downstream service capacity
- observability and alerting

## 7. Recommended Infra Changes In Order

### Phase A: protect the shared coordination layer

- Move Redis sizing from minimal defaults to an explicitly capacity-planned deployment.
- Keep `noeviction` unless there is a deliberate queue-loss strategy.
- Monitor Redis memory, stream depth, pending counts, consumer lag, and persistence latency.
- Treat Redis restart/recovery drills as required because telemetry, analytics, reporting, and auth all depend on it.

### Phase B: scale the telemetry path first

- Increase `data-telemetry-worker` capacity before raising customer/device load materially.
- Consider additional telemetry worker containers before code changes.
- Validate projection, outbox, and downstream service behavior under production-like device burst patterns.
- Ensure device-service and energy-service can absorb the projection and energy fan-out pressure created by telemetry workers.

### Phase C: harden data-plane storage and downstream dependencies

- Capacity-plan InfluxDB for telemetry write rate and query fan-out.
- Capacity-plan MySQL for jobs, outbox, checkpoints, and auth/device metadata.
- Capacity-plan object storage for analytics/report/export artifact reads and writes.

### Phase D: scale queued business workloads

- Add reporting workers before large report usage waves.
- Scale analytics workers only after confirming export readiness and dataset generation are not the dominant limiter.
- Keep analytics queue caps and reporting queue caps in place while increasing worker capacity.

### Phase E: production-like verification

- Re-run load validation on production-like topology rather than a small disposable node.
- Verify sustained behavior for:
  - telemetry ingest and projection lag
  - outbox pending counts
  - report queue latency
  - analytics queue latency
  - export completion latency

## 8. What Should Be Monitored Continuously

### Redis / queue layer

- Redis memory usage
- Redis command latency
- telemetry stream lengths by stage
- analytics stream depth and pending messages
- reporting stream depth and pending messages
- dead-letter stream growth

### `data-service`

- oldest stage age
- projection backlog depth
- energy backlog depth
- rules backlog depth
- outbox pending count
- DLQ retryable pending count
- ingest rejection count
- projection defer / overload events

### `analytics-service`

- queued jobs
- active jobs
- rejected submissions by backlog threshold
- rejected submissions by tenant cap
- dead-letter events
- readiness/export wait time

### `reporting-service`

- queue depth
- pending messages
- dead-letter count
- report completion time
- enqueue-failed recovery events

### `data-export-service`

- forced export frequency
- export duration
- checkpoint lag
- exact-range export timeout rate

### Platform-level

- Influx write latency
- MySQL saturation / connection pressure
- object storage latency
- CPU and memory per worker role

## 9. Which Issues Are Code Problems Vs Infra Problems

### Mostly code problems already addressed

- wildcard CORS in data-service
- missing CSP in ui-web
- sensitive-data exposure in health/validation surfaces
- missing app-level throttling for expensive endpoints
- duplicate report submission work
- reporting enqueue-failure recovery gap
- analytics duplicate-job reuse gap

### Mostly infrastructure / topology problems next

- proving 1000+ device readiness end to end
- sustained telemetry projection throughput
- Redis headroom and failure resilience
- downstream capacity for device/energy projection effects
- reporting and analytics worker fleet sizing
- export/object-storage latency under heavier readiness demand

### Mixed code + infra area to watch

- data-export exact-range readiness behavior

Current code already has bounded retries, suppression, and cooldowns, which is good. But under materially larger fleets, this area may still need both:

- better capacity and worker sizing
- possibly a future cross-instance in-flight export guard if real evidence shows duplicate exact-range export pressure

## 10. Concise Final Recommendation For 1000+ Readiness

Shivex should not currently claim proven 1000+ device readiness from the existing evidence alone.

The codebase is in much better shape than a naive reading would suggest:

- telemetry, analytics, and reporting already have real queueing and backpressure controls
- duplicate expensive work is materially better controlled
- abuse-prone routes now have app-level rate limits

The next serious readiness work is infrastructure-first, especially around:

- Redis as shared queue/coordination infrastructure
- `data-service` projection throughput
- outbox/downstream pressure
- production-like worker topology and storage capacity

If future work follows that order, Shivex can move toward 1000+ readiness without broad backend rewrites. If future work skips that order and only scales user-facing APIs, it is likely to miss the actual first bottlenecks.
