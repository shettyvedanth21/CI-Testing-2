# Copilot Service

> AI-Powered Conversational Assistant for FactoryOPS

## Table of Contents

1. [Introduction](#introduction)
2. [Architecture Overview](#architecture-overview)
3. [Complete Data Flow](#complete-data-flow)
4. [Supported Use Cases](#supported-use-cases)
5. [API Reference](#api-reference)
6. [Configuration](#configuration)
7. [Deployment](#deployment)
8. [Security](#security)
9. [Troubleshooting](#troubleshooting)
10. [Related Documentation](#related-documentation)

---

## Introduction

The **Copilot Service** is an intelligent conversational assistant that enables factory managers and operations teams to query FactoryOPS data using natural language. Instead of writing complex SQL queries, users can ask questions in plain English and receive formatted responses with data tables, visualizations, and actionable insights.

### Core Capabilities

- **Natural Language Queries**: Ask questions in plain English
- **Automated SQL Generation**: Converts questions to optimized SQL queries
- **Smart Intent Classification**: Automatically identifies user intent
- **Multi-Provider AI Support**: Works with Groq, Gemini, or OpenAI
- **Visual Data Presentation**: Generates charts and data tables
- **Context-Aware Responses**: Remembers conversation history
- **Follow-up Suggestions**: Proposes related questions users might ask

### Target Users

- Factory Managers
- Operations Teams
- Maintenance Engineers
- Energy Analysts
- Anyone needing quick insights into factory performance

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           CLIENT APPLICATIONS                            │
│   (Web UI, Mobile App, Third-party Systems)                             │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │ HTTP POST
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     COPILOT SERVICE (Port 8007)                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    API LAYER (FastAPI)                          │   │
│  │   • POST /api/v1/copilot/chat                                  │   │
│  │   • GET  /health                                               │   │
│  │   • GET  /ready                                                │   │
│  └────────────────────────────┬────────────────────────────────────┘   │
│                               │                                         │
│  ┌────────────────────────────▼────────────────────────────────────┐   │
│  │                    CORE PROCESSING LAYER                        │   │
│  │  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐  │   │
│  │  │ Intent Router   │  │  Copilot Engine  │  │ Model Client  │  │   │
│  │  │                 │  │                  │  │               │  │   │
│  │  │ • classify_intent│  │ • process_question│  │ • Groq       │  │   │
│  │  │ • pattern matching│ │ • run quick path │  │ • Gemini     │  │   │
│  │  │ • follow-up check │ │ • run AI-SQL path│  │ • OpenAI     │  │   │
│  │  └─────────────────┘  └──────────────────┘  └────────────────┘  │   │
│  │  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐  │   │
│  │  │ Prompt Templates│  │Reasoning Composer│  │  Quick        │  │   │
│  │  │                 │  │                  │  │  Templates    │  │   │
│  │  │ • SQL System    │  │ • for_query_result│  │              │  │   │
│  │  │ • Formatter     │  │ • for_blocked    │  │ • factory_    │  │   │
│  │  │   System        │  │ • for_unsupported│  │   summary     │  │   │
│  │  └─────────────────┘  └──────────────────┘  │ • alerts_     │  │   │
│  │                                             │   recent      │  │   │
│  │                                             │ • top_energy  │  │   │
│  │                                             │ • idle_waste  │  │   │
│  │                                             │ • health_     │  │   │
│  │                                             │   scores      │  │   │
│  │                                             └────────────────┘  │   │
│  └────────────────────────────┬────────────────────────────────────┘   │
│                               │                                         │
│  ┌────────────────────────────▼────────────────────────────────────┐   │
│  │                       DATA LAYER                                 │   │
│  │  ┌─────────────────────┐  ┌────────────────────────────────┐   │   │
│  │  │   Query Engine      │  │      Schema Loader             │   │   │
│  │  │                     │  │                                │   │   │
│  │  │ • validate_sql()    │  │ • load_schema()                │   │   │
│  │  │ • execute_query()   │  │ • get_schema_context()         │   │   │
│  │  │ • blocked keywords │  │ • get_schema_manifest()         │   │   │
│  │  └─────────────────────┘  └────────────────────────────────┘   │   │
│  └────────────────────────────┬────────────────────────────────────┘   │
│                               │                                         │
│  ┌────────────────────────────▼────────────────────────────────────┐   │
│  │                    INTEGRATIONS LAYER                            │   │
│  │  ┌──────────────────────────┐  ┌─────────────────────────────┐  │   │
│  │  │  Data Service Client     │  │   Service Clients          │  │   │
│  │  │                          │  │                             │  │   │
│  │  │ • fetch_telemetry()      │  │ • get_current_tariff()    │  │   │
│  │  │   (device power/energy)  │  │   (from reporting-service)│  │   │
│  │  └──────────────────────────┘  └─────────────────────────────┘  │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   MySQL DB      │    │  Data Service   │    │ Reporting       │
│  ai_factoryops  │    │  (Telemetry)    │    │ Service         │
│                 │    │                 │    │ (Tariff)        │
│ • devices       │    │ • /api/v1/data/ │    │                 │
│ • alerts        │    │   telemetry/    │    │ • /api/v1/      │
│ • rules         │    │   {device_id}   │    │   settings/     │
│ • idle_running_ │    │                 │    │   tariff        │
│   log           │    │                 │    │                 │
│ • tariff_config│    │                 │    │                 │
│ • activity_    │    │                 │    │                 │
│   events        │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Web Framework | FastAPI | 0.109.0 |
| ASGI Server | Uvicorn | 0.27.0 |
| Database | MySQL (aiomysql) | 0.2.0 |
| ORM | SQLAlchemy (async) | 2.0.25 |
| HTTP Client | httpx | 0.26.0 |
| AI Provider (Primary) | Groq (Llama 3.1) | - |
| AI Providers (Optional) | Gemini, OpenAI | - |
| Validation | Pydantic | 2.5.3 |
| Configuration | Pydantic Settings | 2.1.0 |

### Component Descriptions

#### API Layer (`src/api/chat.py`)
- Exposes the `/api/v1/copilot/chat` endpoint
- Handles request validation
- Initializes AI model and copilot engine
- Returns formatted `CopilotResponse`

#### Intent Router (`src/intent/router.py`)
- Classifies user messages into intents
- Supports predefined quick intents and AI-SQL fallback
- Pattern-based classification with keyword matching
- Determines whether to use quick templates or AI generation

#### Copilot Engine (`src/ai/copilot_engine.py`)
- Main orchestrator for all query processing
- Routes to appropriate processing path (quick template or AI-SQL)
- Builds charts from query results
- Validates and formats follow-up suggestions
- Handles all edge cases and error responses

#### Model Client (`src/ai/model_client.py`)
- Unified interface for multiple AI providers
- Supports Groq, Gemini, and OpenAI
- Async generation of responses
- Health check (`ping()`) capability

#### Query Engine (`src/db/query_engine.py`)
- Validates SQL queries for safety
- Executes read-only SELECT queries
- Enforces query timeouts
- Blocks dangerous SQL keywords
- Returns results with column metadata

#### Schema Loader (`src/db/schema_loader.py`)
- Loads database schema at startup
- Creates context string for AI prompt
- Generates schema manifest for template building
- Caches schema for performance

#### Reasoning Composer (`src/ai/reasoning_composer.py`)
- Generates human-readable explanations
- Creates reasoning sections (what happened, why it matters, how calculated)
- Provides fallback responses for blocked/unsupported queries

#### Integrations

**Data Service Client** (`src/integrations/data_service_client.py`)
- Fetches real-time telemetry data from data-service
- Retrieves power and energy measurements for devices
- Used for trend analysis and energy calculations

**Service Clients** (`src/integrations/service_clients.py`)
- Fetches current tariff rates from reporting-service
- Enables cost calculations for energy consumption

---

## Complete Data Flow

### Overview

When a user sends a message to the Copilot Service, it goes through a multi-stage pipeline:

```
USER MESSAGE
     │
     ▼
┌─────────────┐
│  Validate   │ ──▶ Check message not empty
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Intent    │ ──▶ Classify user intent
│  Classify   │     • Quick Intent (predefined)
       │      │     • AI-SQL (natural language)
       │      │
       ▼      │
┌─────────────┐    ┌─────────────────────┐
│   Quick     │    │    AI-SQL Path      │
│  Template   │    │                     │
│    Path     │    │ 1. Build prompt      │
│             │    │    (schema + history │
       │      │    │    + question)       │
       │      │    │                     │
       ▼      │    ▼                     │
┌─────────────┐  ┌─────────────┐          │
│   Execute   │  │  Stage 1:   │          │
│   Prebuilt  │  │  Generate   │          │
│     SQL     │  │  SQL Query  │          │
└──────┬──────┘  └──────┬──────┘          │
       │                │                  │
       ▼                ▼                  │
┌─────────────┐  ┌─────────────┐          │
│   Build     │  │  Validate   │          │
│   Response  │  │  & Execute  │          │
│             │  │    SQL      │          │
└──────┬──────┘  └──────┬──────┘          │
       │                │                  │
       ▼                ▼                  │
┌─────────────┐  ┌─────────────┐          │
│   Format    │  │  Stage 2:   │          │
│   Results   │  │  Format     │          │
│   (if needed)│  │  Response   │          │
└──────┬──────┘  └──────┬──────┘          │
       │                │                  │
       └────────┬───────┘                  │
                │                          │
                ▼                          │
┌──────────────────────────────────────────┐
│         RETURN COPILOT RESPONSE          │
│  • answer (natural language)              │
│  • reasoning (explanation)               │
│  • data_table (optional)                 │
│  • chart (optional visualization)        │
│  • page_links (navigation)               │
│  • follow_up_suggestions                  │
└──────────────────────────────────────────┘
```

### Step-by-Step Flow

#### Step 1: Request Reception
```
Client ──POST /api/v1/copilot/chat──▶ FastAPI App
```

The API receives:
- `message`: User's question (required)
- `conversation_history`: Previous chat turns (optional)

#### Step 2: Engine Initialization
On first request (or if not initialized):
1. Initialize `ModelClient` with configured AI provider
2. Create `CopilotEngine` instance
3. Check provider is configured

#### Step 3: Intent Classification
The `classify_intent()` function analyzes the message:

| Intent | Patterns Detected |
|--------|-------------------|
| `factory_summary` | "summarize", "factory performance", "overview today", "top problems" |
| `top_energy_today` | "most power today", "most energy today", "consumed the most power" |
| `alerts_recent` | "recent alerts", "alerts today", "rules triggered", "anomalies today" |
| `idle_waste` | "idle cost", "idle running", "standby loss", "waste energy" |
| `health_scores` | "health score", "lowest efficiency", "below 80% efficiency" |
| `telemetry_trend` | "trend", "last 30 days", "spike", "at 3pm", "over time" |
| `unsupported` | "oee", "overall equipment effectiveness", "yield", "production count" |
| `ai_sql_with_context` | (fallback with conversation history) |
| `ai_sql` | (fallback for natural language queries) |

#### Step 4a: Quick Intent Processing (Fast Path)

For recognized intents with pre-built templates:

1. **Template Selection**: Choose the predefined SQL template
2. **Query Execution**: Run the SQL via QueryEngine
3. **Data Processing**: Process results into tabular format
4. **Chart Building**: Generate chart from rows (if applicable)
5. **Response Formatting**: Use ReasoningComposer for explanations

**Example - Factory Summary:**
```
User: "Summarize today's factory performance"

Intent: factory_summary → Quick Template

SQL Executed:
SELECT d.device_id, d.device_name, d.legacy_status, d.last_seen_timestamp,
       COALESCE(i.idle_duration_sec/3600, 0) AS idle_hours_today,
       COALESCE(i.idle_energy_kwh, 0) AS idle_kwh_today
FROM devices d
LEFT JOIN idle_running_log i ON d.device_id = i.device_id
AND DATE(i.period_start)=CURDATE()
ORDER BY idle_kwh_today DESC LIMIT 50
```

#### Step 4b: AI-SQL Processing (Full Path)

For complex or free-form queries:

**Stage 1: SQL Generation**
1. Build prompt with schema context
2. Add conversation history for context
3. Send to AI with SQL_SYSTEM_PROMPT
4. Receive generated SQL query

```python
user_prompt = f"""
SCHEMA:
{schema_context}

CONVERSATION:
{history_text}

USER QUESTION: {message}

Write the query now.
"""
```

**Stage 2: Query Execution**
1. Validate SQL (SELECT only, blocked keywords)
2. Execute query with timeout
3. Handle errors appropriately

**Stage 3: Response Formatting**
1. Build payload with results
2. Send to AI with FORMATTER_SYSTEM_PROMPT
3. Parse JSON response
4. Validate and sanitize chart data
5. Generate follow-up suggestions

#### Step 5: Response Construction

Build the final `CopilotResponse`:

```python
CopilotResponse(
    answer="...",              # Natural language answer
    reasoning="...",           # Human-readable explanation
    reasoning_sections={       # Structured reasoning
        "what_happened": "...",
        "why_it_matters": "...",
        "how_calculated": "..."
    },
    data_table={              # Tabular data (optional)
        "headers": [...],
        "rows": [[...], ...]
    },
    chart={                   # Visualization (optional)
        "type": "bar|line",
        "title": "...",
        "labels": [...],
        "datasets": [...]
    },
    page_links=[              # Navigation links (optional)
        {"label": "...", "route": "..."}
    ],
    follow_up_suggestions=[    # Suggested next questions
        "...", "...", "..."
    ]
)
```

---

## Supported Use Cases

The Copilot Service supports multiple use cases, each designed to answer specific factory operations questions.

### 1. Factory Summary

**Description**: Provides a daily overview of factory performance, showing machines with their idle energy consumption for today.

**Intent Patterns**:
- "summarize today's factory performance"
- "factory overview"
- "top problems"
- "overview today"

**Processing Path**: Quick Template

**Database Tables Used**:
- `devices` - Machine information
- `idle_running_log` - Idle time and energy data

**SQL Query**:
```sql
SELECT d.device_id, d.device_name, d.legacy_status, d.last_seen_timestamp,
       COALESCE(i.idle_duration_sec/3600, 0) AS idle_hours_today,
       COALESCE(i.idle_energy_kwh, 0) AS idle_kwh_today
FROM devices d
LEFT JOIN idle_running_log i ON d.device_id = i.device_id
AND DATE(i.period_start)=CURDATE()
ORDER BY idle_kwh_today DESC LIMIT 50
```

**Response Example**:
```json
{
  "answer": "Today, CNC_MACHINE_001 has the highest idle energy at 12.5 kWh.",
  "reasoning": "What happened: Today, CNC_MACHINE_001 has the highest idle energy at 12.5 kWh.\nWhy it matters: 8/10 machines are active. Reducing idle energy on top contributors can cut avoidable cost.\nHow calculated: Used today's device status and idle energy records, then ranked machines by idle kWh.",
  "reasoning_sections": {
    "what_happened": "Today, CNC_MACHINE_001 has the highest idle energy at 12.5 kWh.",
    "why_it_matters": "8/10 machines are active. Reducing idle energy on top contributors can cut avoidable cost.",
    "how_calculated": "Used today's device status and idle energy records, then ranked machines by idle kWh."
  },
  "data_table": {
    "headers": ["device_id", "device_name", "legacy_status", "idle_hours_today", "idle_kwh_today"],
    "rows": [["CNC_MACHINE_001", "CNC Machine 1", "active", 2.5, 12.5], ...]
  },
  "chart": {
    "type": "bar",
    "title": "Idle Energy Today by Machine",
    "labels": ["CNC_MACHINE_001", "PRESS_001", "WELD_ROBOT_01"],
    "datasets": [{"label": "idle_kwh_today", "data": [12.5, 8.3, 5.7]}]
  },
  "follow_up_suggestions": [
    "Which machine has the highest idle cost today?",
    "Show recent alerts for the top idle machine"
  ]
}
```

**User Scenarios**:
- Morning check: "Summarize today's factory performance"
- End of day: "Give me an overview of today's operations"
- Problem investigation: "What's happening in the factory today?"

---

### 2. Top Energy Consumers Today

**Description**: Identifies which machines consumed the most energy today, with cost calculations based on current tariff rates.

**Intent Patterns**:
- "most power today"
- "most energy today"
- "consumed the most power"
- "top energy consumers"

**Processing Path**: Telemetry + SQL (Hybrid)

**Data Sources**:
- `data-service`: Real-time telemetry (power, energy_kwh)
- `reporting-service`: Current tariff rate
- `devices`: Machine information

**Calculation Method**:
1. Fetch devices list from database
2. For each device, fetch today's telemetry from data-service
3. Calculate energy using either:
   - Energy delta: `max(energy_kwh) - min(energy_kwh)`
   - Power integration fallback: Sum of `power * time_duration`
4. Apply tariff rate for cost calculation

**Response Example**:
```json
{
  "answer": "CNC_MACHINE_001 consumed the most energy today at 245.3 kWh (₹ 2,453.00).",
  "reasoning": "What happened: Today, CNC_MACHINE_001 consumed the most energy at 245.3 kWh.\nWhy it matters: This highlights the best machine to target first for immediate energy savings.\nHow calculated: Compared today's telemetry per machine using energy_kwh delta with power integration fallback.",
  "data_table": {
    "headers": ["Machine", "kWh", "Cost INR", "% of Total"],
    "rows": [
      ["CNC_MACHINE_001", 245.3, 2453.00, 35.2],
      ["PRESS_001", 198.5, 1985.00, 28.5],
      ["WELD_ROBOT_01", 156.2, 1562.00, 22.4],
      ...
    ]
  },
  "chart": {
    "type": "bar",
    "title": "Energy Today by Machine",
    "labels": ["CNC_MACHINE_001", "PRESS_001", "WELD_ROBOT_01", ...],
    "datasets": [{"label": "kWh", "data": [245.3, 198.5, 156.2, ...]}]
  },
  "page_links": [
    {"label": "View CNC_MACHINE_001", "route": "/machines/CNC_MACHINE_001"},
    {"label": "Open Energy Report", "route": "/reports"}
  ]
}
```

**User Scenarios**:
- "Which machine consumed the most power today?"
- "Show me the top energy consumers"
- "What's using the most electricity?"

---

### 3. Recent Alerts

**Description**: Displays alerts triggered today, showing which machines have issues and what rules were violated.

**Intent Patterns**:
- "recent alerts"
- "alerts today"
- "rules triggered"
- "anomalies today"
- "most alerts" (shows ranked by count)

**Processing Path**: Quick Template (with fallback to activity events)

**Database Tables Used**:
- `alerts` - Alert records
- `rules` - Rule definitions
- `devices` - Machine information
- `activity_events` - Fallback if no alerts

**SQL Query**:
```sql
SELECT a.device_id, d.device_name, r.rule_name, a.severity, a.status, a.created_at
FROM alerts a
JOIN devices d ON d.device_id = a.device_id
JOIN rules r ON r.rule_id = a.rule_id
WHERE a.created_at >= CURDATE()
AND a.created_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
ORDER BY a.created_at DESC LIMIT 100
```

**Fallback Query** (if no alerts):
```sql
SELECT ae.device_id, d.device_name, COALESCE(r.rule_name, ae.title) AS rule_name,
       CASE WHEN ae.event_type LIKE 'alert_%' THEN 'high' ELSE 'info' END AS severity,
       ae.event_type AS status, ae.created_at
FROM activity_events ae
LEFT JOIN devices d ON d.device_id = ae.device_id
LEFT JOIN rules r ON r.rule_id = ae.rule_id
WHERE ae.created_at >= CURDATE() AND ae.created_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
AND (ae.event_type LIKE 'alert_%' OR ae.event_type IN ('rule_created','rule_updated','rule_triggered'))
ORDER BY ae.created_at DESC LIMIT 100
```

**Response Examples**:

*Standard Alerts View:*
```json
{
  "answer": "Found 15 alert(s) for today.",
  "data_table": {
    "headers": ["device_id", "device_name", "rule_name", "severity", "status", "created_at"],
    "rows": [...]
  }
}
```

*Ranked by Count ("most alerts"):*
```json
{
  "answer": "CNC_MACHINE_001 has the most alerts today (8).",
  "reasoning": {
    "what_happened": "CNC_MACHINE_001 has the highest count today with 8 alerts.",
    "why_it_matters": "This highlights the machine that may need immediate root-cause attention.",
    "how_calculated": "Grouped today's alerts by machine and ranked by count."
  },
  "data_table": {
    "headers": ["Machine", "Count", "Source"],
    "rows": [["CNC_MACHINE_001", 8, "alerts"], ["PRESS_001", 5, "alerts"], ...]
  },
  "chart": {
    "type": "bar",
    "title": "Alert Count Today by Machine",
    "labels": ["CNC_MACHINE_001", "PRESS_001", ...],
    "datasets": [{"label": "Count", "data": [8, 5, ...]}]
  }
}
```

**User Scenarios**:
- "Show me recent alerts"
- "What's alerts today?"
- "Which machine has the most alerts?"
- "Show me rules triggered today"

---

### 4. Idle Waste Analysis

**Description**: Shows machines with idle time and associated energy waste cost, helping identify opportunities for efficiency improvements.

**Intent Patterns**:
- "idle cost"
- "idle running"
- "standby loss"
- "waste energy"

**Processing Path**: Quick Template

**Database Tables Used**:
- `idle_running_log` - Idle duration and cost data
- `devices` - Machine information
- `tariff_config` - Currency/rate information

**SQL Query**:
```sql
SELECT d.device_id, d.device_name, i.idle_duration_sec/3600 AS idle_hours,
       i.idle_energy_kwh, i.idle_cost, i.currency
FROM idle_running_log i
JOIN devices d ON d.device_id = i.device_id
WHERE DATE(i.period_start)=CURDATE() AND i.idle_duration_sec>0
ORDER BY i.idle_cost DESC LIMIT 50
```

**Response Example**:
```json
{
  "answer": "Today, CNC_MACHINE_001 has the highest idle cost at ₹ 125.50.",
  "reasoning": {
    "what_happened": "Today, CNC_MACHINE_001 has the highest idle cost at ₹ 125.50.",
    "why_it_matters": "5 machine(s) are currently adding idle cost, totaling about ₹ 450.75.",
    "how_calculated": "Used today's idle duration and idle energy cost records, then ranked machines by idle cost."
  },
  "data_table": {
    "headers": ["device_id", "device_name", "idle_hours", "idle_energy_kwh", "idle_cost", "currency"],
    "rows": [...]
  },
  "chart": {
    "type": "bar",
    "title": "Idle Cost Today by Machine",
    "labels": ["CNC_MACHINE_001", "PRESS_001", ...],
    "datasets": [{"label": "idle_cost", "data": [125.50, 98.30, ...]}]
  },
  "follow_up_suggestions": [
    "Why is this machine idle so long?",
    "Show standby loss this week"
  ]
}
```

**User Scenarios**:
- "What's the idle cost today?"
- "Which machines are wasting energy?"
- "Show me standby losses"

---

### 5. Health Scores / Machine Status

**Description**: Displays machine health information based on last seen timestamps, helping identify offline or unreachable devices.

**Intent Patterns**:
- "health score"
- "lowest efficiency"
- "below 80% efficiency"
- "machine status"

**Processing Path**: Quick Template

**Database Tables Used**:
- `devices` - Machine information

**SQL Query**:
```sql
SELECT device_id, device_name, legacy_status, last_seen_timestamp
FROM devices ORDER BY last_seen_timestamp ASC LIMIT 50
```

**Response Example**:
```json
{
  "answer": "Found 10 machines in the system. Showing machines by last seen timestamp.",
  "data_table": {
    "headers": ["device_id", "device_name", "legacy_status", "last_seen_timestamp"],
    "rows": [
      ["PRESS_001", "Hydraulic Press 1", "inactive", "2024-01-15 08:30:00"],
      ["CONVEYOR_02", "Conveyor Belt 2", "active", "2024-01-15 09:15:00"],
      ...
    ]
  },
  "follow_up_suggestions": [
    "Which machine was offline longest today?",
    "Show recent alerts for the first machine"
  ]
}
```

**User Scenarios**:
- "Show me machine health"
- "Which machines are offline?"
- "What's the status of all machines?"

---

### 6. Telemetry Trends

**Description**: Displays power consumption trends for a specific machine over the last 7 days, helping identify patterns, spikes, and anomalies.

**Intent Patterns**:
- "trend"
- "last 7 days"
- "spike"
- "at 3pm"
- "over time"
- "yesterday"

**Processing Path**: Telemetry (Data Service)

**Data Source**: `data-service` - `/api/v1/data/telemetry/{device_id}`

**Resolution Process**:
1. Extract device ID from message or conversation history
2. If no device specified, prompt user to specify
3. Fetch 7 days of power telemetry
4. Generate line chart with up to 200 data points

**Response Example**:
```json
{
  "answer": "Showing power trend for CNC_MACHINE_001 over the last 7 days.",
  "reasoning": {
    "what_happened": "Showing power trend for CNC_MACHINE_001 over the last 7 days.",
    "why_it_matters": "This helps you spot spikes, instability, and recurring load patterns.",
    "how_calculated": "Used the machine's timestamped power telemetry from the last 7 days."
  },
  "data_table": {
    "headers": ["Timestamp", "Power"],
    "rows": [
      ["2024-01-09 00:00", 45.2],
      ["2024-01-09 01:00", 42.1],
      ...
    ]
  },
  "chart": {
    "type": "line",
    "title": "Power Trend: CNC_MACHINE_001",
    "labels": ["2024-01-09 00:00", "2024-01-09 01:00", ...],
    "datasets": [{"label": "Power", "data": [45.2, 42.1, ...]}]
  },
  "page_links": [
    {"label": "View CNC_MACHINE_001", "route": "/machines/CNC_MACHINE_001"}
  ],
  "follow_up_suggestions": [
    "Why did it spike at 3pm?",
    "Show idle cost today for this machine",
    "Show recent alerts for this machine"
  ]
}
```

**User Scenarios**:
- "Show me power trend for CNC_MACHINE_001"
- "What was the power consumption over the last week?"
- "Did we have any power spikes?"

---

### 7. AI-SQL (Natural Language Queries)

**Description**: Handles free-form natural language queries that don't match predefined patterns. The AI generates appropriate SQL based on the database schema.

**Processing Path**: Two-Stage AI

**Stage 1: SQL Generation**
- Sends schema + conversation + question to AI
- AI returns SQL query or "NO_DATA" if unable

**Stage 2: Response Formatting**
- Executes the generated SQL
- Sends results back to AI for natural language formatting
- AI returns structured JSON response

**Example Conversations**:

*User: "What's the average power consumption for all active machines?"*

```sql
-- Stage 1: Generated SQL
SELECT d.device_id, d.device_name, AVG(t.power) AS avg_power
FROM devices d
LEFT JOIN (
    SELECT device_id, AVG(power) AS power 
    FROM device_telemetry 
    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    GROUP BY device_id
) t ON d.device_id = t.device_id
WHERE d.legacy_status = 'active'
GROUP BY d.device_id, d.device_name
ORDER BY avg_power DESC
LIMIT 20
```

*User: "Compare energy usage between morning and night shifts"*

```sql
-- Stage 1: Generated SQL
SELECT 
    CASE 
        WHEN HOUR(i.period_start) BETWEEN 6 AND 14 THEN 'Morning Shift'
        ELSE 'Night Shift'
    END AS shift,
    SUM(i.idle_energy_kwh) AS total_energy_kwh,
    SUM(i.idle_cost) AS total_cost
FROM idle_running_log i
WHERE DATE(i.period_start) = CURDATE()
GROUP BY CASE 
    WHEN HOUR(i.period_start) BETWEEN 6 AND 14 THEN 'Morning Shift'
    ELSE 'Night Shift'
END
```

**Response Example**:
```json
{
  "answer": "Active machines consumed a total of 1,250 kWh today, with CNC_MACHINE_001 leading at 245 kWh.",
  "reasoning": "What happened: Active machines had significant energy consumption today.\nWhy it matters: Understanding consumption patterns helps optimize energy usage.\nHow calculated: Summed 24-hour energy data for all active machines.",
  "data_table": {
    "headers": ["device_id", "device_name", "avg_power"],
    "rows": [...]
  },
  "follow_up_suggestions": [
    "Show this as a 7-day trend",
    "Which machine contributed most to this result?",
    "Show recent alerts related to this machine"
  ]
}
```

**User Scenarios**:
- "What's the total energy consumption this month?"
- "Compare machines by efficiency"
- "Show me any anomalies in the last week"
- Custom queries not covered by predefined templates

---

## API Reference

### Endpoint: Chat

#### POST `/api/v1/copilot/chat`

Send a natural language question to the Copilot Service.

**Request Body**:

```json
{
  "message": "Which machine consumed the most power today?",
  "conversation_history": [
    {"role": "user", "content": "Summarize today's factory performance"},
    {"role": "assistant", "answer": "Today, CNC_MACHINE_001 has the highest idle energy..."}
  ]
}
```

**Parameters**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | User's question in natural language (min 1 character) |
| `conversation_history` | array | No | Array of previous chat turns for context |

**Conversation History Object**:

| Field | Type | Description |
|-------|------|-------------|
| `role` | string | "user" or "assistant" |
| `content` | string | The message content |

**Response Body**:

```json
{
  "answer": "CNC_MACHINE_001 consumed the most energy today at 245.3 kWh (₹ 2,453.00).",
  "reasoning": "What happened: Today, CNC_MACHINE_001 consumed the most energy at 245.3 kWh.\nWhy it matters: This highlights the best machine to target first for immediate energy savings.\nHow calculated: Compared today's telemetry per machine using energy_kwh delta with power integration fallback.",
  "reasoning_sections": {
    "what_happened": "Today, CNC_MACHINE_001 consumed the most energy at 245.3 kWh.",
    "why_it_matters": "This highlights the best machine to target first for immediate energy savings.",
    "how_calculated": "Compared today's telemetry per machine using energy_kwh delta with power integration fallback."
  },
  "data_table": {
    "headers": ["Machine", "kWh", "Cost INR", "% of Total"],
    "rows": [
      ["CNC_MACHINE_001", 245.3, 2453.00, 35.2],
      ["PRESS_001", 198.5, 1985.00, 28.5]
    ]
  },
  "chart": {
    "type": "bar",
    "title": "Energy Today by Machine",
    "labels": ["CNC_MACHINE_001", "PRESS_001", "WELD_ROBOT_01"],
    "datasets": [{"label": "kWh", "data": [245.3, 198.5, 156.2]}]
  },
  "page_links": [
    {"label": "View CNC_MACHINE_001", "route": "/machines/CNC_MACHINE_001"},
    {"label": "Open Energy Report", "route": "/reports"}
  ],
  "follow_up_suggestions": [
    "Why did it spike at 3pm?",
    "Show this machine trend for last 7 days",
    "What is this machine idle cost today?"
  ],
  "error_code": null
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `answer` | string | Natural language answer to user's question |
| `reasoning` | string | Human-readable explanation |
| `reasoning_sections` | object | Structured reasoning (what_happened, why_it_matters, how_calculated) |
| `data_table` | object | Tabular data (headers, rows) - may be null |
| `chart` | object | Chart visualization - may be null |
| `page_links` | array | Navigation links - may be null |
| `follow_up_suggestions` | array | Suggested follow-up questions (max 3) |
| `error_code` | string | Error code if applicable - null if successful |

### Error Codes

| Error Code | Description | Cause |
|------------|-------------|-------|
| `NOT_CONFIGURED` | AI provider not configured | Missing API key or invalid provider |
| `AI_UNAVAILABLE` | AI service temporarily unavailable | Provider API failure |
| `QUERY_BLOCKED` | Query was blocked for security | Invalid or disallowed SQL |
| `QUERY_TIMEOUT` | Query exceeded time limit | Query took too long |
| `QUERY_FAILED` | Query execution failed | Database error |
| `NO_DATA` | No data found for query | Empty result set |
| `MODULE_NOT_AVAILABLE` | Requested module not supported | Unsupported feature |
| `INTERNAL_ERROR` | Unexpected server error | Unknown error |

### Example Requests

#### cURL Example

```bash
curl -X POST "http://localhost:8007/api/v1/copilot/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Which machine consumed the most power today?",
    "conversation_history": []
  }'
```

#### Python Example

```python
import httpx

async def chat():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8007/api/v1/copilot/chat",
            json={
                "message": "Which machine consumed the most power today?",
                "conversation_history": [
                    {"role": "user", "content": "Show me recent alerts"}
                ]
            }
        )
        return response.json()

result = await chat()
print(result["answer"])
```

#### JavaScript Example

```javascript
const response = await fetch('http://localhost:8007/api/v1/copilot/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: 'Which machine consumed the most power today?',
    conversation_history: []
  })
});

const data = await response.json();
console.log(data.answer);
```

---

### Endpoint: Health Check

#### GET `/health`

Check if the Copilot Service is running and provider configuration status.

**Response**:

```json
{
  "status": "ok",
  "provider": "groq",
  "provider_configured": true
}
```

---

### Endpoint: Readiness Check

#### GET `/ready`

Check if the Copilot Service is ready to handle requests (database and schema loaded).

**Response**:

```json
{
  "status": "ready",
  "checks": {
    "schema_loaded": true,
    "provider_configured": true,
    "provider_ping": true,
    "db_ready": true
  }
}
```

---

## Configuration

### Environment Variables

The Copilot Service is configured via environment variables. Create a `.env` file in the service directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | copilot-service | Application name |
| `APP_VERSION` | 1.0.0 | Application version |
| `LOG_LEVEL` | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |

### AI Provider Configuration

You must configure at least one AI provider:

#### Using Groq (Recommended - Default)

```bash
AI_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key
```

**Model Used**: `llama-3.1-70b-versatile`

#### Using Google Gemini

```bash
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
```

**Model Used**: `gemini-1.5-flash`

#### Using OpenAI

```bash
AI_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
```

**Model Used**: `gpt-4o-mini`

### Database Configuration

```bash
# MySQL Connection
MYSQL_URL=mysql+aiomysql://copilot_reader:copilot_readonly_pass@mysql:3306/ai_factoryops
```

Format: `mysql+aiomysql://username:password@host:port/database_name`

### Service URLs

```bash
# Data Service (for telemetry)
DATA_SERVICE_URL=http://data-service:8081

# Reporting Service (for tariff rates)
REPORTING_SERVICE_URL=http://reporting-service:8085
```

### Query Settings

```bash
# Maximum rows to return from queries
MAX_QUERY_ROWS=200

# Query timeout in seconds
QUERY_TIMEOUT_SEC=10

# Conversation history turns to include
MAX_HISTORY_TURNS=5
```

### AI Token Limits

```bash
# Stage 1: SQL generation max tokens
STAGE1_MAX_TOKENS=500

# Stage 2: Response formatting max tokens
STAGE2_MAX_TOKENS=900
```

### Timezone

```bash
# Factory timezone for date calculations
FACTORY_TIMEZONE=Asia/Kolkata
```

### Complete Example `.env` File

```bash
# Application
APP_NAME=copilot-service
LOG_LEVEL=INFO

# AI Provider
AI_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here

# Database
MYSQL_URL=mysql+aiomysql://copilot_reader:copilot_readonly_pass@mysql:3306/ai_factoryops

# Services
DATA_SERVICE_URL=http://data-service:8081
REPORTING_SERVICE_URL=http://reporting-service:8085

# Settings
MAX_QUERY_ROWS=200
QUERY_TIMEOUT_SEC=10
MAX_HISTORY_TURNS=5
STAGE1_MAX_TOKENS=500
STAGE2_MAX_TOKENS=900
FACTORY_TIMEZONE=Asia/Kolkata
```

---

## Deployment

### Docker Deployment

The Copilot Service is containerized using Docker.

#### Dockerfile Overview

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    default-libmysqlclient-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8007"]
```

#### Build and Run

```bash
# Build the Docker image
docker build -t copilot-service ./services/copilot-service

# Run the container
docker run -d \
  --name copilot-service \
  -p 8007:8007 \
  --env-file ./services/copilot-service/.env \
  copilot-service
```

### Service Ports

| Port | Service |
|------|---------|
| 8007 | Copilot Service API |

### Health Checks

The service provides two health endpoints:

- **`GET /health`**: Basic health check - returns service status and AI provider info
- **`GET /ready`**: Readiness check - verifies database connection and schema loading

### Startup Process

1. **Database Connection**: Test MySQL connection with `SELECT 1`
2. **Schema Loading**: Load database schema into memory
3. **AI Provider Configuration**: Initialize configured AI provider
4. **Provider Health Check**: Ping AI provider to verify connectivity

### Dependencies

Runtime dependencies (from `requirements.txt`):

```
fastapi==0.109.0
uvicorn[standard]==0.27.0
httpx==0.26.0
sqlalchemy[asyncio]==2.0.25
aiomysql==0.2.0
pydantic==2.5.3
pydantic-settings==2.1.0
python-dotenv==1.0.0
groq==0.11.0
google-generativeai==0.7.2
openai==1.40.0
pytest==7.4.4
pytest-asyncio==0.23.3
```

### Integration with Docker Compose

Example snippet from `docker-compose.yml`:

```yaml
copilot-service:
  build:
    context: ./services/copilot-service
  container_name: copilot-service
  ports:
    - "8007:8007"
  env_file:
    - ./services/copilot-service/.env
  depends_on:
    - mysql
    - data-service
    - reporting-service
  networks:
    - factoryops-network
```

---

## Security

### SQL Injection Prevention

The Copilot Service implements multiple layers of security to prevent malicious SQL queries:

#### 1. SELECT-Only Enforcement

All queries must start with `SELECT`:

```python
if not upper.startswith("SELECT"):
    return False, "Only SELECT queries are allowed"
```

#### 2. Blocked Keywords

The following SQL keywords are blocked:

```python
BLOCKED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "TRUNCATE", "CREATE", "GRANT", "REVOKE",
    "EXEC", "EXECUTE", "CALL", "LOAD", "OUTFILE", "DUMPFILE"
}
```

#### 3. Query Validation Process

```
User Question
     │
     ▼
┌─────────────┐
│   Stage 1   │ ──▶ AI generates SQL
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Stage 2   │ ──▶ Remove comments & literals
│   Clean     │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Stage 3   │ ──▶ Check for SELECT start
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Stage 4   │ ──▶ Tokenize & check blocked keywords
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Stage 5   │ ──▶ Check query length (< 4000 chars)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Stage 6   │ ──▶ Allow single statement only
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Execute   │ ──▶ Run validated query
└─────────────┘
```

#### 4. Read-Only Database User

The service connects using a read-only database user:

```
Username: copilot_reader
Password: copilot_readonly_pass
Permissions: SELECT only
```

### Query Timeout Protection

All queries have a timeout to prevent resource exhaustion:

```python
TIMEOUT = 10 seconds
```

### Response Sanitization

AI-generated responses are sanitized to prevent injection of malicious content:

- Chart data is validated and converted to proper types
- Technical artifacts (like `Decimal()`, `datetime()`) are removed
- Invalid chart configurations are rejected

---

## Troubleshooting

### Common Issues and Solutions

#### 1. AI Provider Not Configured

**Symptom**:
```json
{
  "answer": "Copilot is not configured. Please add AI_PROVIDER and provider API key.",
  "error_code": "NOT_CONFIGURED"
}
```

**Solution**:
1. Check your `.env` file has `AI_PROVIDER` set
2. Ensure the corresponding API key is set:
   - For Groq: `GROQ_API_KEY`
   - For Gemini: `GEMINI_API_KEY`
   - For OpenAI: `OPENAI_API_KEY`
3. Verify the API key is valid and has sufficient quota

---

#### 2. AI Service Unavailable

**Symptom**:
```json
{
  "answer": "AI service is temporarily unavailable. Please try again.",
  "error_code": "AI_UNAVAILABLE"
}
```

**Solution**:
1. Check your internet connection
2. Verify the AI provider's status page
3. Check API key quota/limits
4. Try a different AI provider in configuration

---

#### 3. Database Connection Failed

**Symptom**:
- Service fails to start
- `/ready` endpoint shows `"db_ready": false`

**Solution**:
1. Verify MySQL is running and accessible
2. Check `MYSQL_URL` in configuration
3. Verify database user has SELECT permissions
4. Check network connectivity between containers

---

#### 4. Query Timeout

**Symptom**:
```json
{
  "answer": "Something went wrong. Please try again.",
  "reasoning": "Query timed out",
  "error_code": "QUERY_TIMEOUT"
}
```

**Solution**:
1. Simplify your question
2. Add time period constraints (e.g., "today" instead of "all time")
3. Ask about specific machines rather than all machines

---

#### 5. No Data Found

**Symptom**:
```json
{
  "answer": "No data found for this period.",
  "error_code": "NO_DATA"
}
```

**Solution**:
1. Check if data exists for the requested time period
2. Verify machines are onboarded and reporting
3. Check if alerts/rules are configured

---

#### 6. Schema Not Loaded

**Symptom**:
- `/ready` shows `"schema_loaded": false`
- AI-SQL queries fail

**Solution**:
1. Check database connectivity
2. Verify database has tables
3. Review startup logs for schema loading errors

---

### Diagnostic Commands

#### Check Service Health

```bash
curl http://localhost:8007/health
```

#### Check Service Readiness

```bash
curl http://localhost:8007/ready
```

#### View Service Logs

```bash
# Docker
docker logs copilot-service

# Docker Compose
docker-compose logs copilot-service

# Direct (with LOG_LEVEL=DEBUG)
uvicorn main:app --log-level debug
```

### Debug Mode

Enable debug logging to troubleshoot issues:

```bash
LOG_LEVEL=DEBUG
```

---

## Related Documentation

### Project Documentation

- **Root README**: `/README.md` - Project overview and architecture
- **Schema Documentation**: `/schema.md` - Database schema reference
- **Formulas**: `/formulas.md` - Calculation formulas used in the system
- **Verification**: `/verification.md` - Testing and verification procedures

### Database Schema

The Copilot Service works with the following main tables:

| Table | Description |
|-------|-------------|
| `devices` | Onboarded machines and metadata |
| `device_shifts` | Shift windows per machine |
| `rules` | Rule definitions for alerts |
| `alerts` | Alert records for triggered rules |
| `activity_events` | Activity/event stream |
| `idle_running_log` | Idle duration, energy, and cost |
| `tariff_config` | Current tariff settings |
| `notification_channels` | Notification recipients |
| `energy_reports` | Energy report job records |
| `waste_analysis_jobs` | Waste analysis jobs |
| `device_performance_trends` | Health/uptime trends |
| `device_properties` | Telemetry property metadata |

### External Services

The Copilot Service integrates with:

1. **Data Service** (`data-service:8081`)
   - Provides real-time telemetry data
   - Endpoint: `/api/v1/data/telemetry/{device_id}`

2. **Reporting Service** (`reporting-service:8085`)
   - Provides tariff configuration
   - Endpoint: `/api/v1/settings/tariff`

3. **MySQL Database** (`mysql:3306`)
   - FactoryOPS operational database
   - Database name: `ai_factoryops`

---

## Appendix

### Intent Classification Patterns

| Intent | Patterns |
|--------|----------|
| `factory_summary` | summarize, factory performance, overview today, top problems |
| `top_energy_today` | most power today, most energy today, consumed the most power |
| `alerts_recent` | recent alerts, alerts today, rules triggered, anomalies today |
| `idle_waste` | idle cost, idle running, standby loss, waste energy |
| `health_scores` | health score, lowest efficiency, below 80% efficiency |
| `telemetry_trend` | trend, last 30 days, spike, at 3pm, over time, yesterday |
| `unsupported` | oee, overall equipment effectiveness, yield, production count |

### Response Format Versions

Current version: `1.0.0`

The API uses semantic versioning for the response format.

### Rate Limits

- No built-in rate limits (handled by upstream API gateway)
- AI provider rate limits apply based on your plan

### Support and Feedback

For issues, questions, or feedback:
- Check the troubleshooting guide above
- Review logs for detailed error messages
- Verify configuration is correct

---

*Document Version: 1.0.0*
*Last Updated: March 2024*
