# Shivex Monitoring Stack

## Overview

The monitoring stack runs as Docker Compose services under the `monitoring` profile. It is opt-in and does not start by default with `docker compose up`.

## Services

| Service | Image | Port | Purpose |
|---|---|---|---|
| Prometheus | `prom/prometheus:v2.53.1` | [9090](http://localhost:9090) | Metrics collection, alert evaluation, 30d retention |
| Alertmanager | `prom/alertmanager:v0.27.0` | [9093](http://localhost:9093) | Alert routing, email notification |
| Grafana | `grafana/grafana:11.1.4` | [3001](http://localhost:3001) | Dashboards and visualization |
| node_exporter | `prom/node-exporter:v1.8.0` | 9100 | Host CPU, memory, disk, load metrics |
| redis-exporter | `oliver006/redis_exporter:v1.67.0` | Internal only | Redis memory, availability, and health metrics |
| blackbox-exporter | `prom/blackbox-exporter:v0.25.0` | 9115 | HTTP endpoint reachability probing |

## Starting the Monitoring Stack

### Local Development

```bash
docker compose --env-file .env.local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  --profile monitoring up -d
```

### Production

```bash
# Ensure .env contains all ALERT_SMTP_* variables (see below)
docker compose --env-file .env \
  -f docker-compose.yml \
  --profile monitoring up -d
```

## Accessing Services

| Service | URL | Notes |
|---|---|---|
| Prometheus | http://localhost:9090 | Targets, queries, alerts |
| Grafana | http://localhost:3001 | Login: `admin` / `admin123` (override via env vars) |
| Alertmanager | http://localhost:9093 | Alert status, silences, receiver config |
| blackbox-exporter | http://localhost:9115 | Internal; no UI, /metrics only |

## Alert Routing (Email)

Alertmanager is configured to deliver alerts via email using SMTP. It reuses the same SMTP credentials that the app services (auth-service, rule-engine-service) already use for password resets and notification emails.

### SMTP Configuration (Unified with App Services)

Alertmanager reads from the same env vars as the rest of the Shivex stack. If you already have email working for password resets or rule-engine notifications, Alertmanager will pick up those credentials automatically.

| Variable | Resolves from | Default | Description |
|---|---|---|---|
| `ALERT_SMTP_HOST` | → `EMAIL_SMTP_HOST` → `SMTP_SERVER` | `smtp.gmail.com` | SMTP server hostname |
| `ALERT_SMTP_PORT` | → `EMAIL_SMTP_PORT` | `587` | SMTP server port |
| `ALERT_SMTP_FROM` | → `EMAIL_FROM_ADDRESS` → `EMAIL_SENDER` | `alerts@shivex.ai` | Sender email address |
| `ALERT_SMTP_USERNAME` | → `EMAIL_SMTP_USERNAME` → `EMAIL_SENDER` | _(empty)_ | SMTP auth username |
| `ALERT_SMTP_PASSWORD` | → `EMAIL_SMTP_PASSWORD` → `EMAIL_PASSWORD` | _(empty)_ | SMTP auth password |
| `ALERT_SMTP_TO` | _(monitoring-specific)_ | `ops@shivex.ai` | Alert recipient email address |
| `ALERTMANAGER_EXTERNAL_URL` | _(monitoring-specific)_ | `http://localhost:9093` | Public URL used in alert emails for “View in Alertmanager” links |

**How it works**: Each `ALERT_SMTP_*` variable first checks for an explicit monitoring-specific value. If not set, it falls back to the app-level `EMAIL_SMTP_*` / `SMTP_SERVER` / `EMAIL_SENDER` / `EMAIL_PASSWORD` variables that already exist in `.env`. This means:

- **If email already works for password resets**: Alertmanager email will work with zero extra configuration, as long as `ALERT_SMTP_TO` is set to the desired recipient.
- **To override for monitoring only**: Set `ALERT_SMTP_HOST`, `ALERT_SMTP_USERNAME`, etc. explicitly in `.env` to use a different SMTP account for alerts.
- **Only `ALERT_SMTP_TO` has no app-level fallback** — this must be set to tell Alertmanager where to send alerts.

### Minimum Production Setup

If your `.env` already has `SMTP_SERVER`, `EMAIL_SENDER`, and `EMAIL_PASSWORD` (used by auth-service and rule-engine-service), you only need to add one line:

```env
ALERT_SMTP_TO=oncall@your-domain.com
```

If you want to use a separate SMTP account for alerts, set the full chain:

```env
ALERT_SMTP_HOST=smtp.gmail.com
ALERT_SMTP_PORT=587
ALERT_SMTP_FROM=alerts@your-domain.com
ALERT_SMTP_USERNAME=alerts@your-domain.com
ALERT_SMTP_PASSWORD=your-app-password
ALERT_SMTP_TO=oncall@your-domain.com
```

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833) instead of the account password.

### Routing Behavior

- All alerts go to the `email-default` receiver
- **Critical** alerts repeat every 30 minutes
- Other alerts repeat every 4 hours
- Resolved alerts trigger a follow-up email (`send_resolved: true`)
- Email subject: `[Shivex FIRING] AlertName (severity)`

### Verifying Alerts Work

1. Start the monitoring stack
2. Open Alertmanager at http://localhost:9093
3. Check the "Status" page — the rendered config should show your SMTP host and recipient
4. Open Prometheus at http://localhost:9090/alerts to see active alerts
5. To force a test: temporarily lower an alert threshold (e.g., set `HostCPUHigh` to `> 1`), wait for it to fire, then revert

## Grafana Dashboards

All dashboards are auto-provisioned from `monitoring/grafana/dashboards/`:

| Dashboard | UID | Panels |
|---|---|---|
| Host Health | `host-health` | CPU, memory, disk, load |
| Service Status Overview | `service-status` | All scrape targets up/down, scrape duration, active alerts |
| Device Dashboard SLO | `device-dashboard-slo` | Fleet stream lag, snapshot age, scheduler lag |
| Endpoint Health | `endpoint-health` | Probe status, latency, uptime %, failures |

Grafana credentials are controlled by:
- `GRAFANA_ADMIN_USER` (default: `admin`)
- `GRAFANA_ADMIN_PASSWORD` (default: `admin123`)

## Alert Rules

Rules live in `monitoring/prometheus/rules/`:

| File | Rules | Coverage |
|---|---|---|
| `infra-alerts.yml` | 7 | Host CPU/memory/disk, service target down |
| `redis-alerts.yml` | 3 | Redis availability and maxmemory pressure |
| `endpoint-health-alerts.yml` | 3 | Endpoint probe failure, slow response, sustained unavailability |
| `device-slo-alerts.yml` | 8 | Dashboard snapshot freshness, fleet stream lag, cost data |
| `queue-worker-slo-alerts.yml` | 25 | Analytics, reporting, waste, rule-engine, telemetry pipeline |

## Prometheus Scrape Targets

| Job | Target | Method |
|---|---|---|
| prometheus | `prometheus:9090` | Direct scrape |
| device-service | `device-service:8000/metrics` | App metrics |
| data-service | `data-service:8081/metrics` | App metrics |
| analytics-service | `analytics-service:8003/metrics` | App metrics |
| reporting-service | `reporting-service:8085/metrics` | App metrics |
| waste-analysis-service | `waste-analysis-service:8087/metrics` | App metrics |
| rule-engine-service | `rule-engine-service:8002/metrics` | App metrics |
| node-exporter | `node-exporter:9100` | Host metrics |
| redis-exporter | `redis-exporter:9121` | Redis metrics |
| blackbox-http | Via blackbox-exporter | HTTP probe (9 endpoints) |

## What Remains Unfinished

- **Services without `/metrics`**: `energy-service`, `auth-service`, `data-export-service`, and `copilot-service` have no Prometheus metrics endpoint. They are probed via blackbox but lack application-level observability.
- **Worker containers**: No dedicated HTTP health endpoint or Prometheus metrics yet.
- **Prometheus lifecycle API**: `--web.enable-lifecycle` is active without authentication. Consider disabling or adding basic auth for production.
- **Secrets in git**: `.env` and `.env.local` contain real credentials and are tracked in git. These should be untracked and rotated.
- **Alertmanager `group_by`**: Currently groups by `["alertname", "service"]`. The old `snapshot_key` grouping was removed, but further routing refinement (e.g., separate routes for infra vs. app alerts) can be added later.
