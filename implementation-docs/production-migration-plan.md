# Production Migration Plan

- Repository name: `FactoryOPS-Cittagent-Obeya-main`
- Generation date: `2026-04-18`
- Branch: `main` (`git branch --show-current`)
- Primary context analyzed:
  - `memory.md`
  - `memory-appendix-api.md`
  - `memory-appendix-db.md`
  - `docker-compose.yml`
  - `.env.production.example`
  - `README.md`
  - `docs/aws_production_deployment.md`
  - `docs/preprod_validation.md`
  - `docs/auth_cutover_runbook.md`
  - service startup/config/runtime files under `services/*`
  - `ui-web/next.config.ts`, `ui-web/lib/*`
- Confidence markers used throughout:
  - `Confirmed from code`
  - `Inferred from usage`
  - `Needs runtime verification`
  - `Not found in repository`

## 1. Executive Verdict

### Direct answer

- Production infrastructure migration can begin now: `Yes, incrementally` (`Confirmed from code`).
- Direct production cutover from the current repo/runtime shape: `No` (`Confirmed from code` and `Inferred from usage`).

### What is already strong enough

- The platform is already split into deployable services and workers rather than one monolith (`docker-compose.yml`, `services/*`) (`Confirmed from code`).
- Multi-tenant request scoping and repository guards are first-class design concerns, not afterthoughts (`services/shared/auth_middleware.py`, `services/shared/tenant_context.py`, `services/shared/tenant_guards.py`, `services/shared/scoped_repository.py`) (`Confirmed from code`).
- Several heavy paths are already bounded:
  - telemetry uses Redis Streams backlog thresholds, stage workers, DLQ, reconciliation, and retention controls (`services/data-service/src/config/settings.py`, `services/data-service/src/workers/telemetry_pipeline.py`, `services/data-service/src/services/outbox_relay.py`) (`Confirmed from code`)
  - analytics has queue caps, tenant fairness caps, stale worker recovery, and worker heartbeats (`services/analytics-service/src/config/settings.py`, `services/analytics-service/src/workers/job_worker.py`) (`Confirmed from code`)
  - reporting and rule notifications both use durable Redis stream queues plus DB claim state (`services/reporting-service/src/queue/report_queue.py`, `services/reporting-service/src/workers/report_worker.py`, `services/rule-engine-service/app/queue/notification_queue.py`, `services/rule-engine-service/app/workers/notification_worker.py`) (`Confirmed from code`)
- The repo already contains a pre-production validation harness and AWS guardrail doc (`docs/preprod_validation.md`, `docs/aws_production_deployment.md`) (`Confirmed from code`).

### What still must be hardened before real production rollout

- The checked-in runtime is explicitly local-only and must not be promoted directly (`docker-compose.yml:1-3`) (`Confirmed from code`).
- Several services still assume permissive network trust and flat east-west access:
  - internal service bypass is header-based (`X-Internal-Service`) and therefore must be protected by private networking and security groups, not exposed publicly (`services/shared/auth_middleware.py`) (`Confirmed from code`)
  - reporting and waste-analysis currently allow `allow_origins=["*"]` (`services/reporting-service/src/main.py`, `services/waste-analysis-service/src/main.py`) (`Confirmed from code`)
- Some background behavior is still co-located with API replicas and needs singleton or leader-aware deployment:
  - device-service runs performance trend schedulers, dashboard snapshot schedulers, live projection reconciliation, and state interval retention inside API lifespan (`services/device-service/app/__init__.py:264-525`) (`Confirmed from code`)
  - data-service API role owns MQTT ingestion, while worker role owns durable stage processing and maintenance (`services/data-service/src/main.py`) (`Confirmed from code`)
  - waste-analysis jobs run via FastAPI `BackgroundTasks`, not a separate durable worker queue (`services/waste-analysis-service/src/handlers/waste_analysis.py`) (`Confirmed from code`)
- Observability is incomplete for production-wide operations:
  - checked-in Prometheus only scrapes `device-service` today (`monitoring/prometheus/prometheus.yml`) (`Confirmed from code`)
  - checked-in Alertmanager targets `host.docker.internal`, which is local-only (`monitoring/alertmanager/alertmanager.yml`) (`Confirmed from code`)
- There is no repo-native production IaC, Helm, or Terraform (`Not found in repository`).

### What is safe to defer until after first production migration

- Replacing EMQX with AWS IoT Core. The code is already MQTT-broker oriented, so private EMQX is the lower-risk first migration (`docker-compose.yml:98-123`, `services/data-service/src/handlers/mqtt_handler.py`) (`Confirmed from code`, `Inferred from usage`).
- Reworking analytics storage away from S3/MinIO dataset files. The current analytics path is explicitly built around object storage datasets (`services/analytics-service/src/services/dataset_service.py`, `services/data-export-service`) (`Confirmed from code`).
- Rewriting copilot provider integration. It is already provider-optional and can be launched after core platform reliability if needed (`services/copilot-service/src/main.py`, `services/copilot-service/src/config.py`) (`Confirmed from code`).

## 2. Current Architecture As Found

### Service and worker inventory

| Component | Current role | Evidence | Confidence |
|---|---|---|---|
| `ui-web` | Next.js server-rendered/operator UI and rewrite proxy | `ui-web/package.json`, `ui-web/next.config.ts`, `docker-compose.yml:1136-1154` | Confirmed from code |
| `auth-service` | Identity, tenants, plants, users, invite/reset, refresh cookie/session | `services/auth-service/app/main.py`, `services/auth-service/app/api/v1/*.py` | Confirmed from code |
| `device-service` | Device CRUD, live projections, fleet/dashboard, shifts, health config, SSE fleet stream | `services/device-service/app/__init__.py`, `services/device-service/app/api/v1/devices.py` | Confirmed from code |
| `data-service` API | MQTT ingress host, telemetry query API, WebSocket live telemetry | `services/data-service/src/main.py`, `services/data-service/src/api/*`, `docker-compose.yml:214-315` | Confirmed from code |
| `data-telemetry-worker` / `data-telemetry-worker-2` | Durable telemetry stage workers, outbox relay, reconciliation, DLQ retry, retention cleanup | `docker-compose.yml:317-565`, `services/data-service/src/worker_main.py`, `services/data-service/src/workers/telemetry_pipeline.py` | Confirmed from code |
| `energy-service` | Energy live updates, summaries, monthly calendar, Redis pub/sub broadcaster | `services/energy-service/app/main.py`, `services/energy-service/app/api/routes.py`, `services/energy-service/app/services/broadcaster.py` | Confirmed from code |
| `rule-engine-service` | Rule CRUD, alert APIs, evaluation endpoint, outbox metrics | `services/rule-engine-service/app/__init__.py`, `app/api/v1/*` | Confirmed from code |
| `rule-engine-worker` | Durable notification delivery worker | `docker-compose.yml:616-653`, `services/rule-engine-service/app/worker_main.py`, `app/workers/notification_worker.py` | Confirmed from code |
| `analytics-service` API | Analytics job submission/status/results/ops | `docker-compose.yml:654-719`, `services/analytics-service/src/main.py` | Confirmed from code |
| `analytics-worker` / `analytics-worker-2` | Heavy ML execution, retrainer, queue consumption | `docker-compose.yml:721-829`, `services/analytics-service/src/worker_main.py`, `src/workers/job_worker.py` | Confirmed from code |
| `data-export-service` | Continuous telemetry export from InfluxDB to object storage | `services/data-export-service/main.py`, `worker.py`, `docker-compose.yml:831-869` | Confirmed from code |
| `reporting-service` | Report APIs, schedule APIs, tariff/settings APIs | `services/reporting-service/src/main.py`, `docker-compose.yml:871-931` | Confirmed from code |
| `reporting-worker` | Report execution worker | `docker-compose.yml:933-983`, `services/reporting-service/src/worker_main.py`, `src/workers/report_worker.py` | Confirmed from code |
| `waste-analysis-service` | Waste-analysis API plus in-process background job execution | `docker-compose.yml:984-1040`, `services/waste-analysis-service/src/handlers/waste_analysis.py` | Confirmed from code |
| `copilot-service` | Tenant-scoped copilot with readonly DB path and provider-optional AI | `docker-compose.yml:1041-1074`, `services/copilot-service/src/main.py`, `src/database.py` | Confirmed from code |
| `mysql` | Shared relational DB | `docker-compose.yml:5-24` | Confirmed from code |
| `influxdb` | Time-series telemetry DB | `docker-compose.yml:26-48` | Confirmed from code |
| `redis` | Redis Streams, pub/sub, revocation state | `docker-compose.yml:70-81`, service code above | Confirmed from code |
| `minio` | S3-compatible object storage | `docker-compose.yml:50-69` | Confirmed from code |
| `emqx` | MQTT broker | `docker-compose.yml:98-123` | Confirmed from code |
| `mailpit` | Local SMTP sink | `docker-compose.yml:1127-1134` | Confirmed from code |
| `prometheus`, `alertmanager`, `grafana` | Local monitoring stack | `docker-compose.yml:1172-1220`, `monitoring/*` | Confirmed from code |

