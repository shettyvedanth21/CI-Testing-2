# Shivex Data Handling Reference

Last updated: 2026-05-16

This document is an internal operational reference for how Shivex currently handles data in the codebase. It is meant to support implementation clarity, privacy reviews, security hardening, and production readiness planning.

## 1. Primary Data Domains

### 1.1 Identity and access

Handled primarily by `auth-service`.

Core data types:

- organisations / tenants
- plants
- users
- user roles
- plant access assignments
- refresh tokens
- invite / password-reset action tokens
- platform maintenance announcements and delivery metadata

Relevant code:

- [services/auth-service/app/models/auth.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/auth-service/app/models/auth.py)

### 1.2 Device registry and operational metadata

Handled primarily by `device-service`.

Core data types:

- device identifiers
- tenant and plant mapping
- device name/type/manufacturer/model/location
- runtime snapshot state
- maintenance logs
- device health configuration
- device MQTT credential and ACL metadata
- device state intervals and recent telemetry snapshots

Relevant code:

- [services/device-service/app/models/device.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/device-service/app/models/device.py)

### 1.3 Telemetry and time-series operations

Handled primarily by `data-service`, with downstream consumers.

Core data types:

- telemetry measurements
- tenant-scoped telemetry streams
- queue/backlog/dead-letter operational metadata
- projection and reconciliation state
- outbox relay metadata for downstream services

Time-series telemetry is stored separately from the main relational identity data.

### 1.4 Energy, reporting, analytics, and exports

Handled across:

- `energy-service`
- `reporting-service`
- `analytics-service`
- `data-export-service`
- `waste-analysis-service`

Core data types:

- daily/monthly energy aggregates
- report jobs and report history
- analytics job state and output artifacts
- export checkpoints and S3-compatible object storage artifacts
- waste and loss analysis results
- reconcile/audit records for derived calculations

Relevant code examples:

- [services/energy-service/app/models.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/energy-service/app/models.py)
- [services/data-export-service/models.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/data-export-service/models.py)

### 1.5 Copilot / AI-assisted workflows

Handled by `copilot-service` and the `ui-web` Copilot experience.

Current data handling characteristics:

- requests include the current message plus limited recent conversation history
- tenant-scoped operational context is used to answer the request
- provider keys remain server-side
- AI provider usage is configuration-dependent
- current UI assumptions have been curated-question focused during recent validation work

Relevant code:

- [services/copilot-service/src/api/chat.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/copilot-service/src/api/chat.py)
- [services/copilot-service/src/config.py](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/copilot-service/src/config.py)

## 2. Storage Boundaries

### 2.1 Relational database

Used for:

- users
- organisations
- plants
- device registry metadata
- reports and jobs
- auth/session metadata
- audit/reconciliation metadata
- configuration state

### 2.2 Redis

Used for:

- telemetry streams
- queue coordination
- worker/group state
- rate limiting support
- transient synchronization / short-lived runtime state

Redis is not just cache in Shivex; it is a core operational dependency.

### 2.3 Time-series storage

Used for:

- telemetry history
- device measurements
- historical query windows used by dashboards, reports, exports, and analytics

### 2.4 Object storage

Used for:

- generated reports
- export files
- artifacts associated with analytics/reporting workflows

## 3. Access Boundaries

The intended access model is:

- super admin: platform-level administration
- org admin: tenant/org-level administration
- plant manager / operator / viewer: plant-scoped operational access
- backend services: scoped machine-to-machine access required for runtime processing

Internal service-to-service flows should remain scoped and authenticated, and frontend users should only see data within their tenant and plant access boundaries.

## 4. Retention Guidance

The codebase already includes some service-specific cleanup/retention behavior, but production policy should still be made explicit.

Recommended explicit retention categories:

- auth/session tokens
- logs and diagnostics
- telemetry hot history
- report/export artifacts
- analytics job records
- dead-letter and reconciliation records

Suggested operational rule:

- define environment-level retention windows before scaling to broader production usage

## 5. AI / Copilot Handling Guidance

If Copilot remains enabled:

- treat prompts and operational questions as customer data
- avoid placing secrets or credentials into prompts
- keep provider API keys server-side only
- document which provider is enabled in each environment
- review model-provider terms before public launch

## 6. Current Hardening Follow-Ups

These are the main data/privacy/security follow-ups currently worth prioritizing:

1. tighten open CORS settings in services that still use broad defaults
2. add a stronger web Content Security Policy
3. verify no secrets leak into frontend code, API payloads, or logs
4. confirm expensive endpoints are rate-limited or protected by queue/backpressure
5. keep production secrets out of repo/env drift paths and managed securely

## 7. Operational Notes

- This document is a technical reference, not a legal approval artifact.
- The privacy policy should be reviewed with business/legal stakeholders before broad public rollout.
- Memory and runtime truth should continue to be cross-checked against [memory.md](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/memory.md/memory.md) for project-specific operational context.

