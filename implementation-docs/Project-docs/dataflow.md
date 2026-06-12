# FactoryOPS Data Flow Documentation

> Complete data flow documentation for the AI FactoryOPS platform.
> This document provides a comprehensive understanding of how data moves through the entire system.
> Last Updated: March 2026

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Data Ingestion Flow](#2-data-ingestion-flow)
3. [Service Processing Pipelines](#3-service-processing-pipelines)
4. [Storage Layer](#4-storage-layer)
5. [Inter-Service Communication](#5-inter-service-communication)
6. [Database Table Relationships](#6-database-table-relationships)
7. [API Reference](#7-api-reference)
8. [Error Handling & DLQ Flows](#8-error-handling--dlq-flows)
9. [Complete Data Flow Diagrams](#9-complete-data-flow-diagrams)

---

## 1. System Architecture Overview

### 1.1 Service Inventory

| Service | Container Name | Port | Purpose |
|---------|---------------|------|---------|
| **device-service** | device-service | 8000 | Device management, dashboard, heartbeat, property sync |
| **data-service** | data-service | 8081 | Telemetry ingestion, MQTT handling, enrichment, InfluxDB storage |
| **rule-engine-service** | rule-engine-service | 8002 | Rule evaluation, alerting, notifications |
| **analytics-service** | analytics-service | 8003 | ML analytics (anomaly detection, failure prediction, forecasting) |
| **analytics-worker** | analytics-worker | - | Background ML job processing |
| **data-export-service** | data-export-service | 8080 | Export telemetry to S3/MinIO |
| **reporting-service** | reporting-service | 8085 | Energy reports, cost analysis, power quality |
| **waste-analysis-service** | waste-analysis-service | 8087 | Waste analysis reports |
| **copilot-service** | copilot-service | 8007 | AI copilot for querying data via LLM |
| **ui-web** | ui-web | 3000 | Next.js web application |

### 1.2 Infrastructure Services

| Service | Container Name | Port | Purpose |
|---------|---------------|------|---------|
| **MySQL** | energy_mysql | 3306 | Relational database |
| **InfluxDB** | influxdb | 8086 | Time-series database |
| **MinIO** | minio | 9000/9001 | S3-compatible object storage |
| **EMQX** | emqx | 1883/18083 | MQTT broker |
| **Redis** | analytics_redis | 6379 | Job queue, caching |
| **Prometheus** | prometheus | 9090 | Metrics collection |
| **Grafana** | grafana | 3001 | Dashboards & visualization |

### 1.3 Network Configuration

All services communicate via the `energy-net` Docker bridge network.
External ports are mapped as follows:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL PORTS                                    │
├──────────┬──────────┬──────────┬──────────┬──────────┬────────────────────┤
│  3000    │   8000   │   8002   │   8003   │   8080   │     8081           │
│  (UI)    │ (device) │  (rule)  │(analytics)│ (export) │    (data)         │
├──────────┴──────────┴──────────┴──────────┴──────────┴────────────────────┤
│                         8085         8087         8007        9000        │
│                    (reporting)   (waste)      (copilot)   (MinIO)        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Data Ingestion Flow

### 2.1 MQTT Telemetry Ingestion (Primary)

The primary data ingestion path is via MQTT protocol from IoT devices.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  IoT Device │────▶│    EMQX    │────▶│ data-service│────▶│  InfluxDB   │
│ (MQTT Pub)  │     │ (Broker)   │     │ (Consumer)  │     │ (Storage)   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
     Port 1883           Topic:            Validation,        Bucket:
     QoS: 1              devices/+/telemetry  Enrichment       telemetry
```

#### MQTT Connection Details

| Property | Value |
|----------|-------|
| Broker Host | `localhost` (or deployment host) |
| Port | `1883` (MQTT), `8883` (MQTT/TLS), `8083` (WebSocket) |
| Topic Pattern | `devices/{device_id}/telemetry` |
| QoS | `1` (at least once delivery) |
| Dashboard | `http://localhost:18083` (default credentials: admin/public) |

#### Telemetry Payload Format

**Required JSON fields:**

```json
{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-18T10:00:00Z",
  "schema_version": "v1"
}
```

**Measurement values (all numeric):**

```json
{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-18T10:00:00Z",
  "schema_version": "v1",
  "voltage": 230.5,
  "current": 12.5,
  "power": 2500.0,
  "power_factor": 0.95,
  "energy_kwh": 1250.5,
  "temperature": 45.0,
  "frequency": 50.0
}
```

#### Accepted Field Aliases

| Canonical Field | Aliases Accepted |
|----------------|------------------|
| `current` | `current_l1`, `current_l2`, `current_l3`, `phase_current`, `i_l1` |
| `voltage` | `voltage_l1`, `voltage_l2`, `voltage_l3`, `v_l1` |
| `power_factor` | `pf` |
| `power` | `active_power`, `kw` (if `kw`, treated as kW directly) |

**Important:** `power` and `active_power` are interpreted as Watts (W). The backend normalizes to kW internally. Use `kw` if the value is already in kW.

### 2.2 HTTP API Ingestion (Secondary)

Secondary ingestion via HTTP REST API:

```
┌─────────────┐     ┌─────────────┐
│   HTTP      │────▶│ data-service│
│   Client    │     │  /api/v1/data/telemetry
└─────────────┘     └─────────────┘
```

**Example curl command:**

```bash
curl -X POST "http://localhost:8081/api/v1/data/telemetry" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "COMPRESSOR-001",
    "timestamp": "2026-03-18T10:00:00Z",
    "power": 2500.0,
    "current": 12.5,
    "voltage": 230.5
  }'
```

---

## 3. Service Processing Pipelines

### 3.1 data-service Processing Pipeline

The data-service is the central ingestion point for all telemetry data.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           data-service Pipeline                            │
└─────────────────────────────────────────────────────────────────────────────┘

     MQTT Message                HTTP Request
         │                           │
         ▼                           ▼
┌─────────────────┐         ┌─────────────────┐
│  MQTT Handler  │         │  REST Handler  │
│ (emqx broker)  │         │  (FastAPI)     │
└────────┬────────┘         └────────┬────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐         ┌─────────────────┐
│   Validation   │         │   Validation   │
│   Service      │         │   Service      │
└────────┬────────┘         └────────┬────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐         ┌─────────────────┐
│  Enrichment    │         │  Enrichment    │
│   Service      │         │   Service      │
│ (fetch device  │         │ (fetch device  │
│  metadata)     │         │  metadata)     │
└────────┬────────┘         └────────┬────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐         ┌─────────────────┐
│  InfluxDB      │         │  InfluxDB      │
│  Repository    │         │  Repository    │
└────────┬────────┘         └────────┬────────┘
         │                          │
    ┌────┴────┐                ┌────┴────┐
    ▼         ▼                ▼         ▼
┌───────┐ ┌──────────┐   ┌───────┐ ┌──────────┐
│ Rule  │ │WebSocket │   │ Rule  │ │WebSocket │
│Engine │ │Broadcast │   │Engine │ │Broadcast │
│Client │ │          │   │Client │ │          │
└───┬───┘ └──────────┘   └───┬───┘ └──────────┘
    │                       │
    ▼                       ▼
┌──────────────┐     ┌──────────────┐
│  Device      │     │  Real-time   │
│  Sync        │     │  Dashboard  │
│  Worker      │     │  Updates    │
└──────────────┘     └──────────────┘
```

#### Step-by-Step Processing

**Step 1: Validation**
- Validates required fields: `device_id`, `timestamp`
- Checks schema version
- Validates numeric ranges
- Rejects invalid payloads

**Step 2: Enrichment**
- Fetches device metadata from device-service
- Adds device_name, device_type, location, tenant_id
- Adds enrichment_status flag

**Step 3: InfluxDB Storage**
- Writes to `telemetry` bucket
- Measurement: `device_telemetry`
- Tags: device_id, device_type, tenant_id, schema_version
- Fields: All numeric telemetry values

**Step 4: Async Processing**
- Calls rule-engine-service for evaluation
- Broadcasts via WebSocket for real-time UI updates
- Syncs to device-service (heartbeat + properties)

### 3.2 rule-engine-service Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      rule-engine-service Pipeline                          │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
  │data-service │     │ Load Rules │     │  Evaluate   │
  │  calls      │────▶│   from     │────▶│   Rules     │
  │ /evaluate   │     │   MySQL    │     │             │
  └─────────────┘     └─────────────┘     └──────┬──────┘
                                                 │
                    ┌─────────────┐              │
                    │ Not         │◀─────────────┤
                    │ Triggered   │              │
                    └─────────────┘              │
                                                 ▼
                                          ┌─────────────┐
                                          │  Triggered  │
                                          └──────┬──────┘
                                                 │
                    ┌─────────────┐    ┌──────────┴─────────┐
                    │   MySQL    │    │   Send             │
                    │  alerts    │    │   Notifications   │
                    │   table    │    │   (Email/Webhook) │
                    └─────────────┘    └───────────────────┘
```

#### Rule Types

**1. Threshold Rules**
```json
{
  "rule_type": "threshold",
  "property": "current_a",
  "condition": ">",
  "threshold": 55.0,
  "device_ids": ["COMPRESSOR-001", "COMPRESSOR-002"]
}
```

**2. Time-Based Rules**
```json
{
  "rule_type": "time_based",
  "property": "power_kw",
  "condition": ">",
  "threshold": 5.0,
  "time_window_start": "22:00",
  "time_window_end": "06:00",
  "timezone": "Asia/Kolkata"
}
```

### 3.3 analytics-service Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       analytics-service Pipeline                           │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
  │   API       │────▶│   Job       │────▶│  Redis      │
  │  Request    │     │   Queue     │     │  Stream     │
  └─────────────┘     └─────────────┘     └──────┬──────┘
                                                  │
  ┌─────────────┐     ┌─────────────┐              │
  │   Worker    │◀────│  Consume    │◀─────────────┤
  │  (async)    │     │   Job       │              │
  └──────┬──────┘     └─────────────┘              │
         │                                        │
         ▼                                        │
  ┌─────────────┐     ┌─────────────┐              │
  │  Fetch      │────▶│   ML        │              │
  │  Telemetry  │     │  Models     │
  │ (InfluxDB)  │     │ (Anomaly,   │
  └─────────────┘     │  Failure,   │
                      │  Forecast)  │
                      └──────┬──────┘
                             │
                             ▼
                      ┌─────────────┐
                      │   Store    │
                      │  Results   │
                      │ (MySQL +   │
                      │   S3)      │
                      └─────────────┘
```

#### Analysis Types

| Analysis Type | Model | Purpose |
|---------------|-------|---------|
| Anomaly Detection | Isolation Forest, CUSUM | Detect unusual device behavior |
| Failure Prediction | LSTM Classifier | Predict equipment failures |
| Degradation Tracking | Time-series analysis | Track equipment health over time |
| Forecasting | Prophet, ARIMA | Predict future energy consumption |

### 3.4 reporting-service Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       reporting-service Pipeline                          │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
  │   Report    │────▶│   Report    │────▶│   InfluxDB │
  │   Request   │     │   Task      │     │   Reader   │
  │             │     │  (Celery)   │     │            │
  └─────────────┘     └─────────────┘     └──────┬──────┘
                                                  │
  ┌─────────────┐     ┌─────────────┐              │
  │   Cost      │◀────│   Energy   │◀─────────────┤
  │   Engine    │     │   Engine    │              │
  └──────┬──────┘     └──────┬──────┘              │
         │                   │                     │
         ▼                   ▼                     │
  ┌─────────────┐     ┌─────────────┐              │
  │   Demand    │     │  Power      │              │
  │   Engine    │     │  Quality    │              │
  └──────┬──────┘     └──────┬──────┘              │
         │                   │                     │
         └─────────┬─────────┘                     │
                   ▼                               │
          ┌─────────────┐                          │
          │   PDF       │◀─────────────────────────┘
          │   Builder   │
          └──────┬──────┘
                 │
                 ▼
          ┌─────────────┐     ┌─────────────┐
          │   MinIO/S3  │────▶│    Email    │
          │   Storage   │     │  Notifcation│
          └─────────────┘     └─────────────┘
```

#### Report Types

| Report Type | Description | Engines Used |
|-------------|-------------|--------------|
| Energy Consumption | Daily/weekly/monthly energy usage | Energy Engine, Cost Engine |
| Comparison | Period-over-period comparison | Comparison Engine, Energy Engine |
| Power Quality | Voltage, current, PF analysis | Power Quality Engine, Reactive Engine |
| Demand | Peak demand analysis | Demand Engine, Cost Engine |
| Load Factor | Equipment utilization | Load Factor Engine |

### 3.5 waste-analysis-service Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    waste-analysis-service Pipeline                         │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
  │   Waste     │────▶│   Waste     │────▶│  InfluxDB   │
  │   Job       │     │   Engine    │     │   Reader    │
  │   Request   │     │             │     │             │
  └─────────────┘     └─────────────┘     └──────┬──────┘
                                                  │
                    ┌─────────────┐              │
                    │  Wastage    │◀─────────────┤
                    │ Calculators│              │
                    └──────┬──────┘              │
                           │                     │
         ┌─────────────────┼─────────────────────┘
         │                 │
         ▼                 ▼
  ┌─────────────┐   ┌─────────────┐
  │   Idle      │   │  Over       │
  │  Analysis  │   │ Consumption │
  └──────┬──────┘   └──────┬──────┘
         │                 │
         ▼                 ▼
  ┌─────────────┐   ┌─────────────┐
  │  Standby    │   │  Unoccupied │
  │  Power      │   │  Time       │
  └──────┬──────┘   └──────┬──────┘
         │                 │
         └────────┬────────┘
                  │
                  ▼
          ┌─────────────┐
          │   Summary   │
          │   Results   │
          │  (MySQL)    │
          └─────────────┘
```

### 3.6 data-export-service Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      data-export-service Pipeline                          │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
  │  Timer      │────▶│   Check     │────▶│   Query     │
  │ (every 60s) │     │  Checkpoint │     │  InfluxDB   │
  └─────────────┘     └─────────────┘     └──────┬──────┘
                                                  │
  ┌─────────────┐     ┌─────────────┐              │
  │  Update     │◀────│   Write     │◀─────────────┤
  │  Checkpoint │     │   S3/MinIO  │              │
  │  (MySQL)    │     │  (Parquet)  │              │
  └─────────────┘     └─────────────┘
```

---

## 4. Storage Layer

### 4.1 MySQL Database

**Database Name:** `ai_factoryops`
**Host:** `mysql` (container), port `3306`
**Credentials:** User: `energy`, Password: `energy`

#### Complete Table List

| Table | Service | Description |
|-------|---------|-------------|
| `devices` | device-service | Master device registry |
| `device_shifts` | device-service | Shift schedules per device |
| `parameter_health_config` | device-service | Health score thresholds |
| `device_performance_trends` | device-service | Performance snapshots |
| `device_properties` | device-service | Discovered telemetry fields |
| `device_dashboard_widgets` | device-service | Dashboard widget config |
| `device_dashboard_widget_settings` | device-service | Widget settings state |
| `idle_running_log` | device-service | Daily idle/running logs |
| `waste_site_config` | device-service | Site waste config |
| `dashboard_snapshots` | device-service | Cached dashboard data |
| `rules` | rule-engine-service | Alert rules |
| `alerts` | rule-engine-service | Triggered alerts |
| `activity_events` | rule-engine-service | Activity audit trail |
| `energy_reports` | reporting-service | Report job tracking |
| `scheduled_reports` | reporting-service | Scheduled report config |
| `tenant_tariffs` | reporting-service | Tenant pricing |
| `tariff_config` | reporting-service | Global tariff |
| `notification_channels` | reporting-service | Notification targets |
| `analytics_jobs` | analytics-service | ML job tracking |
| `ml_model_artifacts` | analytics-service | Model storage |
| `analytics_worker_heartbeats` | analytics-service | Worker health |
| `failure_event_labels` | analytics-service | ML training labels |
| `analytics_accuracy_evaluations` | analytics-service | Model accuracy |
| `waste_analysis_jobs` | waste-analysis-service | Waste job tracking |
| `waste_device_summary` | waste-analysis-service | Waste results |
| `dlq_messages` | data-service | Failed telemetry |
| `export_checkpoints` | data-export-service | Export progress |

### 4.2 InfluxDB

**URL:** `http://influxdb:8086`
**Organization:** `energy-org`
**Bucket:** `telemetry`
**Token:** `energy-token`

#### Measurement: `device_telemetry`

**Tags (Indexed):**
| Tag | Type | Description |
|-----|------|-------------|
| device_id | string | Device identifier |
| device_type | string | Type of device |
| tenant_id | string | Tenant identifier |
| schema_version | string | Data schema version |
| enrichment_status | string | Enrichment success/failure |

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| current_a | float | Current in Amperes |
| voltage_v | float | Voltage in Volts |
| power_kw | float | Power in Kilowatts |
| power_factor | float | Power factor (0-1) |
| energy_kwh | float | Cumulative energy (kWh) |
| temperature_c | float | Temperature (Celsius) |
| frequency_hz | float | Frequency (Hertz) |
| reactive_power_kvar | float | Reactive power |
| thd | float | Total harmonic distortion |
| * | float | Any other numeric field |

**Retention Policies:**
- `autogen`: Infinite retention (default)
- `1h`: 1-hour downsampled
- `1d`: 1-day downsampled

### 4.3 MinIO/S3 Storage

**Endpoint:** `http://minio:9000`
**Access Key:** `minio`
**Secret Key:** `minio123`

#### Buckets

| Bucket | Purpose |
|--------|---------|
| `energy-platform-datasets` | Telemetry exports, ML datasets |
| `factoryops-waste-reports` | Waste analysis PDF reports |

#### Object Key Patterns

```
# Telemetry Exports
telemetry/
  └── {device_id}/
      └── {year}/{month}/{day}/
          └── {timestamp}.parquet

# Waste Reports
waste-reports/
  └── {job_id}/
      └── report.pdf

# ML Artifacts
ml-artifacts/
  └── {device_id}/
      └── {analysis_type}/
          └── {model_version}.pkl
```

---

## 5. Inter-Service Communication

### 5.1 HTTP REST API Calls

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Inter-Service HTTP Calls                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐                              ┌──────────────┐
│ data-service │◀─────────────────────────────│    UI/Web    │
│   (8081)     │    GET /api/v1/data/*        │   (3000)     │
└──────────────┘                              └──────────────┘
       │
       │ GET /api/v1/devices/{id}
       ▼
┌──────────────┐
│device-service│
│   (8000)     │
└──────────────┘
       │
       │ GET /api/v1/rules/evaluate
       ▼
┌──────────────┐
│rule-engine   │
│ (8002)       │
└──────────────┘

┌──────────────┐                              ┌──────────────┐
│analytics     │◀─────────────────────────────│    UI/Web    │
│ (8003)       │    GET /api/v1/analytics/*   │   (3000)     │
└──────────────┘                              └──────────────┘

┌──────────────┐                              ┌──────────────┐
│ reporting    │◀─────────────────────────────│    UI/Web    │
│  (8085)      │    GET /api/reports/*        │   (3000)     │
└──────────────┘                              └──────────────┘

┌──────────────┐                              ┌──────────────┐
│   waste      │◀─────────────────────────────│    UI/Web    │
│  (8087)      │    GET /api/v1/waste/*       │   (3000)     │
└──────────────┘                              └──────────────┘
```

### 5.2 Service Communication Matrix

| From Service | To Service | Endpoint | Purpose |
|--------------|------------|----------|---------|
| data-service | device-service | `/api/v1/devices/{id}` | Fetch device metadata |
| data-service | device-service | `/api/v1/devices/{id}/heartbeat` | Update device heartbeat |
| data-service | device-service | `/api/v1/devices/{id}/properties/sync` | Sync telemetry properties |
| data-service | rule-engine-service | `/api/v1/rules/evaluate` | Evaluate rules |
| data-service | device-service | WebSocket | Real-time telemetry broadcast |
| rule-engine-service | device-service | `/api/v1/devices/{id}` | Get device name for alerts |
| rule-engine-service | SMTP | Email | Send alert notifications |
| analytics-service | data-service | `/api/v1/data/telemetry` | Query telemetry |
| analytics-service | data-export-service | `/api/v1/exports/status/{id}` | Check export status |
| copilot-service | data-service | `/api/v1/data/*` | Query telemetry |
| copilot-service | reporting-service | `/api/reports/*` | Query reports |
| waste-analysis-service | reporting-service | `/api/reports/*` | Get energy data |
| waste-analysis-service | device-service | `/api/v1/devices/*` | Get device list |
| reporting-service | InfluxDB | Query API | Read telemetry |
| reporting-service | MinIO | S3 API | Store generated PDFs |

### 5.3 Message Queues

| Queue Name | Technology | Purpose | Consumer |
|------------|------------|---------|----------|
| `analytics_jobs_stream` | Redis Stream | ML job queue | analytics-worker |
| `analytics_jobs_dead_letter` | Redis Stream | Failed ML jobs | Manual review |
| MQTT: `devices/+/telemetry` | EMQX | Telemetry ingestion | data-service |

### 5.4 WebSocket Communication

| Service | WebSocket Endpoint | Purpose |
|---------|-------------------|---------|
| data-service | `ws://localhost:8081/ws/telemetry` | Real-time telemetry broadcast |
| device-service | `ws://localhost:8000/ws/dashboard` | Dashboard updates |

---

## 6. Database Table Relationships

### 6.1 Entity Relationship Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DEVICES (Master Entity)                            │
│                           device_id (PK)                                    │
└─────────────────────────────────────────────────────────────────────────────┘
      │
      ├──┬──► device_shifts (1:N)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► parameter_health_config (1:N)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► device_properties (1:N)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► device_performance_trends (1:N)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► idle_running_log (1:N)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► device_dashboard_widgets (1:N)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► device_dashboard_widget_settings (1:1)
      │       └── device_id (PK, FK) → devices.device_id
      │
      ├──┬──► alerts (N:1)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► activity_events (N:1)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► analytics_jobs (1:N)
      │       └── device_id (FK) → devices.device_id
      │
      ├──┬──► ml_model_artifacts (1:N)
      │       └── device_id (FK) → devices.device_id
      │
      └──┬──► failure_event_labels (1:N)
              └── device_id (FK) → devices.device_id


┌─────────────────────────────────────────────────────────────────────────────┐
│                              RULES                                           │
│                           rule_id (PK)                                       │
└─────────────────────────────────────────────────────────────────────────────┘
      │
      ├──┬──► alerts (1:N)
      │       └── rule_id (FK) → rules.rule_id
      │
      └──┬──► activity_events (1:N)
              └── rule_id (FK) → rules.rule_id


┌─────────────────────────────────────────────────────────────────────────────┐
│                           ANALYTICS_JOBS                                     │
│                             job_id (PK)                                     │
└─────────────────────────────────────────────────────────────────────────────┘
      │
      └──┬──► ml_model_artifacts (N:1)
              └── (device_id, analysis_type) lookup


┌─────────────────────────────────────────────────────────────────────────────┐
│                        WASTE_ANALYSIS_JOBS                                  │
│                              id (PK)                                         │
└─────────────────────────────────────────────────────────────────────────────┘
      │
      └──┬──► waste_device_summary (1:N)
              └── job_id (FK) → waste_analysis_jobs.id
```

### 6.2 Foreign Key References

| Child Table | Parent Table | FK Column | Relationship |
|-------------|--------------|------------|--------------|
| device_shifts | devices | device_id | 1:N |
| parameter_health_config | devices | device_id | 1:N |
| device_properties | devices | device_id | 1:N |
| device_performance_trends | devices | device_id | 1:N |
| idle_running_log | devices | device_id | 1:N |
| device_dashboard_widgets | devices | device_id | 1:N |
| device_dashboard_widget_settings | devices | device_id | 1:1 |
| alerts | rules | rule_id | N:1 |
| alerts | devices | device_id | N:1 |
| activity_events | rules | rule_id | N:1 |
| activity_events | alerts | alert_id | N:1 |
| activity_events | devices | device_id | N:1 |
| analytics_jobs | devices | device_id | N:1 |
| ml_model_artifacts | devices | device_id | N:1 |
| failure_event_labels | devices | device_id | N:1 |
| waste_device_summary | waste_analysis_jobs | job_id | 1:N |

---

## 7. API Reference

### 7.1 data-service API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/data/telemetry` | Ingest telemetry |
| GET | `/api/v1/data/telemetry/{device_id}` | Query device telemetry |
| GET | `/api/v1/data/health` | Health check |
| GET | `/api/v1/data/devices` | List devices with telemetry |
| WebSocket | `/ws/telemetry` | Real-time telemetry stream |

#### POST /api/v1/data/telemetry

**Request:**
```bash
curl -X POST "http://localhost:8081/api/v1/data/telemetry" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "COMPRESSOR-001",
    "timestamp": "2026-03-18T10:00:00Z",
    "power": 2500.0,
    "current": 12.5,
    "voltage": 230.5,
    "power_factor": 0.95,
    "energy_kwh": 1250.5
  }'
```

**Response (202 Accepted):**
```json
{
  "status": "accepted",
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-18T10:00:00Z",
  "enrichment_status": "success"
}
```

#### GET /api/v1/data/telemetry/{device_id}

**Request:**
```bash
curl "http://localhost:8081/api/v1/data/telemetry/COMPRESSOR-001?start=2026-03-18T00:00:00Z&end=2026-03-18T23:59:59Z&limit=100"
```

**Response (200 OK):**
```json
{
  "device_id": "COMPRESSOR-001",
  "data": [
    {
      "timestamp": "2026-03-18T10:00:00Z",
      "power_kw": 2.5,
      "current_a": 12.5,
      "voltage_v": 230.5,
      "power_factor": 0.95,
      "energy_kwh": 1250.5
    }
  ],
  "count": 1
}
```

### 7.2 device-service API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/devices` | List all devices |
| POST | `/api/v1/devices` | Create device |
| GET | `/api/v1/devices/{device_id}` | Get device details |
| PUT | `/api/v1/devices/{device_id}` | Update device |
| DELETE | `/api/v1/devices/{device_id}` | Delete device |
| GET | `/api/v1/devices/{device_id}/dashboard-widgets` | Get widget config |
| PUT | `/api/v1/devices/{device_id}/dashboard-widgets` | Update widget config |
| POST | `/api/v1/devices/{device_id}/heartbeat` | Device heartbeat |
| WebSocket | `/ws/dashboard` | Dashboard stream |

#### GET /api/v1/devices

**Response:**
```json
{
  "devices": [
    {
      "device_id": "COMPRESSOR-001",
      "device_name": "Compressor 001",
      "device_type": "compressor",
      "data_source_type": "metered",
      "phase_type": "three",
      "manufacturer": "Atlas Copco",
      "model": "GA37",
      "location": "Plant A",
      "legacy_status": "active",
      "last_seen_timestamp": "2026-03-18T10:00:00"
    }
  ],
  "total": 1
}
```

### 7.3 rule-engine-service API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/rules` | List rules |
| POST | `/api/v1/rules` | Create rule |
| GET | `/api/v1/rules/{rule_id}` | Get rule |
| PUT | `/api/v1/rules/{rule_id}` | Update rule |
| DELETE | `/api/v1/rules/{rule_id}` | Delete rule |
| POST | `/api/v1/rules/evaluate` | Evaluate telemetry |
| GET | `/api/v1/alerts` | List alerts |
| PUT | `/api/v1/alerts/{alert_id}/acknowledge` | Acknowledge alert |
| GET | `/api/v1/activity` | Activity feed |

#### POST /api/v1/rules/evaluate

**Request:**
```bash
curl -X POST "http://localhost:8002/api/v1/rules/evaluate" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "COMPRESSOR-001",
    "telemetry": {
      "current_a": 58.5,
      "power_kw": 25.0,
      "power_factor": 0.95
    }
  }'
```

**Response:**
```json
{
  "device_id": "COMPRESSOR-001",
  "evaluated_at": "2026-03-18T10:00:00Z",
  "triggered_rules": [
    {
      "rule_id": "uuid-001",
      "rule_name": "High Current Alert",
      "message": "Current exceeded threshold: 58.5 > 55.0",
      "severity": "critical"
    }
  ]
}
```

### 7.4 analytics-service API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/analytics/jobs` | List ML jobs |
| POST | `/api/v1/analytics/jobs` | Submit ML job |
| GET | `/api/v1/analytics/jobs/{job_id}` | Get job status |
| GET | `/api/v1/analytics/results/{job_id}` | Get job results |
| GET | `/api/v1/analytics/models` | List trained models |
| GET | `/api/v1/analytics/accuracy` | Model accuracy metrics |

#### POST /api/v1/analytics/jobs

**Request:**
```bash
curl -X POST "http://localhost:8003/api/v1/analytics/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "COMPRESSOR-001",
    "analysis_type": "failure_prediction",
    "model_name": "lstm_classifier",
    "date_range_start": "2026-01-01T00:00:00Z",
    "date_range_end": "2026-03-18T00:00:00Z"
  }'
```

**Response:**
```json
{
  "job_id": "uuid-001",
  "status": "queued",
  "message": "Job queued for processing"
}
```

### 7.5 reporting-service API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/reports/consumption` | Generate consumption report |
| POST | `/api/reports/comparison` | Generate comparison report |
| GET | `/api/reports/{report_id}` | Get report status |
| GET | `/api/reports/{report_id}/download` | Download report |
| GET | `/api/reports/scheduled` | List scheduled reports |
| POST | `/api/reports/scheduled` | Create scheduled report |
| GET | `/api/tariffs` | Get tariffs |
| PUT | `/api/tariffs` | Update tariffs |

#### POST /api/reports/consumption

**Request:**
```bash
curl -X POST "http://localhost:8085/api/reports/consumption" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-001",
    "device_ids": ["COMPRESSOR-001", "COMPRESSOR-002"],
    "start_date": "2026-03-01",
    "end_date": "2026-03-31",
    "granularity": "daily"
  }'
```

**Response:**
```json
{
  "report_id": "uuid-001",
  "status": "processing",
  "progress": 0
}
```

### 7.6 waste-analysis-service API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/waste/jobs` | Submit waste analysis job |
| GET | `/api/v1/waste/jobs/{job_id}` | Get job status |
| GET | `/api/v1/waste/jobs/{job_id}/results` | Get results |
| GET | `/api/v1/waste/summary/{job_id}` | Get device summary |

#### POST /api/v1/waste/jobs

**Request:**
```bash
curl -X POST "http://localhost:8087/api/v1/waste/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "all",
    "start_date": "2026-03-01",
    "end_date": "2026-03-31",
    "granularity": "daily"
  }'
```

**Response:**
```json
{
  "job_id": "uuid-001",
  "status": "pending",
  "progress_pct": 0
}
```

### 7.7 data-export-service API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/exports` | List exports |
| POST | `/api/v1/exports` | Trigger export |
| GET | `/api/v1/exports/{export_id}` | Get export status |

### 7.8 copilot-service API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/chat` | Send chat message |
| GET | `/chat/history` | Get chat history |

---

## 8. Error Handling & DLQ Flows

### 8.1 Telemetry Processing Errors

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Error Handling Flow                                    │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │   Telemetry  │
    │   Received   │
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  Validation  │───────────▶ Invalid Payload
    │   Service    │           (HTTP 400)
    └──────┬───────┘
           │
           │ Valid
           ▼
    ┌──────────────┐
    │  Enrichment  │───────────▶ Device Not Found
    │   Service    │           (HTTP 404)
    └──────┬───────┘
           │
           │ Enriched
           ▼
    ┌──────────────┐
    │  InfluxDB    │───────────▶ Write Failed
    │  Write       │           (DLQ)
    └──────┬───────┘
           │
           │ Success
           ▼
    ┌──────────────┐
    │   Rule       │───────────▶ Rule Engine Error
    │   Engine     │           (Retry with backoff)
    │   Call       │
    └──────────────┘
```

### 8.2 Dead Letter Queue (DLQ)

The data-service uses MySQL as the DLQ backend to store failed telemetry messages.

**DLQ Table: `dlq_messages`**

| Column | Type | Description |
|--------|------|-------------|
| id | BIGINT | Auto-increment ID |
| timestamp | DATETIME(6) | Original telemetry timestamp |
| error_type | VARCHAR(128) | Error classification |
| error_message | TEXT | Error details |
| retry_count | INT | Number of retry attempts |
| original_payload | JSON | Original message payload |
| status | VARCHAR(32) | pending, reprocessing, failed |
| created_at | DATETIME(6) | Queue entry creation time |

**Error Types:**
- `validation_error`: Invalid payload format or missing fields
- `device_not_found`: Device not registered in system
- `influxdb_write_error`: Failed to write to InfluxDB
- `enrichment_error`: Failed to fetch device metadata

**Retry Logic:**
1. Initial failure → status: `pending`, retry_count: 0
2. Retry up to 3 times with exponential backoff
3. After 3 failures → status: `failed`

### 8.3 Analytics Job Error Handling

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   Analytics Job Error Handling                             │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │   Job        │
    │   Submitted  │
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  Job Worker  │───────────▶ Processing Error
    │  Processing  │           (Retry up to 3 times)
    └──────┬───────┘
           │
           │ Success
           ▼
    ┌──────────────┐
    │  Model       │───────────▶ Model Error
    │  Training    │           (Store error, mark job failed)
    └──────┬───────┘
           │
           │ Success
           ▼
    ┌──────────────┐
    │  Store       │───────────▶ Storage Error
    │  Results     │           (DLQ to dead letter stream)
    └──────┬───────┘
           │
           │ Complete
           ▼
    ┌──────────────┐
    │    Done      │
    └──────────────┘
```

### 8.4 Failed Job Dead Letter

Failed analytics jobs are moved to a Redis dead letter stream.

| Stream | Purpose |
|--------|---------|
| `analytics_jobs_stream` | Active job queue |
| `analytics_jobs_dead_letter` | Failed jobs for manual review |

---

## 9. Complete Data Flow Diagrams

### 9.1 End-to-End Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    FACTORYOPS COMPLETE DATA FLOW                            │
└─────────────────────────────────────────────────────────────────────────────┘

                           ┌─────────────────────────────────────────────────────┐
                           │                   IoT DEVICES                      │
                           │    (Sensors, Meters, PLCs, Industrial Equipment)   │
                           └──────────────────────┬──────────────────────────────┘
                                                    │
                                                    │ MQTT (Port 1883)
                                                    │ Topic: devices/{device_id}/telemetry
                                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EMQX MQTT BROKER                                │
│                         (Port 1883, Dashboard: 18083)                       │
│                                                                             │
│    ┌────────────────────────────────────────────────────────────────────┐   │
│    │                    MQTT Message Flow                              │   │
│    │  devices/COMPRESSOR-001/telemetry ──► devices/COMPRESSOR-002/...  │   │
│    └────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────┬────────────────────────────────────┘
                                         │
                                         │ Subscribe: devices/+/telemetry
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA-SERVICE (Port 8081)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ MQTT Handler│  │  Validation │  │ Enrichment  │  │    InfluxDB Writer  │ │
│  │             │─▶│   Service   │─▶│   Service   │─▶│                     │ │
│  └─────────────┘  └─────────────┘  └──────┬──────┘  └──────────┬──────────┘ │
│                                           │                     │             │
│                                           │                     │             │
│                    ┌──────────────────────┴─────────────────────┘             │
│                    │                                                           │
│                    ▼                        ▼                                  │
│           ┌─────────────────┐          ┌─────────────────┐                   │
│           │  Rule Engine    │          │   WebSocket     │                   │
│           │    Client       │          │   Broadcast     │                   │
│           └────────┬────────┘          └─────────────────┘                   │
│                    │                                                           │
└────────────────────┼───────────────────────────────────────────────────────────┘
                     │
                     │ HTTP POST /api/v1/rules/evaluate
                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        RULE-ENGINE-SERVICE (Port 8002)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Load Rules  │─▶│  Evaluate   │─▶│   Trigger   │─▶│    Store Alert     │ │
│  │  from MySQL │  │   Rules     │  │   Alert     │  │    in MySQL        │ │
│  └─────────────┘  └─────────────┘  └──────┬──────┘  └─────────────────────┘ │
│                                           │                                    │
│                                           ▼                                    │
│                                   ┌─────────────────┐                         │
│                                   │   Notification  │                         │
│                                   │   (Email/Slack) │                         │
│                                   └─────────────────┘                         │
└───────────────────────────────────────────────────────────────────────────────┘


                                    │
                                    │ (Async) Device Sync
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DEVICE-SERVICE (Port 8000)                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  Heartbeat  │  │  Property   │  │  Dashboard  │  │    Dashboard        │ │
│  │  Update     │  │   Sync      │  │   Widgets   │  │    Snapshots        │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│                                                                             │
│                          ┌─────────────────────────────────────────┐         │
│                          │            MySQL Database               │         │
│                          │         (ai_factoryops)                 │         │
│                          │  ┌──────────────────────────────────┐   │         │
│                          │  │ devices, device_shifts,          │   │         │
│                          │  │ parameter_health_config,         │   │         │
│                          │  │ device_properties, alerts,      │   │         │
│                          │  │ rules, activity_events, etc.    │   │         │
│                          │  └──────────────────────────────────┘   │         │
│                          └─────────────────────────────────────────┘         │
└───────────────────────────────────────────────────────────────────────────────┘


         │                                    │                                    │
         │                                    │ (Query)                             │
         ▼                                    ▼                                    │
┌─────────────────────────────┐   ┌─────────────────────────────────────────────┐
│    REAL-TIME DASHBOARD      │   │              OTHER SERVICES                  │
│         Port 3000           │   │                                              │
│                             │   │  ┌─────────────────────────────────────────┐ │
│  ┌───────────────────────┐  │   │  │        ANALYTICS-SERVICE (8003)        │ │
│  │   WebSocket           │  │   │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐ │ │
│  │   Subscription        │  │   │  │  │Job Queue│─▶│ Worker  │─▶│ ML      │ │ │
│  └───────────┬───────────┘  │   │  │  │(Redis)  │  │(async)  │  │ Models  │ │ │
│              │              │   │  │  └─────────┘  └─────────┘  └────┬────┘ │ │
│              ▼              │   │  │                                       │    │ │
│  ┌───────────────────────┐  │   │  └───────────────────────────────────────┼────┘ │
│  │   Dashboard           │  │   │                                          │      │
│  │   Components          │  │   │  ┌───────────────────────────────────────┼────┐ │
│  └───────────────────────┘  │   │  │        REPORTING-SERVICE (8085)       │    │ │
│                             │   │  │  ┌─────────┐  ┌─────────┐  ┌────────┐│    │ │
│                             │   │  │  │  Report │─▶│ Energy  │─▶│  Cost  ││    │ │
│                             │   │  │  │ Request │  │ Engine  │  │ Engine ││    │ │
│                             │   │  │  └────┬────┘  └────┬────┘  └───┬────┘│    │ │
│                             │   │  │       │            │           │     │    │ │
│                             │   │  │       ▼            ▼           ▼     │    │ │
│                             │   │  │  ┌─────────┐  ┌─────────┐  ┌───────┐ │    │ │
│                             │   │  │  │ InfluxDB│  │  Tariff │  │  PDF  │ │    │ │
│                             │   │  │  │ Reader  │  │  Lookup │  │Build r│ │    │ │
│                             │   │  │  └────┬────┘  └─────────┘  └───┬────┘ │    │ │
│                             │   │  │       │                      │      │    │ │
│                             │   │  │       └──────────┬─────────────┘      │    │ │
│                             │   │  │                  ▼                   │    │ │
│                             │   │  │          ┌─────────────┐             │    │ │
│                             │   │  │          │   MinIO/S3  │             │    │ │
│                             │   │  │          └─────────────┘             │    │ │
│                             │   │  └──────────────────────────────────────────┘ │
│                             │   │                                             │
│                             │   │  ┌─────────────────────────────────────────┐ │
│                             │   │  │     WASTE-ANALYSIS-SERVICE (8087)       │ │
│                             │   │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  │ │
│                             │   │  │  │  Waste  │─▶│ Wastage │─▶│ Summary │  │ │
│                             │   │  │  │  Job    │  │Calculator│ │ Results │  │ │
│                             │   │  │  └─────────┘  └─────────┘  └────┬────┘  │ │
│                             │   │  │                                    │       │ │
│                             │   │  └────────────────────────────────────┼───────┘ │
│                             │   │                                           │      │
│                             │   │  ┌────────────────────────────────────┼───────┐│
│                             │   │  │     DATA-EXPORT-SERVICE (8080)      │       │
│                             │   │  │  ┌─────────┐  ┌─────────┐  ┌────────┐│       │
│                             │   │  │  │ Timer   │─▶│  Query  │─▶│ Write  ││       │
│                             │   │  │  │(every60s)│  │InfluxDB│  │  S3   ││       │
│                             │   │  │  └────┬────┘  └────┬────┘  └───┬────┘│       │
│                             │   │  │       │            │           │     │       │
│                             │   │  │       └───────────┴───────────┘     │       │
│                             │   │  │                   │                 │       │
│                             │   │  └───────────────────┼─────────────────┘       │
│                             │   │                       ▼                         │
│                             │   │  ┌─────────────────────────────────────────┐   │
│                             │   │  │     COPILOT-SERVICE (8007)             │   │
│                             │   │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐ │   │
│                             │   │  │  │  Chat   │─▶│  LLM    │─▶│ Query   │ │   │
│                             │   │  │  │ Request │  │ (AI)   │  │ Service │ │   │
│                             │   │  │  └─────────┘  └─────────┘  └─────────┘ │   │
│                             │   │  └─────────────────────────────────────────┘   │
│                             │   │                                             │
└─────────────────────────────┴───┴─────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                          INFLUXDB (Port 8086)                               │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Bucket: telemetry                                                    │  │
│  │  Measurement: device_telemetry                                       │  │
│  │                                                                       │  │
│  │  Tags: device_id, device_type, tenant_id, schema_version             │  │
│  │  Fields: voltage, current, power, power_factor, energy_kwh, etc.     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                          MINIO (Port 9000)                                  │
│                                                                             │
│  ┌─────────────────────────┐    ┌───────────────────────────────────────┐ │
│  │ energy-platform-datasets│    │     factoryops-waste-reports         │ │
│  │  /telemetry/{device}/   │    │     /waste-reports/{job_id}/         │ │
│  │  /ml-artifacts/          │    │                                       │ │
│  └─────────────────────────┘    └───────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 User-Initiated Report Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    User-Initiated Report Flow                               │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
    │  User   │────▶│   UI     │────▶│Reporting │────▶│ Report  │
    │ Request │     │         │     │ Service │     │  Task   │
    └─────────┘     └─────────┘     └────┬────┘     └────┬────┘
                                          │               │
                                          │               ▼
                                          │      ┌─────────────┐
                                          │      │   InfluxDB   │
                                          │      │   Reader     │
                                          │      └──────┬──────┘
                                          │              │
                                          │              ▼
                                          │      ┌─────────────┐
                                          │      │   Engines    │
                                          │      │ (Energy,     │
                                          │      │  Cost, etc.) │
                                          │      └──────┬──────┘
                                          │              │
                                          │              ▼
                                          │      ┌─────────────┐
                                          │      │  PDF Builder │
                                          │      └──────┬──────┘
                                          │              │
                                          │              ▼
                                          │      ┌─────────────┐
                                          │      │   MinIO/S3   │
                                          │      └──────┬──────┘
                                          │              │
                                          │              ▼
                                          │      ┌─────────────┐
                                          │      │   MySQL      │
                                          │      │   (status    │
                                          │      │   update)    │
                                          │              │
                                          │              ▼
                                          │      ┌─────────────┐
                                          └─────▶│   UI         │
                                                 │   (download) │
                                                 └─────────────┘
```

### 9.3 Scheduled Report Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Scheduled Report Flow                                    │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
    │   Report    │     │   Report    │     │  InfluxDB   │
    │  Scheduler  │────▶│   Task      │────▶│   Reader    │
    │  (Timer)    │     │             │     │             │
    └─────────────┘     └──────┬──────┘     └──────┬──────┘
                                │                   │
                                ▼                   ▼
                         ┌─────────────┐     ┌─────────────┐
                         │   Energy    │     │   Cost      │
                         │   Engine    │     │   Engine    │
                         └──────┬──────┘     └──────┬──────┘
                                │                   │
                                └─────────┬─────────┘
                                          │
                                          ▼
                                  ┌─────────────┐
                                  │  PDF Builder│
                                  └──────┬──────┘
                                         │
                                         ▼
                                  ┌─────────────┐
                                  │  MinIO/S3   │
                                  └──────┬──────┘
                                         │
                                         ▼
                                  ┌─────────────┐
                                  │  Email      │
                                  │ Notification│
                                  └─────────────┘
```

---

## Appendix A: Environment Variables

### data-service

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 8081 | Service port |
| MQTT_BROKER_HOST | emqx | MQTT broker hostname |
| MQTT_BROKER_PORT | 1883 | MQTT broker port |
| INFLUXDB_URL | http://influxdb:8086 | InfluxDB URL |
| INFLUXDB_TOKEN | energy-token | InfluxDB token |
| INFLUXDB_BUCKET | telemetry | InfluxDB bucket |
| DEVICE_SERVICE_URL | http://device-service:8000 | Device service URL |
| RULE_ENGINE_URL | http://rule-engine-service:8002 | Rule engine URL |

### device-service

| Variable | Default | Description |
|----------|---------|-------------|
| DATABASE_URL | mysql+aiomysql://... | MySQL connection string |
| DASHBOARD_STREAM_HEARTBEAT_SECONDS | 5 | WebSocket heartbeat |

### reporting-service

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 8085 | Service port |
| DATABASE_URL | mysql+aiomysql://... | MySQL connection string |
| MINIO_BUCKET | energy-platform-datasets | MinIO bucket |

---

## Appendix B: Health Check Endpoints

| Service | Endpoint | Expected Response |
|---------|----------|------------------|
| device-service | http://localhost:8000/health | `{"status":"healthy"}` |
| data-service | http://localhost:8081/api/v1/data/health | `{"status":"healthy"}` |
| rule-engine-service | http://localhost:8002/health | `{"status":"healthy"}` |
| analytics-service | http://localhost:8003/health | `{"status":"healthy"}` |
| data-export-service | http://localhost:8080/health | `{"status":"healthy"}` |
| reporting-service | http://localhost:8085/health | `{"status":"healthy"}` |
| waste-analysis-service | http://localhost:8087/health | `{"status":"healthy"}` |
| copilot-service | http://localhost:8007/health | `{"status":"healthy"}` |

---

## Appendix C: Troubleshooting

### View MQTT Messages
```bash
# Using mosquitto_sub
mosquitto_sub -h localhost -p 1883 -t "devices/+/telemetry" -v

# Using EMQX Dashboard
# Open http://localhost:18083 → Analytics → Messages
```

### View InfluxDB Data
```bash
# Using influx CLI
influx -org energy-org -token energy-token
> use telemetry
> select * from device_telemetry limit 10
```

### View MinIO Objects
```bash
# Using mc CLI
mc alias set local http://minio:9000 minio minio123
mc ls local/energy-platform-datasets/
```

### Check Service Logs
```bash
docker compose logs -f data-service
docker compose logs -f rule-engine-service
docker compose logs -f analytics-worker
```

---

*Document Version: 1.0*
*Last Updated: March 2026*