### Datastores, caches, queues, and object storage

- Relational database:
  - Shared MySQL database `ai_factoryops` across auth, device, data outbox/DLQ, analytics, reporting, rule-engine, and waste-analysis (`docker-compose.yml`, `memory-appendix-db.md`) (`Confirmed from code`)
- Time-series database:
  - InfluxDB v2 bucket `telemetry` for `device_telemetry` (`docker-compose.yml:26-48`, `services/data-service/src/repositories/influxdb_repository.py`) (`Confirmed from code`)
- Redis usage:
  - auth token revocation/current-token checks (`services/shared/auth_middleware.py`) (`Confirmed from code`)
  - telemetry multi-stage Redis Streams and worker heartbeat keys (`services/data-service/src/queue/telemetry_stream.py`) (`Confirmed from code`)
  - analytics jobs stream and DLQ stream (`services/analytics-service/src/workers/job_queue.py`) (`Confirmed from code`)
  - reporting jobs stream and DLQ stream (`services/reporting-service/src/queue/report_queue.py`) (`Confirmed from code`)
  - rule notification outbox stream and DLQ stream (`services/rule-engine-service/app/queue/notification_queue.py`) (`Confirmed from code`)
  - live fan-out channels for fleet and energy (`services/device-service/app/config.py`, `services/energy-service/app/services/broadcaster.py`) (`Confirmed from code`)
- Object storage:
  - datasets bucket `energy-platform-datasets` and waste bucket `factoryops-waste-reports` created in local MinIO bootstrap (`docker-compose.yml:83-96`) (`Confirmed from code`)
  - dashboard snapshots also have explicit MinIO/S3 storage settings in device-service (`services/device-service/app/config.py:86-92`) (`Confirmed from code`)

### External integrations

- SMTP email for auth invites/resets and rule notifications (`services/auth-service/app/config.py`, `services/rule-engine-service/app/config.py`) (`Confirmed from code`)
- Twilio placeholders for SMS and WhatsApp in rule-engine (`services/rule-engine-service/app/config.py`) (`Confirmed from code`)
- AI provider integration in copilot: Groq, Gemini, or OpenAI (`services/copilot-service/src/config.py`, `src/ai/model_client.py`) (`Confirmed from code`)

### Reverse proxy and frontend routing

- Browser traffic goes to `ui-web`, which rewrites `/backend/*` and selected `/api/*` routes to service backends (`ui-web/next.config.ts`) (`Confirmed from code`)
- Auth refresh cookie path is bound to `/backend/auth/api/v1/auth`, which means the web app domain/path matters during production ingress design (`services/auth-service/app/config.py`) (`Confirmed from code`)
- Fleet live updates use SSE from `device-service` through frontend routing (`services/device-service/app/api/v1/devices.py`, `ui-web/tests/unit/fleetStreamRoute.test.ts`) (`Confirmed from code`)

### Healthcheck model

- Most services expose `/health`; some also expose `/ready` and `/metrics`.
- Docker healthchecks mostly call only the liveness route, not deep readiness (`docker-compose.yml`, service Dockerfiles) (`Confirmed from code`)
- Better readiness exists in code for auth, device, reporting, waste, data-export, and rule-engine, but compose often checks only `/health` (`Confirmed from code`).

### Current local/dev assumptions

- Published host ports for nearly every service (`docker-compose.yml`) (`Confirmed from code`)
- Local or weak credentials in compose and `.env` examples (`docker-compose.yml`, `.env.production.example`, `.env`) (`Confirmed from code`)
- EMQX anonymous access is allowed in local/dev by explicit flag (`docker-compose.yml:116`, `.env.production.example:53`) (`Confirmed from code`)
- Local admin consoles exposed for MinIO, EMQX, Mailpit, Prometheus, Alertmanager, Grafana (`docker-compose.yml`) (`Confirmed from code`)
- Local-only alert sink via `host.docker.internal:9999/alerts` (`monitoring/alertmanager/alertmanager.yml`) (`Confirmed from code`)

### Interactive paths

- Login, refresh, logout, invite acceptance (`auth-service`) (`Confirmed from code`)
- Fleet dashboard summary, fleet snapshot, SSE fleet stream, device dashboard bootstrap (`device-service`) (`Confirmed from code`)
- Telemetry reads/latest/latest-batch and WebSocket telemetry (`data-service`) (`Confirmed from code`)
- Alerts list, rule CRUD, notification usage views (`rule-engine-service`) (`Confirmed from code`)
- Energy summary and monthly calendar (`energy-service`) (`Confirmed from code`)
- Analytics submit/status/result retrieval (`analytics-service`) (`Confirmed from code`)
- Report creation/history/status/download (`reporting-service`) (`Confirmed from code`)
- Waste submit/status/result/download (`waste-analysis-service`) (`Confirmed from code`)
- Copilot curated/AI chat (`copilot-service`) (`Confirmed from code`)

### Heavy/background paths

- MQTT ingest acceptance and durable stage processing (`data-service` API + workers) (`Confirmed from code`)
- Device projection reconciliation and dashboard snapshot scheduler in API process (`device-service`) (`Confirmed from code`)
- Analytics ML workers and retrainer (`analytics-worker*`) (`Confirmed from code`)
- Report worker and schedule claiming (`reporting-worker`, `reporting-service` scheduler) (`Confirmed from code`)
- Rule notification worker (`rule-engine-worker`) (`Confirmed from code`)
- Waste analysis job execution in API background task (`waste-analysis-service`) (`Confirmed from code`)
- Data export continuous export worker (`data-export-service`) (`Confirmed from code`)

### Real-time paths

- MQTT device ingress (`EMQX -> data-service`) (`Confirmed from code`)
- Fleet SSE from `device-service` (`Confirmed from code`)
- Device telemetry WebSockets from `data-service` (`Confirmed from code`)
- Redis pub/sub energy and fleet fan-out (`Confirmed from code`)

### Cross-service dependencies

- `data-service` depends on `device-service`, `energy-service`, and `rule-engine-service` (`docker-compose.yml:236-242`, `services/data-service/src/services/outbox_relay.py`, `src/services/rule_engine_client.py`) (`Confirmed from code`)
- `device-service` depends on `data-service`, `rule-engine-service`, `reporting-service`, and `energy-service` URLs (`docker-compose.yml:135-150`, `services/device-service/app/config.py`) (`Confirmed from code`)
- `reporting-service` depends on `device-service`, `energy-service`, InfluxDB, MinIO/S3, and Redis (`docker-compose.yml:877-906`, `services/reporting-service/src/main.py`) (`Confirmed from code`)
- `waste-analysis-service` depends on `device-service`, `reporting-service`, `energy-service`, InfluxDB, and MinIO (`docker-compose.yml:990-1012`, `services/waste-analysis-service/src/services/remote_clients.py`) (`Confirmed from code`)
- `analytics-service` depends on Redis, MySQL, object storage datasets, `data-export-service`, `data-service`, and `device-service` (`services/analytics-service/src/config/settings.py`, `src/services/readiness_orchestrator.py`) (`Confirmed from code`)
- `copilot-service` depends on readonly MySQL plus data/reporting/energy service URLs and an AI provider (`docker-compose.yml:1047-1060`, `services/copilot-service/src/config.py`) (`Confirmed from code`)

### Single points of failure in current shape

- Single MySQL container (`docker-compose.yml:5-24`) (`Confirmed from code`)
- Single InfluxDB container (`docker-compose.yml:26-48`) (`Confirmed from code`)
- Single Redis container (`docker-compose.yml:70-81`) (`Confirmed from code`)
- Single EMQX broker (`docker-compose.yml:98-123`) (`Confirmed from code`)
- Waste analysis has no separate durable worker queue; jobs are tied to API instance lifetime (`services/waste-analysis-service/src/handlers/waste_analysis.py`) (`Confirmed from code`)
- `data-service` API role owns MQTT subscription; scaling API replicas without a deliberate MQTT topology review risks duplicate or competing subscriptions (`services/data-service/src/main.py`, `services/data-service/src/handlers/mqtt_handler.py`) (`Confirmed from code`, `Needs runtime verification`)

## 3. Production Target Architecture

### Recommended target shape for this codebase

This repository is already container-first and service-oriented, so the lowest-risk production target is:

- Public application tier:
  - `ui-web` as a containerized Next.js service behind an AWS Application Load Balancer
- Private application tier:
  - Python API services and workers on ECS services with separate task definitions by role
- Managed data tier:
  - Amazon RDS MySQL
  - ElastiCache Redis
  - S3 for object storage
  - InfluxDB kept on Influx-compatible hosting, not rewritten to a different time-series engine
- Device ingress tier:
  - Private EMQX first, fronted by NLB for MQTT/TLS

