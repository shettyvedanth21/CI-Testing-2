# Shivex Privacy Policy

Last updated: 2026-05-16

This Privacy Policy explains how Shivex collects, uses, stores, and protects information when organisations use the Shivex platform for industrial monitoring, analytics, reporting, and operational workflows.

This document is intended to provide a clear baseline privacy posture for the current product. It should still be reviewed by business owners and legal counsel before any public production launch.

## 1. Scope

Shivex is a multi-tenant industrial operations platform used to:

- manage organisations, plants, and users
- onboard and manage monitored devices
- receive and process machine telemetry
- generate analytics, reports, exports, and operational summaries
- optionally provide AI-assisted operational workflows through Copilot features

This policy applies to data processed by the Shivex web app, backend services, worker processes, and approved storage systems used by the platform.

## 2. Data We Collect

### 2.1 User and account data

Shivex stores account and access-management data needed to authenticate users and manage tenant access, including:

- email address
- hashed password
- full name, when provided
- role and permission level
- organisation membership
- plant access assignments
- account lifecycle timestamps such as invitation, activation, deactivation, and last login
- refresh-token and access-control metadata required for secure session handling

### 2.2 Organisation and plant data

Shivex stores tenant and operational-structure data, including:

- organisation name
- organisation slug / tenant identifier
- plant name
- plant location, when provided
- plant timezone
- feature entitlement and access-control configuration

### 2.3 Device and telemetry data

Shivex stores device-management and telemetry-related data, including:

- device identifiers
- tenant and plant assignment
- device name, type, manufacturer, model, and location metadata
- device operational configuration
- device health / threshold configuration
- device status projection and recent telemetry state
- incoming telemetry such as voltage, current, power, temperature, energy-related values, and derived operational metrics
- device maintenance and runtime tracking records
- MQTT credential/ACL metadata used for controlled device connectivity

### 2.4 Reporting, export, and analytics data

Shivex may store or generate:

- scheduled and on-demand reports
- report status, job progress, and report history
- analytics job metadata and result artifacts
- export checkpoints and exported datasets
- audit and reconciliation records used to validate energy calculations and system consistency

### 2.5 Copilot / AI-assisted workflow data

If Copilot or similar AI-assisted workflow features are enabled, Shivex may process:

- user-submitted operational questions
- limited recent conversation history needed to answer the current question
- tenant-scoped operational context needed to produce a response
- generated reasoning, summaries, tables, and charts returned to the user

AI features should be treated as operational tooling, not as a place to submit unnecessary personal or regulated sensitive data.

## 3. How We Use Data

Shivex uses data to:

- authenticate users and enforce tenant / plant access boundaries
- onboard, identify, and manage devices
- ingest and process telemetry from industrial equipment
- compute fleet dashboards, trends, losses, and health indicators
- generate reports, exports, and analytics outputs
- deliver notifications, invitations, password resets, and maintenance communications
- support operational AI / Copilot workflows when enabled
- maintain security, detect failures, investigate incidents, and protect platform integrity

## 4. Where Data Is Stored

Based on the current system design, Shivex data may be stored in a combination of:

- relational database storage for users, organisations, plants, devices, permissions, jobs, and audit metadata
- Redis for operational queues, stream processing, rate limiting, and short-lived coordination state
- InfluxDB or equivalent telemetry storage for time-series machine telemetry
- S3-compatible object storage for report artifacts, export files, and generated assets
- application logs and monitoring systems for troubleshooting, operational observability, and security review

Deployment-specific storage locations may vary by environment, but production systems should use approved managed or protected infrastructure only.

## 5. Who Can Access Data

Access to data is intended to follow role and tenant boundaries:

- **Super admins** may administer the platform and organisation-level onboarding
- **Organisation admins** may manage users, plants, and organisation-scoped operational data
- **Plant managers / operators / viewers** should only access the plants and devices granted to them
- **Backend services and workers** may access data needed to perform platform operations
- **Approved infrastructure operators** may access logs, databases, and storage systems only for operational, security, support, or maintenance purposes

Shivex is designed to enforce tenant separation in application logic and service APIs. Access should always be restricted to the minimum necessary scope.

## 6. AI and Third-Party Processing

When AI-assisted features are enabled, Shivex may send limited request data to an approved model provider configured by the platform operator. Depending on deployment configuration, this may include providers such as OpenAI, Google Gemini, or Groq.

AI-assisted processing should follow these principles:

- only send the minimum data needed to answer the request
- avoid placing secrets, credentials, or unnecessary sensitive personal data into prompts
- keep provider API keys server-side only
- review provider settings and data-handling terms before enabling the integration

If AI features are disabled, no such provider processing should occur.

## 7. Data Retention

Shivex retains data for operational, security, audit, and reporting purposes. Retention windows depend on the type of data and deployment settings.

In general:

- account and tenant records are retained while the organisation remains active and for a reasonable period required for audit or access recovery
- telemetry, projection, queue, and report data may be retained according to service-specific retention and cleanup settings
- logs and operational diagnostics should be retained only as long as necessary for troubleshooting, security review, and compliance needs
- exported artifacts and generated reports may remain in object storage until deleted by policy or operator action

Production operators should define explicit retention schedules for:

- telemetry history
- logs
- report artifacts
- export files
- maintenance/audit trails

## 8. Security Measures

Shivex uses technical measures intended to reduce risk, including:

- authenticated access control
- role- and tenant-scoped authorization
- hashed passwords
- security headers in the web application
- rate limiting on selected auth flows
- service-level health checks and operational monitoring
- separation of frontend and backend secrets

No platform can guarantee absolute security. Operators must still configure infrastructure, secrets, network boundaries, backups, and monitoring correctly.

## 9. User Rights, Corrections, and Deletion Requests

Where applicable, users and customer organisations may request:

- correction of inaccurate account information
- deactivation of accounts
- review of access assignments
- deletion of customer-controlled data, subject to legal, contractual, and operational retention requirements

Requests should be routed through the designated Shivex operator, customer administrator, or support contact responsible for the environment.

## 10. Contact and Responsibility

Questions about privacy, access, correction, deletion, or data handling should be directed to the designated Shivex support or platform operations contact for the deployment in use.

Before a public or broad production launch, this policy should be reviewed and approved by:

- product/business owner
- platform operator
- legal/privacy reviewer
- security reviewer