This matches the repo’s existing runtime contracts instead of forcing a rewrite:

- MySQL is hard-coded across services (`DATABASE_URL`, `MYSQL_*`) (`Confirmed from code`)
- object storage is already S3-compatible (`S3_*`, `MINIO_*`) (`Confirmed from code`)
- Redis Streams and pub/sub are real runtime primitives, not optional cache sugar (`Confirmed from code`)
- InfluxDB v2 APIs are used directly in data/reporting/waste/export services (`Confirmed from code`)
- MQTT broker semantics are already embedded in `data-service` (`Confirmed from code`)

### Recommended AWS component mapping

| Concern | Recommended production component | Why it fits this repo | Confidence |
|---|---|---|---|
| Web UI | ECS service for `ui-web` behind public ALB | Next.js runs as a Node server (`ui-web/package.json`), not static-only | Confirmed from code |
| API runtimes | ECS services per API (`auth`, `device`, `data`, `energy`, `rule-engine`, `analytics`, `reporting`, `waste`, `copilot`, `data-export`) | All services are containerized and already run under Uvicorn/start scripts | Confirmed from code |
| Worker runtimes | Separate ECS services/task definitions for telemetry workers, analytics workers, reporting worker, rule worker | Heavy and background roles are already separate in compose/start scripts | Confirmed from code |
| Relational DB | Amazon RDS MySQL 8 / Aurora MySQL compatible | Direct MySQL dependency across services, Alembic migrations, SQLAlchemy async | Confirmed from code |
| Redis | ElastiCache Redis with auth/TLS | Redis Streams, pub/sub, and revocation keys are core runtime behavior | Confirmed from code |
| Object storage | S3 buckets with private access, versioning/lifecycle | Existing code already uses S3-compatible APIs and bucket/key abstractions | Confirmed from code |
| Telemetry time-series | InfluxDB Cloud Dedicated or self-managed private InfluxDB 2.x on EC2/EKS | Code uses InfluxDB client APIs directly; replacing engine would be a rewrite | Confirmed from code |
| MQTT ingress | Private EMQX first; revisit AWS IoT Core later only if device auth/project scope supports it | Existing MQTT topic/host/port model is EMQX-friendly now | Confirmed from code, Inferred from usage |
| TLS / ingress | ACM certs + public ALB for web, internal ALB/service discovery for private east-west HTTP, NLB for MQTT TLS | Browser/UI and device MQTT have different ingress needs | Confirmed from code, Inferred from usage |
| Secrets | AWS Secrets Manager + SSM Parameter Store | Repo already expects many sensitive env vars; no in-repo secret manager | Confirmed from code |
| Logs/metrics | CloudWatch Logs mandatory; Managed Prometheus/Grafana optional after scrape expansion | Current checked-in monitoring is incomplete for prod but metrics endpoints exist | Confirmed from code |

### Service placement and separation

#### Public-facing

- `ui-web`
- MQTT endpoint for devices/brokers (`EMQX` or equivalent)
- Optional public `auth-service` path only if the mobile app requires direct auth endpoints outside the web domain (`Needs runtime verification`)

#### Internal-only

- `device-service`
- `data-service`
- `energy-service`
- `rule-engine-service`
- `analytics-service`
- `data-export-service`
- `reporting-service`
- `waste-analysis-service`
- `copilot-service`
- all workers
- RDS, Redis, InfluxDB, S3 private access, and internal ALBs/service discovery

### Horizontal scaling guidance by component

#### Safe to scale horizontally now

- `ui-web` (`stateless`)
- `auth-service` (`mostly stateless`, but token cleanup loop runs in each instance; harmless but noisy)
- `energy-service`
- `rule-engine-service` API
- `rule-engine-worker`
- `analytics-service` API
- `analytics-worker`
- `reporting-worker`
- `data-export-service` only if export partitioning/tenant sharding is introduced or verified; today it behaves like a singleton continuous exporter (`Needs runtime verification`)

#### Scale with extra care

- `device-service`
  - API reads/writes are horizontally safe
  - API lifespan also starts schedulers and reconciliation loops; run one singleton maintenance replica or move these loops out before broad scale-out (`services/device-service/app/__init__.py:264-525`) (`Confirmed from code`)
- `data-service`
  - API role owns MQTT subscription
  - worker role owns durable stage processing and maintenance
  - production should scale `worker` separately from `api`, and MQTT ingestion should be single-active or intentionally shared (`services/data-service/src/main.py`) (`Confirmed from code`)
- `reporting-service`
  - API includes APScheduler for due schedules; DB claim logic prevents obvious duplicate processing, but one schedule runner is still cleaner (`services/reporting-service/src/main.py`, `src/repositories/scheduled_repository.py`) (`Confirmed from code`)
- `waste-analysis-service`
  - jobs run in-process through FastAPI `BackgroundTasks`, so replica loss can orphan in-flight work until stale-job cleanup catches it (`services/waste-analysis-service/src/handlers/waste_analysis.py`, `src/main.py`) (`Confirmed from code`)

## 4. Recommended Production Environments

### Local

- Purpose:
  - developer workflow
  - schema iteration
  - UI/API feature work
  - manual device simulation
- Real vs mocked infra:
  - keep compose-local MySQL, Redis, InfluxDB, MinIO, EMQX, Mailpit
- Validation supported:
  - unit tests
  - local integration checks
  - UI development
  - simulator-based smoke tests
- Scale expectation:
  - low-volume, single-host only

### Staging / preprod

- Purpose:
  - production-like validation environment
  - full migration rehearsal
  - go-live certification
- Real vs mocked infra:
  - must use the same classes of infra as production for RDS, Redis, S3, Influx, MQTT, TLS, secrets, and ingress (`Confirmed from code`, `Inferred from usage`)
  - external notification providers can use sandbox/test mode
  - AI provider may be disabled initially if copilot is not in the first production slice
- Validation supported:
  - `scripts/preprod_validation.py`
  - auth cutover tests
  - telemetry ingest soak
  - tenant isolation certification
  - performance/load and failure drills
- Scale expectation:
  - enough to validate concurrency, backlog behavior, and recovery under representative tenant/device volume

### Production

- Purpose:
  - tenant-facing SaaS runtime
- Real vs mocked infra:
  - all real managed/private infra
  - no local-only consoles or Mailpit
  - production SMTP/provider credentials
- Validation supported:
  - smoke tests
  - live SLO monitoring
  - backup/restore drills
  - canary/rollback
- Scale expectation:
  - sized initially for current customer/device load with headroom
  - autoscaling only where current code/runtime separation justifies it

## 5. Service-by-Service Production Readiness Map

| Service / worker | Current role | Port / entrypoint / health | Stateful? | Runtime dependencies | Production deployment recommendation | Scale strategy | Multi-replica safe? | Verify before prod |
|---|---|---|---|---|---|---|---|---|
| `ui-web` | Browser UI and backend rewrite proxy | `3000`, Next.js server, no deep readiness found in repo | Stateless | internal service URLs | ECS service behind public ALB | scale by CPU/RPS | Yes | confirm mobile/public routing strategy, auth cookie domain/path, health endpoint |
| `auth-service` | identity/tenant/user lifecycle | `8090`, `start.sh`, `/health`, `/ready` | DB-backed but stateless request path | MySQL, Redis, SMTP | internal ECS service; optionally public only if mobile requires direct access | scale by auth RPS | Yes, with cleanup-loop duplication caveat | secure cookie config, allowed origins, SMTP, rotation of JWT secret |
| `device-service` | devices, live state, dashboard, SSE | `8000`, `start.sh`, `/health`, `/ready`, `/metrics` | DB-backed plus background schedulers in API | MySQL, Redis, data/energy/reporting/rules URLs | split API from maintenance in deployment model or keep one singleton maintenance replica | scale API by read latency and SSE load | API yes; maintenance loops need singleton handling | leader/singleton plan for schedulers, Redis channel topology, SSE load test |
| `data-service` API | MQTT ingress and telemetry read APIs | `8081`, `uvicorn src.main:app`, `/health`, `/api/v1/data/health`, ws stats | API stateless but owns MQTT connection | InfluxDB, Redis, MySQL, MQTT broker, device/energy/rules | run one or controlled-small count of API ingress tasks; do not autoscale blindly | scale only after MQTT topology review | Needs care | verify duplicate subscription behavior, broker auth/TLS, ws auth path |
| `data-telemetry-worker` | durable ingest/projection/broadcast/energy/rules stages | `python -m src.worker_main`, Redis heartbeat-based health | Stateful through queue claims only | InfluxDB, Redis, MySQL, device/energy/rules | dedicated worker ECS service | scale by stream depth and lag | Yes | stage lag alerts, DLQ retry loops, outbox relay saturation |
| `energy-service` | summaries and live energy projection | `8010`, `start.sh`, `/health` | DB-backed, Redis pub/sub | MySQL, Redis, reporting/device URLs | internal ECS service | scale by API latency and batch load | Yes | readiness should include DB/Redis in production, tariff cache behavior |
| `rule-engine-service` | rule CRUD/evaluation/alerts | `8002`, `start.sh`, `/health`, `/ready`, `/metrics` | DB-backed, queue-backed | MySQL, Redis, SMTP/Twilio, device URL | internal ECS service | scale by API RPS and eval latency | Yes | internal header trust, SMTP/Twilio readiness, metrics scraping |
| `rule-engine-worker` | notification outbox delivery | `python -m app.worker_main` | queue-backed | MySQL, Redis, SMTP/Twilio | dedicated worker ECS service | scale by queue depth and send latency | Yes | DLQ monitoring, retry/backoff tuning, provider throughput limits |
| `analytics-service` API | analytics admission/status/results | `8003`, `start.sh`, `/health`, `/health/*` | stateless API over DB/Redis | MySQL, Redis, object storage, device/data/data-export URLs | internal ECS service | scale by submission and read traffic | Yes | queue fairness settings, overload responses, ops endpoints |
| `analytics-worker` | heavy ML execution | `python -m src.worker_main` via `start.sh` | queue-backed, CPU/memory heavy | MySQL, Redis, S3/MinIO datasets, data-export/data/device URLs | dedicated worker ECS service with separate autoscaling and larger memory | scale by queue depth, CPU, memory | Yes | worker memory profile, stale lease recovery, retrainer isolation |
| `reporting-service` | reports/settings/schedules API | `8085`, `start.sh`, `/health`, `/ready`, `/metrics` | DB-backed; scheduler runs in API lifespan | MySQL, Redis, InfluxDB, object storage, device/energy | internal ECS service, but prefer one scheduler-active replica | scale API separately from worker | API yes; scheduler best singleton | tighten CORS, validate schedule claim behavior under replicas |
| `reporting-worker` | report execution | `python -m src.worker_main` | queue-backed | MySQL, Redis, InfluxDB, object storage, device/energy | dedicated worker ECS service | scale by queue depth and report runtime | Yes | PDF runtime, queue DLQ alerts, object storage throughput |
| `waste-analysis-service` | waste job API and execution | `8087`, `start.sh`, `/health`, `/ready` | DB-backed; heavy jobs are in-process background tasks | MySQL, InfluxDB, object storage, device/reporting/energy | internal ECS service; short term keep isolated from interactive APIs and use low replica count; medium term move to real worker queue | scale very cautiously | Not safely for heavy-job durability | background task survivability, timeout behavior, strict quality gate under load |
| `copilot-service` | tenant-scoped copilot | `8007`, `uvicorn main:app`, `/health` | stateless over readonly DB/provider | readonly MySQL, data/reporting/energy, AI provider | separate internal ECS service; optionally defer first prod slice | scale by request latency, provider quotas | Yes | readonly DB permissions, provider timeouts, tenant SQL guard coverage |
| `data-export-service` | continuous telemetry export | `8080`, `uvicorn main:app`, `/health`, `/ready` | checkpointed exporter | InfluxDB, MySQL checkpoint DB, S3 | dedicated internal ECS service | start singleton, then shard only after verified | Needs runtime verification | checkpoint schema, S3 retention, export lag and status API correctness |
| supporting infra | MySQL, Redis, InfluxDB, S3, EMQX, observability | n/a | Stateful | all services | move to managed/private services per section 6 | scale per service class | n/a | backups, restore, private networking, retention |

## 6. Managed Infrastructure Mapping

| Current component | Current local/dev implementation | Recommended production replacement | Why | Migration notes |
|---|---|---|---|---|
| MySQL | single `mysql:8.0` container with local volume (`docker-compose.yml:5-24`) | Amazon RDS MySQL 8 or Aurora MySQL compatible | shared system-of-record for nearly every service | run Alembic against RDS; snapshot before cutover; private subnets only |
| InfluxDB | single `influxdb:2.7-alpine` container (`docker-compose.yml:26-48`) | InfluxDB Cloud Dedicated or private self-managed InfluxDB 2.x | code depends on native InfluxDB client APIs | do not swap engines during first migration |
| Redis | single `redis:7-alpine` container (`docker-compose.yml:70-81`) | ElastiCache Redis with auth/TLS | streams, pub/sub, token revocation, queue metrics all rely on Redis | preserve stream names; set retention/monitoring explicitly |
| MinIO | local MinIO container (`docker-compose.yml:50-69`) | Amazon S3 private buckets | code is already S3-compatible and production example points there (`.env.production.example`) | create separate buckets/prefixes, versioning, lifecycle |
| EMQX | single `emqx` container with optional anonymous access (`docker-compose.yml:98-123`) | Private EMQX on EC2/ECS plus NLB, or AWS IoT Core later | current broker/topic integration is already EMQX-friendly | first production migration should keep EMQX semantics |
| Mailpit | local SMTP sink (`docker-compose.yml:1127-1134`) | Real SMTP provider or SES/enterprise SMTP relay | auth invites and notifications need real delivery | move secrets to Secrets Manager; test sandbox before production |
| Prometheus/Grafana/Alertmanager | local containers (`docker-compose.yml:1172-1220`) | CloudWatch Logs + CloudWatch alarms at minimum; optionally AMP + AMG + managed Alertmanager | checked-in config is local/incomplete | extend scrapes to all services before relying on Prometheus alerts |
| Local volumes | `mysql_data`, `influxdb_data`, `minio_data`, `prometheus_data`, `alertmanager_data`, `grafana_data` | managed persistent services / S3 / snapshots | local volumes are single-host only | do not carry volume-level state directly into prod |
| Local secrets / `.env` | checked-in examples and local env values | Secrets Manager / SSM / task env injection | multiple secrets are required across services | prohibit use of compose defaults in prod |

### What can remain self-hosted vs should be managed

- Should move to managed now:
  - MySQL
  - Redis
  - S3 object storage
  - secret management
  - public DNS/TLS
- Can remain self-hosted initially if managed equivalent would force code or protocol changes:
  - InfluxDB
  - EMQX
  - Prometheus/Grafana, if CloudWatch is still used as the authoritative alert path

## 7. Data and State Migration Plan

### Relational MySQL data

- What data exists:
  - tenants, users, plants, refresh tokens, auth action tokens
  - devices, shifts, live state, dashboard snapshots, performance trends, device state intervals, hardware inventory
  - telemetry outbox, reconciliation logs, DLQ rows
  - analytics jobs/artifacts metadata/worker heartbeats
  - reports, schedules, tariffs, notification channels
  - rules, alerts, activity events, notification outbox/delivery logs
  - waste jobs and summaries
- Criticality:
  - critical system-of-record (`Confirmed from code`)
- Must migrate:
  - yes, unless production is a clean greenfield with no seed state (`Inferred from usage`)
- Safe migration method:
  - provision RDS
  - run all Alembic migrations there
  - export/import source data in a rehearsal
  - during cutover, freeze writes, drain queues, snapshot source, final logical export/import, validate row counts by domain
- Downtime:
  - safest first cutover is short maintenance window
  - true no-downtime is not justified by current multi-service shared-DB coupling (`Inferred from usage`)
- Rollback:
  - keep source DB untouched until production validation passes
  - point services back to source or restore RDS snapshot if post-cutover defect is data-related

### InfluxDB telemetry

- What data exists:
  - device telemetry in bucket `telemetry`, measurement `device_telemetry` (`Confirmed from code`)
- Criticality:
  - critical for dashboards, reporting, waste, export, analytics readiness
- Must migrate:
  - yes if historical telemetry matters at go-live
- Safe migration method:
  - create production bucket with explicit retention
  - bulk export/import historical telemetry
  - dual-write is `Not found in repository`
  - safer first cutover is ingress pause, final export/import, then resume devices
- Downtime:
  - brief MQTT ingest pause is safest
- Rollback:
  - keep source Influx online until validation passes; re-point data-service if necessary

### Object storage

- What data exists:
  - analytics datasets
  - report PDFs/results
  - waste PDFs/results
  - dashboard snapshots
  - export artifacts/checkpoints references
- Criticality:
  - important for analytics/report/download continuity
- Must migrate:
  - yes for any environment with existing report/export history
- Safe migration method:
  - create private S3 buckets and copy objects with checksums
  - preserve key naming because services construct deterministic keys (`services/analytics-service/src/services/dataset_service.py`, `services/waste-analysis-service/src/tasks/waste_task.py`, reporting/device snapshot models) (`Confirmed from code`)
- Downtime:
  - object sync can mostly be online, but final consistency pass should happen during cutover window
- Rollback:
  - keep source MinIO readable until services are stable

### Redis state

- What data exists:
  - queue streams
  - pub/sub channels
  - revoked token keys
  - worker heartbeat keys
- Criticality:
  - operationally important but mostly ephemeral
- Must migrate:
  - generally `No` for first production launch
- Safe migration method:
  - cold start Redis in target environment
  - drain source queues before cutover
  - invalidate active sessions if needed rather than trying to carry revocation state
- Downtime:
  - none if queues are drained and sessions are re-established
- Rollback:
  - stateless restart against previous Redis endpoint

### In-flight jobs

- Reporting / analytics / waste / notification outbox:
  - do not migrate in-flight work
  - drain or fail jobs before cutover
  - let users resubmit if necessary
- Evidence:
  - analytics and waste already fail stale/interrupted jobs on startup (`services/analytics-service/src/main.py`, `services/waste-analysis-service/src/main.py`) (`Confirmed from code`)

### Token and session state

- Access tokens are JWTs and refresh tokens are DB-backed (`Confirmed from code`)
- Safest cutover:
  - preserve auth DB state if continuity matters
  - otherwise revoke all sessions after migration and force login
- Production recommendation:
  - plan a controlled re-auth window rather than attempting secret rotation during cutover

## 8. Environment Variable and Secrets Migration Map

Only variables that materially affect production runtime are included below.

| Service | Variable | Class | Purpose | Current local/dev behavior | Production handling | Where it should live | Rotation? |
|---|---|---|---|---|---|---|---|
| shared/all services | `JWT_SECRET_KEY` | secret | signs/verifies JWTs and startup contract | local env / compose passthrough | single strong secret shared across services, injected securely | Secrets Manager | Yes, planned rotation window |
| auth-service | `DATABASE_URL` | infra endpoint + secret | auth DB connectivity | compose MySQL URL | RDS endpoint with secret-sourced credentials | Secrets Manager + task env | Yes |
| auth-service | `REDIS_URL` | infra endpoint + secret | token revocation/current checks | local Redis | ElastiCache endpoint with auth/TLS | Secrets Manager + task env | Yes |
| auth-service | `EMAIL_SMTP_HOST`, `EMAIL_SMTP_USERNAME`, `EMAIL_SMTP_PASSWORD`, `EMAIL_FROM_ADDRESS` | secret + infra endpoint | invite/reset email delivery | local/dev SMTP values | real SMTP relay | Secrets Manager | Yes |
| auth-service | `FRONTEND_BASE_URL` | safe config | origin checks and email links | `http://localhost:3000` | production app URL | task env / SSM | No |
| auth-service | `REFRESH_COOKIE_DOMAIN`, `REFRESH_COOKIE_PATH`, `REFRESH_COOKIE_SAMESITE` | runtime auth config | browser refresh cookie behavior | local defaults | set production domain/path explicitly; verify same-site rules | task env / SSM | No |
| device-service | `DATABASE_URL` | infra endpoint + secret | device DB | local MySQL | RDS | Secrets Manager | Yes |
| device-service | `REDIS_URL` | infra endpoint + secret | fleet stream pub/sub | local Redis | ElastiCache | Secrets Manager | Yes |
| device-service | `DATA_SERVICE_BASE_URL`, `RULE_ENGINE_SERVICE_BASE_URL`, `REPORTING_SERVICE_BASE_URL`, `ENERGY_SERVICE_BASE_URL`, `AUTH_SERVICE_BASE_URL` | infra endpoint | internal service routing | compose hostnames | private service DNS/internal ALB | task env / SSM | No |
| device-service | `SNAPSHOT_STORAGE_BACKEND`, `SNAPSHOT_MINIO_*` | secret + infra endpoint | dashboard snapshot object storage | MinIO defaults | move to S3-compatible private config | Secrets Manager + task env | Yes for creds |
| device-service | `DASHBOARD_*`, `PERFORMANCE_TRENDS_*`, `STATE_INTERVAL_*` | runtime tuning knobs | scheduler cadence, freshness, retention | compose defaults | tune in staging with real data | SSM / task env | No |
| data-service | `MQTT_BROKER_HOST`, `MQTT_BROKER_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_CLEAN_SESSION` | secret + infra endpoint | broker connectivity | local EMQX, mostly no auth | private EMQX TLS/auth | Secrets Manager + task env | Yes |
| data-service | `INFLUXDB_URL`, `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET` | secret + infra endpoint | telemetry persistence | local Influx | production Influx | Secrets Manager + task env | Yes |
| data-service | `REDIS_URL` | infra endpoint + secret | streams/pubsub | local Redis | ElastiCache | Secrets Manager | Yes |
| data-service | `DEVICE_SERVICE_URL`, `ENERGY_SERVICE_URL`, `RULE_ENGINE_URL` | infra endpoint | downstream HTTP | compose hostnames | private service discovery/internal ALB | task env / SSM | No |
| data-service | `TELEMETRY_*`, `OUTBOX_*`, `DLQ_*`, `RECONCILIATION_*` | runtime tuning knobs | backlog, retries, retention, health thresholds | compose tuning | keep explicit per environment | SSM / task env | No |
| energy-service | `DATABASE_URL`, `REDIS_URL` | infra endpoint + secret | energy state and pub/sub | local | RDS + ElastiCache | Secrets Manager | Yes |
| energy-service | `REPORTING_SERVICE_BASE_URL`, `DEVICE_SERVICE_BASE_URL` | infra endpoint | downstream calls | compose hostnames | private DNS | task env / SSM | No |
| rule-engine-service | `DATABASE_URL`, `REDIS_URL` | infra endpoint + secret | rules/alerts DB and notification queue | local | RDS + ElastiCache | Secrets Manager | Yes |
| rule-engine-service | `EMAIL_*`, `TWILIO_*` | secrets | notification delivery | local placeholders | real providers/sandboxes | Secrets Manager | Yes |
| analytics-service | `MYSQL_*` or `DATABASE_URL` | infra endpoint + secret | jobs DB | local MySQL | RDS | Secrets Manager | Yes |
| analytics-service | `S3_BUCKET_NAME`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | infra endpoint + secret | dataset access | MinIO values in compose | S3 private bucket/IAM role preferred | task role + SSM | Rotate via IAM, not long-lived keys |
| analytics-service | `REDIS_*`, queue caps, tenant caps | infra endpoint + tuning | durable queue/fairness | compose defaults | tune from staging metrics | task env / SSM | No |
| analytics-service | `DATA_EXPORT_SERVICE_URL`, `DATA_SERVICE_URL`, `DEVICE_SERVICE_URL` | infra endpoint | readiness orchestration | compose hostnames | private service discovery | task env / SSM | No |
| reporting-service | `DATABASE_URL`, `REDIS_URL`, `INFLUXDB_*` | infra endpoint + secret | reports DB/queue/telemetry reads | local | RDS + ElastiCache + Influx | Secrets Manager | Yes |
| reporting-service | `MINIO_*`, `AWS_ENDPOINT_URL*`, `S3_BUCKET_NAME` | infra endpoint + secret | report artifact storage | MinIO defaults | S3 private bucket, prefer IAM role | task role + SSM | Yes |
| reporting-service | `REPORT_WORKER_CONCURRENCY`, queue vars | tuning | report worker scaling | compose defaults | tune after staging | SSM / task env | No |
| waste-analysis-service | `DATABASE_URL`, `INFLUXDB_*`, `MINIO_*`, service URLs | infra endpoint + secret | job persistence, telemetry reads, artifact storage | local | managed/private equivalents | Secrets Manager / task env | Yes |
| waste-analysis-service | `WASTE_*`, `TARIFF_CACHE_TTL_SECONDS` | tuning | job timeout, device concurrency, chunking | defaults in config | tune carefully because jobs run in-process | SSM / task env | No |
| copilot-service | `MYSQL_URL`, `MYSQL_READONLY_URL` | infra endpoint + secret | readonly DB connectivity | compose sets `MYSQL_URL` with `copilot_reader` | enforce readonly DB user/URL separately | Secrets Manager | Yes |
| copilot-service | `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `AI_PROVIDER` | secret | AI provider access | provider-optional | only enable after legal/security approval | Secrets Manager | Yes |
| data-export-service | `INFLUXDB_*`, `CHECKPOINT_DB_*`, `S3_*`, `AWS_*` | infra endpoint + secret | export source/checkpoints/storage | local/MinIO | production Influx, RDS checkpoint DB, S3 | Secrets Manager / task role | Yes |
| ui-web | `AUTH_SERVICE_BASE_URL`, `*_SERVICE_BASE_URL` | public/frontend-internal config | server-side rewrite targets | local or service hostnames | point to internal service DNS if UI stays server-side proxy | task env / SSM | No |
| ui-web | `NEXT_PUBLIC_AUTH_SERVICE_URL`, `NEXT_PUBLIC_API_URL` | public frontend config | browser-visible config | local / placeholder | public app/API URL only | frontend public env | No |

## 9. Networking and Security Plan

### Public entrypoints

- `app.<domain>`:
  - public ALB -> `ui-web`
- `mqtt.<domain>`:
  - NLB/TLS -> private EMQX
- Optional:
  - `api.<domain>` only if mobile/native clients or external systems require direct API access outside the web rewrite path (`Needs runtime verification`)

### Internal-only services

- `auth-service` if web-only access is acceptable
- `device-service`
- `data-service`
- `energy-service`
- `rule-engine-service`
- `analytics-service`
- `reporting-service`
- `waste-analysis-service`
- `copilot-service`
- `data-export-service`
- all workers

### Private data-plane access

- RDS:
  - private subnets only
  - access only from ECS security groups and migration runner
- ElastiCache:
  - private subnets only
  - no public access
- InfluxDB:
  - private access only
- S3:
  - private buckets, VPC endpoint where feasible

### Service-to-service auth assumptions

- Current code trusts internal service identity from `X-Internal-Service` headers (`services/shared/auth_middleware.py`) (`Confirmed from code`)
- Production implication:
  - do not expose internal APIs publicly
  - restrict east-west HTTP by SGs and internal ALB/service discovery
  - optionally add mTLS/service-auth later, but private networking is mandatory for first migration

### CORS and origin handling

- Auth has an explicit origin builder and cookie/origin checks (`services/auth-service/app/main.py`, `services/auth-service/app/cors.py`) (`Confirmed from code`)
- Reporting and waste currently use wildcard CORS (`services/reporting-service/src/main.py`, `services/waste-analysis-service/src/main.py`) (`Confirmed from code`)
- Production action:
  - replace wildcard CORS with explicit app/mobile origins before go-live

### Cookie and auth transport considerations

- Refresh tokens are cookie-based for web, access token held browser-side (`services/auth-service`, `ui-web/lib/authApi.ts`, `ui-web/lib/browserSession.ts`) (`Confirmed from code`)
- Production action:
  - keep auth traffic on HTTPS only
  - set explicit cookie domain/path and verify `SameSite`
  - ensure web rewrites preserve the cookie path `/backend/auth/api/v1/auth`

### Tenant isolation implications at infra level

- Tenant isolation is primarily application- and DB-level today (`Confirmed from code`)
- Infrastructure still must prevent tenant bleed via shared queues and flat network exposure:
  - private internal services
  - strict IAM and bucket policies
  - no public access to raw datasets or report buckets
  - per-environment bucket/prefix separation

### SMTP/provider security

- SMTP credentials must come from Secrets Manager
- SES or enterprise SMTP is preferred over ad hoc mailbox credentials
- Twilio/WhatsApp should stay disabled until contractually approved and tested (`Inferred from usage`)

### HTTPS / TLS requirements

- ACM certs on all public ALBs/NLBs
- TLS for MQTT device ingress
- TLS for browser traffic
- TLS for Redis if ElastiCache is used
- TLS for RDS and Influx where supported

### Flat-network trust assumptions that must change or be verified

- Internal header trust (`X-Internal-Service`) must not cross public boundaries (`Confirmed from code`)
- Compose hostnames and `http://service-name:port` assumptions must be replaced with private DNS/internal ALB endpoints (`Confirmed from code`)
- Localhost and local-origin assumptions in auth allowed origins must be pruned in production (`Confirmed from code`)

## 10. Interactive vs Heavy Workload Separation

| Flow | Classification | Latency sensitivity | Scale sensitivity | Deployment implication | Isolation recommendation |
|---|---|---|---|---|---|
| Login / refresh / logout | Interactive | High | Moderate | keep auth separate from heavy workers | dedicate auth service autoscaling |
| Dashboard summary / fleet snapshot | Interactive | Very high | High read fan-out | device-service API + Redis-backed live fanout | keep off heavy report/ML nodes |
| Device detail / recent telemetry | Interactive | High | Moderate | device-service + data-service read path | isolate from MQTT workers and analytics |
| Telemetry ingestion | Mixed but write-heavy | High for acceptance; async for downstream | Very high | data-service API ingress + telemetry workers | separate API ingress from worker pool |
| Alerts list / acknowledge | Interactive | High | Moderate | rule-engine API | keep separate from notification sender |
| Reports | Mixed | submit/status interactive; generation async | High | reporting API + reporting worker | separate worker pool from API |
| Exports | Heavy async | Low interactive sensitivity | Moderate to high | data-export-service singleton/worker | keep away from user-facing APIs |
| Analytics | Heavy async | low for completion, moderate for submit/status | Very high CPU/memory | analytics API + analytics workers | separate worker autoscaling and memory class |
| Waste analysis | Mixed but effectively heavy | submit/status interactive; generation heavy | High | current API also runs heavy background task | isolate service now; move to worker queue later |
| Notification fan-out | Heavy async | medium | bursty | rule-engine-worker | keep separate from alert API |
| Copilot | Interactive but provider-bound | High | provider-quota sensitive | separate service and timeout controls | keep off core auth/dashboard pools |

### How to prevent heavy jobs from degrading interactive UX

- Keep separate ECS services/task definitions for:
  - `ui-web`
  - `auth-service`
  - `device-service`
  - `data-service` API
  - `analytics-worker`
  - `reporting-worker`
  - `rule-engine-worker`
  - `data-telemetry-worker`
  - `waste-analysis-service` as its own pool because it is heavy in-process
- Do not co-host analytics/reporting/waste workers with dashboard/auth APIs.
- Scale telemetry workers by queue depth and lag, not by API CPU.
- Keep `data-service` MQTT ingest API count controlled separately from `data-telemetry-worker` count.

## 11. Scalability Strategy

### What can be scaled horizontally now

- `ui-web`
- `auth-service`
- `energy-service`
- `rule-engine-service`
- `rule-engine-worker`
- `analytics-service`
- `analytics-worker`
- `reporting-worker`

### What needs sticky/stateful handling or singleton care

- `device-service` maintenance loops (`Confirmed from code`)
- `data-service` MQTT ingress role (`Confirmed from code`)
- `reporting-service` schedule runner (`Confirmed from code`)
- `data-export-service` continuous exporter (`Needs runtime verification`)
- `waste-analysis-service` background tasks in API process (`Confirmed from code`)

### Likely bottlenecks

- MySQL shared across many domains and background systems (`Confirmed from code`)
- InfluxDB query load from reporting, waste, export, and telemetry reads (`Confirmed from code`)
- Redis stream depth during sustained telemetry bursts (`Confirmed from code`)
- waste-analysis in-process background execution under concurrent user demand (`Confirmed from code`)
- analytics worker memory/CPU from ML libraries (`Confirmed from code`)

### Queue and worker scaling model

- Telemetry workers:
  - scale by oldest stage age, stream depth, DLQ growth
  - first knobs: `TELEMETRY_*_WORKERS`, `*_BATCH_SIZE`, backlog thresholds
- Analytics:
  - scale workers by queue depth, pending jobs, job runtime, worker CPU/memory
  - first knobs: `MAX_CONCURRENT_JOBS`, `GLOBAL_ACTIVE_JOB_LIMIT`, tenant caps
- Reporting:
  - scale report workers by queue depth and mean completion time
  - first knobs: `REPORT_WORKER_CONCURRENCY`, queue maxlen, timeout
- Notification fan-out:
  - scale rule workers by pending outbox rows and send latency
  - first knobs: `NOTIFICATION_WORKER_CONCURRENCY`, retry/backoff

### Likely DB and query hotspots

- `device_live_state`, dashboard snapshot reads, and fleet snapshot materialization (`Confirmed from code`)
- analytics job table under status polling and stale lease recovery (`Confirmed from code`)
- reporting schedule claim/update loops (`Confirmed from code`)
- notification outbox and delivery ledger growth (`Confirmed from code`)
- auth `refresh_tokens` and `auth_action_tokens` cleanup/lookup paths (`Confirmed from code`)

### Caching and precompute opportunities already present

- device dashboard snapshots and performance trends (`Confirmed from code`)
- tariff caches in energy/reporting/waste paths (`Confirmed from code`, `Inferred from usage`)
- analytics formatted results and artifact reuse (`Confirmed from code`)

### Recommended first autoscaling policies

- `ui-web`: CPU and request count
- `auth-service`: CPU and ALB request latency
- `device-service`: CPU plus p95 read latency/SSE connection count
- `data-telemetry-worker`: telemetry stage lag and stream depth
- `analytics-worker`: queue depth + CPU + memory
- `reporting-worker`: queue depth + average report time
- `rule-engine-worker`: queue depth + provider send error rate

### Minimum replica strategy

- `ui-web`: 2
- `auth-service`: 2
- `device-service`: 2 API replicas, with only 1 maintenance-active replica until loops are externalized
- `data-service` API: 1-2, but start with 1 until MQTT topology is certified
- `data-telemetry-worker`: 2
- `energy-service`: 2
- `rule-engine-service`: 2
- `rule-engine-worker`: 2
- `analytics-service`: 2
- `analytics-worker`: 2
- `reporting-service`: 2 API replicas with one scheduler-active path preferred
- `reporting-worker`: 2
- `waste-analysis-service`: 1-2 isolated replicas, start conservative
- `copilot-service`: 1-2 depending on launch scope
- `data-export-service`: 1 singleton to start

## 12. Latency and SLA Plan

These are pragmatic first-production targets for this repo shape, not theoretical best-case numbers.

| Endpoint / flow | Target p50 | Target p95 | Hard failure threshold | Measurement source |
|---|---|---|---|---|
| login `POST /api/v1/auth/login` | 300 ms | 1200 ms | 3 s | ALB + auth-service logs |
| refresh `POST /api/v1/auth/refresh` | 250 ms | 1000 ms | 3 s | ALB + auth-service logs |
| dashboard summary | 400 ms | 1500 ms | 4 s | device-service API metrics/logs |
| fleet snapshot page load | 600 ms | 2000 ms | 5 s | device-service + browser RUM if added |
| fleet SSE reconnect | 1 s | 5 s | 15 s | client reconnect telemetry + server logs |
| latest telemetry read | 400 ms | 1500 ms | 5 s | data-service logs |
| alerts list | 400 ms | 1500 ms | 4 s | rule-engine logs |
| report job submission | 300 ms | 1200 ms | 3 s | reporting-service logs |
| report completion | 30 s | 5 min | 15 min | report worker metrics |
| export trigger | 500 ms | 2 s | 5 s | data-export-service logs |
| analytics job submission | 400 ms | 1500 ms | 4 s | analytics-service logs |
| analytics completion single-device | 30 s | 10 min | 20 min | analytics job table + worker logs |
| analytics completion fleet | 2 min | 20 min | 45 min | analytics parent/child job telemetry |
| waste-analysis completion | 45 s | 8 min | 20 min | waste job status table |
| notification fan-out per alert burst | 5 s | 60 s | 10 min | rule worker metrics/outbox |
| copilot response | 2 s | 12 s | 25 s | copilot logs + provider timing |

## 13. Observability and Operations Plan

### Current repo baseline

- Logs:
  - structured logging exists in several services (`Confirmed from code`)
- Metrics:
  - `/metrics` exists for device-service, rule-engine-service, reporting-service; telemetry/analytics expose ops and health endpoints (`Confirmed from code`)
- Health:
  - `/health` is widespread; `/ready` exists in key services (`Confirmed from code`)
- Dashboards/alerts:
  - local Prometheus/Grafana/Alertmanager are present but incomplete and local-only (`monitoring/*`) (`Confirmed from code`)

### Production operations model

- Logs:
  - all ECS task stdout/stderr to CloudWatch Logs
  - JSON-structured fields preserved
- Metrics:
  - scrape or emit:
    - auth request rate/error rate
    - device dashboard latency and SSE connection counts
    - telemetry stage depth/age/DLQ counts
    - energy API latency
    - rule notification queue depth and failure counts
    - analytics queue depth, active jobs, stale recoveries, worker heartbeats
    - report queue depth, stale claims, timeout counts
    - waste active jobs, timeout/failure counts
    - RDS and Redis system metrics
- Tracing:
  - `Not found in repository`
  - recommended after first production migration, but not a day-one blocker

### Must-add monitoring before go-live

- telemetry backlog and lag alerts for every stream stage
- DLQ depth alerts for telemetry, analytics, reporting, and notifications
- worker-heartbeat alarms:
  - analytics worker table heartbeat
  - telemetry worker Redis heartbeat
- RDS:
  - CPU, memory proxy, connections, replica lag if used, slow query log
- Redis:
  - memory, evictions, command latency, stream depth
- InfluxDB:
  - write failure rate, query latency, retention enforcement status
- S3/object storage:
  - bucket errors, request failure rate, missing-object alarms on key read paths
- Auth/session:
  - login failures, refresh failures, token revoked/stale spikes
- MQTT:
  - broker connections, dropped messages, unauthenticated connection attempts

## 14. Backup, Restore, and Disaster Recovery

### Database backup expectations

- RDS automated backups enabled
- point-in-time recovery enabled
- pre-cutover manual snapshot
- restore drill before production launch

### Object storage backup/versioning

- S3 versioning on
- lifecycle policies by bucket/prefix
- replication optional later; not mandatory for first migration if restore-tested

### InfluxDB backup expectations

- explicit retention policy configured
- scheduled backup/export plan documented and tested
- restore test for one representative tenant/device/date range

### Secrets backup approach

- Secrets Manager authoritative source
- secret values not stored in repo or task definitions
- backup by infra control plane, plus documented break-glass rotation process

### Rollback-friendly deployment expectations

- immutable container images in ECR
- environment variables versioned in parameter/secrets systems
- DB migration order documented and reversible where possible
- do not run irreversible data transforms during cutover without snapshot

### Recovery priorities

1. auth-service + RDS
2. device-service + data-service + Redis + Influx + MQTT
3. reporting/rule notifications
4. analytics/export
5. copilot

## 15. Production Risks and Gaps

| Risk | Evidence | Impact | Severity | Mitigation |
|---|---|---|---|---|
| Local-only compose could be misused as “production” | `docker-compose.yml:1-3` | insecure/public single-host deployment | Critical | build real AWS env; ban compose promotion |
| Internal auth bypass is header-based | `services/shared/auth_middleware.py` | public exposure of internal APIs would bypass end-user auth assumptions | Critical | private networking only; SG restrictions; internal-only ALBs |
| Wildcard CORS on reporting and waste | `services/reporting-service/src/main.py`, `services/waste-analysis-service/src/main.py` | browser abuse, origin confusion | High | replace with explicit origins |
| Device-service schedulers run inside API replicas | `services/device-service/app/__init__.py:264-525` | duplicate maintenance work or racey background load | High | singleton maintenance task or externalize scheduler |
| Data-service API owns MQTT connection | `services/data-service/src/main.py`, `src/handlers/mqtt_handler.py` | scaling API replicas may duplicate ingress or complicate broker semantics | High | certify single-active/shared-subscription model before autoscaling |
| Waste-analysis jobs are in-process background tasks | `services/waste-analysis-service/src/handlers/waste_analysis.py` | job loss on replica restart and interactive/heavy contention | High | isolate service now; move to durable worker queue later |
| Observability stack is incomplete for prod | `monitoring/prometheus/prometheus.yml`, `monitoring/alertmanager/alertmanager.yml` | blind spots during cutover and incident response | High | CloudWatch baseline plus expanded metrics/alarming |
| Shared MySQL across many domains | `memory-appendix-db.md`, service configs | DB saturation and migration coupling | High | right-size RDS, performance test, watch connection pools |
| InfluxDB is not replaceable without compatibility | service Influx clients | cloud migration could accidentally trigger rewrite | High | keep Influx-compatible target |
| Local/default credentials exist in repo and compose | `docker-compose.yml`, `.env.production.example`, `.env` | credential leakage and unsafe habits | High | move all secrets to Secrets Manager and rotate |
| Alertmanager config is host-local | `monitoring/alertmanager/alertmanager.yml` | alerts silently fail in prod if copied | Medium | replace with real alert routing |
| Reporting scheduler runs in API | `services/reporting-service/src/main.py`, `src/tasks/scheduler.py` | duplicate schedule scans if many replicas | Medium | keep single scheduler-active replica or external scheduler |
| Copilot production scope unclear | `services/copilot-service` config is provider-optional | launch uncertainty and provider risk | Medium | make copilot an optional phase-gated subsystem |
| WebSocket auth path in data-service not fully certified from static scan | `memory-appendix-api.md` notes | possible auth regression under prod ingress | Medium | explicit runtime verification before go-live |
| Mobile/public API routing not fully discoverable from repo scan | `Needs runtime verification` | ingress design mismatch | Medium | confirm client entrypoints before DNS finalization |

## 16. Phased Migration Plan

### Phase 1: Architecture and inventory freeze

- Objective:
  - freeze deployable scope and target production slice
- Exact tasks:
  - lock service list and first-wave feature set
  - confirm whether copilot is in wave 1
  - confirm mobile/direct API entrypoints
  - freeze schema/migration baseline
- Blockers:
  - unresolved ingress expectations
- Validation required:
  - architecture sign-off
- Exit criteria:
  - approved production scope and service inventory

### Phase 2: Managed infra provisioning

- Objective:
  - stand up production-like staging infrastructure
- Exact tasks:
  - provision VPC, subnets, SGs
  - provision RDS, Redis, S3, Influx-compatible target, EMQX target
  - provision ALB/NLB, ACM, Route53
  - provision ECR repositories
- Blockers:
  - no finalized CIDR/network policy
- Validation required:
  - connectivity tests from ECS to all private services
- Exit criteria:
  - all managed/private dependencies reachable from staging tasks

### Phase 3: Config and secrets migration

- Objective:
  - remove local-secret assumptions
- Exact tasks:
  - map envs from section 8 into Secrets Manager/SSM
  - inject per-service runtime config
  - disable anonymous MQTT
  - define bucket names, lifecycle, and IAM access
- Blockers:
  - unresolved provider credentials
- Validation required:
  - `validate_startup_contract()` passes for every service
- Exit criteria:
  - all services start in staging without `.env`

### Phase 4: Staging deployment

- Objective:
  - deploy full stack against managed/private dependencies
- Exact tasks:
  - push images to ECR
  - deploy `ui-web`, APIs, workers as separate ECS services
  - wire ingress, DNS, TLS
  - enable CloudWatch logging and alarms
- Blockers:
  - missing health/readiness route integration
- Validation required:
  - basic service health and smoke requests
- Exit criteria:
  - staging stack runs end-to-end

### Phase 5: State and data migration rehearsal

- Objective:
  - prove DB/Influx/S3 migration process
- Exact tasks:
  - rehearse MySQL export/import
  - rehearse Influx history copy
  - sync object storage artifacts
  - measure cutover window
- Blockers:
  - missing data copy automation
- Validation required:
  - row/object counts, representative telemetry/report reads
- Exit criteria:
  - repeatable rehearsed runbook with timings

### Phase 6: Subsystem performance tuning

- Objective:
  - tune queues, workers, and heavy workloads
- Exact tasks:
  - telemetry burst test
  - dashboard/SSE load test
  - analytics concurrency test
  - report and waste job concurrency test
  - RDS and Redis pool tuning
- Blockers:
  - insufficient representative data volume
- Validation required:
  - meet first-pass SLAs from section 12
- Exit criteria:
  - tuned staging parameters and capacity plan

### Phase 7: Production cutover

- Objective:
  - migrate production traffic/state safely
- Exact tasks:
  - pre-cutover freeze
  - final data sync
  - deploy production services
  - resume ingress
- Blockers:
  - unresolved NO-GO findings
- Validation required:
  - smoke suite, tenant isolation, dashboard freshness, job completion
- Exit criteria:
  - stable post-cutover service and data validation

### Phase 8: Post-cutover validation and autoscaling tuning

- Objective:
  - stabilize and optimize
- Exact tasks:
  - monitor queues and DB load
  - adjust worker counts and backlog thresholds
  - finalize alarms and dashboards
  - schedule restore drill
- Blockers:
  - recurring incident patterns
- Validation required:
  - seven-day operational stability window
- Exit criteria:
  - platform operating within SLOs with rollback no longer needed

## 17. Cutover Plan

### Pre-cutover checklist

- RDS snapshot taken
- source MySQL backup verified
- source Influx backup/export ready
- S3 buckets created and IAM validated
- production secrets injected
- staging runbook rehearsal passed
- NO-GO findings closed or explicitly accepted

### Schema migration order

1. provision empty RDS
2. run all service Alembic migrations
3. import relational data
4. run any post-import integrity checks
5. start services against migrated DB in isolated mode

### Service deployment order

1. foundational dependencies:
   - RDS
   - Redis
   - InfluxDB
   - S3
   - EMQX
2. auth-service
3. device-service
4. energy-service
5. rule-engine-service
6. data-service API
7. telemetry workers
8. reporting-service
9. reporting-worker
10. analytics-service
11. analytics-workers
12. data-export-service
13. waste-analysis-service
14. copilot-service if in scope
15. ui-web

### Worker enablement order

1. telemetry workers
2. rule-engine worker
3. reporting worker
4. analytics workers
5. data-export service
6. waste-analysis traffic only after heavy-path checks

### Smoke test order

1. auth login/refresh/logout
2. tenant-scoped `/me`
3. device list and fleet summary
4. telemetry latest/latest-batch
5. alert list
6. report submission/status
7. analytics submission/status
8. waste submission/status
9. notification test path
10. copilot curated question if in scope

### Rollback triggers

- cross-tenant data exposure
- telemetry ingest lag grows without recovery
- dashboard stale/failing for core tenants
- login/refresh broken for production users
- report/analytics/waste queues dead-lettering abnormally
- DB integrity mismatch discovered

### Rollback steps

1. pause MQTT/device ingress to target
2. stop worker services in target
3. remove UI traffic from target ALB
4. re-point traffic and services to prior environment
5. restore from pre-cutover snapshot only if source data was altered
6. preserve failed target environment for forensic review

## 18. Validation and Certification Checklist

### Must pass before production

- Auth:
  - login, refresh, logout, invite, reset
- Tenant isolation:
  - tenant-scoped APIs return only owned rows
  - super-admin explicit tenant selection works
- Telemetry ingest:
  - MQTT accept, Influx write, telemetry readback, downstream projection
- Dashboard freshness:
  - fleet summary/fleet snapshot/device detail reflect fresh telemetry
- Alerts/notifications:
  - rule evaluation, alert creation, notification outbox delivery
- Reports:
  - submit, complete, download
- Analytics:
  - submit, queue, worker completion, formatted results
- Waste analysis:
  - submit, complete, download, quality-gate failure path
- Exports:
  - force export and object landed in S3
- Backups/restore:
  - RDS restore test
  - object storage restore/read test
  - Influx restore/read test
- Observability:
  - logs, alarms, metrics, queue depth visibility
- Latency/SLA:
  - interactive paths meet target p95 in staging load test
- Load checks:
  - telemetry burst and dashboard concurrency
- Multi-tenant fairness:
  - analytics/reporting queues enforce fairness without starvation

### May pass shortly after production if scoped carefully

- Copilot provider-enabled production path, if copilot is explicitly deferred
- Secondary analytics retrainer optimization
- Managed Prometheus/Grafana expansion beyond baseline CloudWatch alarms
- EMQX to AWS IoT Core evaluation

## 19. AWS Resource Checklist

This repository already contains an AWS-oriented production example and AWS deployment guardrail doc (`.env.production.example`, `docs/aws_production_deployment.md`), so AWS is a justified target.

| Resource | Why needed | Depends / used by | Required before staging | Required before production |
|---|---|---|---|---|
| VPC | private networking boundary for services/data | all | Yes | Yes |
| public subnets | ALB/NLB placement | `ui-web`, MQTT ingress | Yes | Yes |
| private app subnets | ECS services and workers | all APIs/workers | Yes | Yes |
| private data subnets | RDS/Redis/Influx/EMQX | stateful infra | Yes | Yes |
| security groups | enforce east-west and data-plane access | all | Yes | Yes |
| RDS MySQL | primary relational store | auth, device, data, analytics, reporting, rule-engine, waste | Yes | Yes |
| S3 buckets | datasets, reports, waste reports, snapshots, exports | analytics, reporting, waste, export, device snapshots | Yes | Yes |
| ElastiCache Redis | streams, pub/sub, token revocation | auth, data, device, analytics, reporting, rule-engine | Yes | Yes |
| Influx-compatible deployment | telemetry time-series | data, reporting, waste, export | Yes | Yes |
| public ALB | web ingress | `ui-web` | Yes | Yes |
| internal ALB or service discovery | private service-to-service HTTP | internal APIs | Yes | Yes |
| NLB | MQTT TLS ingress | EMQX | Yes | Yes |
| ACM | TLS certs | ALB/NLB | Yes | Yes |
| Route53 | DNS | web and MQTT endpoints | Yes | Yes |
| ECS cluster/services | run APIs and workers | all app services | Yes | Yes |
| ECR | image registry | all deployable services | Yes | Yes |
| CloudWatch Logs/alarms | baseline ops visibility | all | Yes | Yes |
| Secrets Manager / SSM | secrets and config injection | all | Yes | Yes |
| IAM task roles | least-privilege S3/parameter access | analytics, reporting, export, device snapshots, all services | Yes | Yes |
| Bastion or SSM access path | migrations/debugging | RDS/Influx/EMQX admin | Yes | Yes |

## 20. Final Recommendation

### Go / No-Go

- Go for starting production infrastructure migration now: `GO`
- Go for direct production cutover now: `NO-GO`

### Exact next 10 things to do in order

1. Freeze the first production scope and decide whether `copilot-service` is in or explicitly deferred.
2. Confirm the required public entrypoints for web, mobile, and device MQTT so ingress is designed correctly.
3. Provision staging AWS networking, RDS, Redis, S3, and an Influx-compatible target.
4. Provision private EMQX for staging and disable anonymous access.
5. Move all production-impacting env vars and secrets from `.env` assumptions into Secrets Manager/SSM.
6. Deploy every API and worker role to staging as separate ECS services, not one combined stack.
7. Tighten CORS/origin/cookie settings for auth, reporting, and waste-analysis before any external exposure.
8. Rehearse full state migration: MySQL, Influx telemetry, and object storage artifacts.
9. Run the repo-native preprod validation plus telemetry burst, dashboard freshness, queue, and tenant-isolation tests against staging.
10. Only after those pass, schedule a maintenance-window cutover with queue drain, final sync, smoke tests, and explicit rollback triggers.
