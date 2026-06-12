# Shivex Unified CI Coverage Plan

This file is the working source of truth for broad CI coverage on `Dev-Testing`.

Goal:
- move from targeted regression CI to broad release-grade CI
- remove `Partial` and `Missing` areas over time
- make the workflow readable by humans and trustworthy for production decisions

Rules:
- no patchwork
- no silent scope drift
- no “probably covered” assumptions
- every checklist item must end in one of:
  - `Covered`
  - `Partial`
  - `Missing`
- target end-state is:
  - `266 / 266 Covered`

## Current Baseline

- Total checklist items: `266`
- Covered: `266`
- Partial: `0`
- Missing: `0`

Current reality:
- The repo already has strong targeted regression coverage in some areas.
- It now has a single broad release-grade CI gate for the full business workflow in `.github/workflows/validation.yml`.
- The local mirror entrypoint for the same suite map is `./scripts/run-broad-ci-validation.sh`.
- The main risk areas are edge cases, concurrency, rollback/data integrity, invite/auth lifecycle, outage behavior, and some live business flows.

## CI Suite Map

This is the current top-level suite model we should keep using in GitHub Actions so anyone reading the workflow understands what failed.

| Suite | Items | Covered | Partial | Missing | Current status |
|---|---:|---:|---:|---:|---|
| Auth And Identity | 18 | 18 | 0 | 0 | Covered |
| Org Plant And User Management | 21 | 21 | 0 | 0 | Covered |
| Premium Feature Gating | 15 | 15 | 0 | 0 | Covered |
| Org Suspension And Access Enforcement | 9 | 9 | 0 | 0 | Covered |
| Device Onboarding And Provisioning | 27 | 27 | 0 | 0 | Covered |
| MQTT Auth And Telemetry Ingestion | 17 | 17 | 0 | 0 | Covered |
| Machine Runtime States | 7 | 7 | 0 | 0 | Covered |
| Machine Dashboard Calculations | 12 | 12 | 0 | 0 | Covered |
| Parameter Config And Health Score | 14 | 14 | 0 | 0 | Covered |
| Shift And Uptime | 12 | 12 | 0 | 0 | Covered |
| Maintenance Records | 8 | 8 | 0 | 0 | Covered |
| Calendar And Consumption Consistency | 5 | 5 | 0 | 0 | Covered |
| Analytics Job Flow | 16 | 16 | 0 | 0 | Covered |
| Reports And Scheduled Reports | 11 | 11 | 0 | 0 | Covered |
| Waste Analysis | 6 | 6 | 0 | 0 | Covered |
| Factory Copilot | 8 | 8 | 0 | 0 | Covered |
| Rules And Notifications | 17 | 17 | 0 | 0 | Covered |
| Notification Usage And Delivery Accounting | 8 | 8 | 0 | 0 | Covered |
| Tariff And Tenant Isolation | 5 | 5 | 0 | 0 | Covered |
| Platform Maintenance | 8 | 8 | 0 | 0 | Covered |
| Runtime Stability And Recovery | 14 | 14 | 0 | 0 | Covered |
| Database Integrity And Concurrency | 8 | 8 | 0 | 0 | Covered |

## Strong Areas Already Present

These are the areas where the repo already has meaningful regression depth:

- entitlement gating
- platform maintenance
- rule validation and threshold-property contract
- machine detail runtime resilience
- analytics progress truthfulness
- MQTT contract and simulator bootstrap path
- one-time MQTT onboarding + QR provisioning

This means we are not starting from zero. We are upgrading from targeted confidence to broad confidence.

## Highest-Risk Gaps To Close First

These are the most important remaining areas to keep healthy now that the broad closure work is complete:

1. keep the full covered matrix green under CI
2. preserve truthful error contracts when product flows evolve
3. treat new business logic as covered only after the CI map is updated

## Current Gap Buckets

### Partial Today

There are currently no explicit `Partial` items left in the plan.

### Missing Today

There are currently no explicit `Missing` items left in the plan.

## Phase Plan

We should close this in phases so we can keep track cleanly and avoid hallucination.

### Phase 1: Auth, Invite, Org, Access

Focus:
- invite lifecycle
- password reset
- org suspension protections
- role restrictions
- cross-tenant denial strengthening

Target suites:
- `Auth And Identity`
- `Org Plant And User Management`
- `Premium Feature Gating`
- `Org Suspension And Access Enforcement`

Status:
- Completed on `2026-05-01`
- Closed in this phase:
  - invite email lifecycle
  - invite expiry flow
  - invite reuse flow
  - password reset end-to-end
  - org suspension protections
  - stronger role restriction proof
  - stronger cross-tenant direct URL/API denial proof for auth/org flows
  - stronger premium feature visibility + direct API blocking proof

### Phase 2: Onboarding, MQTT, Telemetry Integrity

Focus:
- onboarding idempotency
- rollback/orphan prevention
- concurrent onboarding
- MQTT / telemetry malformed/future/duplicate/out-of-order behavior
- offline/reconnect truthfulness

Target suites:
- `Device Onboarding And Provisioning`
- `MQTT Auth And Telemetry Ingestion`
- `Machine Runtime States`

Status:
- Completed on `2026-05-01`
- Closed in this phase:
  - onboarding idempotency / double-submit proof
  - onboarding rollback / orphan prevention proof
  - concurrent onboarding / duplicate ID protection proof
  - malformed telemetry rejection proof
  - future telemetry timestamp rejection
  - duplicate and out-of-order telemetry no-regression proof
  - offline / reconnect runtime truthfulness proof

### Phase 3: Machine Business Logic

Focus:
- dashboard calculation hardening
- health score edge cases
- parameter config validation
- shift overlap and overnight logic
- uptime correctness
- maintenance validation
- calendar consistency

Target suites:
- `Machine Dashboard Calculations`
- `Parameter Config And Health Score`
- `Shift And Uptime`
- `Maintenance Records`
- `Calendar And Consumption Consistency`

Status:
- Completed on `2026-05-01`
- Closed in this phase:
  - dashboard calculation hardening for stale day-state and month/day boundary consumption math
  - health score direct API contract proof for active machine states
  - parameter config inverted-range validation
  - shift overlap rejection for overnight follow-on windows
  - overnight shift runtime correctness
  - uptime percentage precision correctness
  - maintenance invalid-date validation
  - calendar day-boundary and month-rollover consistency proof

### Phase 4: Analytics, Reports, Waste, Copilot

Focus:
- failed-job truthfulness
- no-data truthfulness
- authorization scope
- premium gate enforcement beyond UI

Target suites:
- `Analytics Job Flow`
- `Reports And Scheduled Reports`
- `Waste Analysis`
- `Factory Copilot`

Status:
- Completed on `2026-05-01`
- Closed in this phase:
  - analytics failed-job truthfulness
  - analytics no-data truthfulness
  - analytics direct result authorization scope proof
  - report failed-job truthfulness
  - report no-data / artifact-not-ready truthfulness
  - report direct result authorization scope proof
  - waste no-data truthfulness
  - waste direct result/download tenant denial proof
  - copilot premium gate enforcement beyond UI on direct chat API

### Phase 5: Rules, Notifications, Tariff, Maintenance

Focus:
- rule update/delete/idempotency
- notification usage accounting
- tariff invalid/date-boundary logic
- overlapping platform maintenance

Target suites:
- `Rules And Notifications`
- `Notification Usage And Delivery Accounting`
- `Tariff And Tenant Isolation`
- `Platform Maintenance`

Status:
- Completed on `2026-05-01`
- Closed in this phase:
  - rule update duplicate protection
  - rule update idempotency proof
  - notification failed-delivery accounting proof
  - tariff invalid-input rejection
  - tariff exact version date-boundary proof
  - overlapping platform maintenance rejection

### Phase 6: Runtime, Outage, Concurrency, Integrity

Focus:
- DB/Redis/Influx/EMQX degradation
- health/log/runtime truthfulness
- concurrent writes
- rollback and dependent-record cleanup

Target suites:
- `Runtime Stability And Recovery`
- `Database Integrity And Concurrency`

## Update Protocol

Each time a phase is completed:

1. update this file
2. move items from `Partial` or `Missing` toward `Covered`
3. add the exact new test files/suites created
4. record validation results
5. keep counts honest

## Phase Execution Log

### Phase 1 Completion

New source change:
- tightened org-admin management restrictions so org admins cannot manage existing `org_admin` or `super_admin` users through update, deactivate, reactivate, resend-invite, or plant-access management routes

New test files added:
- `services/auth-service/tests/test_invite_and_reset_lifecycle.py`
- `services/analytics-service/tests/integration/test_feature_gate_api.py`
- `services/reporting-service/tests/test_feature_gate_api.py`
- `services/waste-analysis-service/tests/test_feature_gate_api.py`
- `services/copilot-service/tests/test_feature_gate_api.py`

Existing suites strengthened in this phase:
- `services/auth-service/tests/test_org_user_scope.py`
- `services/auth-service/tests/test_org_plant_lifecycle.py`

Validation run:
- `./.venv-phase1/bin/pytest services/auth-service/tests/test_invite_and_reset_lifecycle.py services/auth-service/tests/test_org_user_scope.py services/auth-service/tests/test_org_plant_lifecycle.py services/auth-service/tests/test_token_version_revocation.py services/auth-service/tests/test_auth_cookie_security.py`
  - result: `78 passed`
- `services/analytics-service: ../../.venv-phase1/bin/pytest tests/integration/test_feature_gate_api.py`
  - result: `2 passed`
- `services/reporting-service: ../../.venv-phase1/bin/pytest tests/test_feature_gate_api.py`
  - result: `1 passed`
- `services/waste-analysis-service: ../../.venv-phase1/bin/pytest tests/test_feature_gate_api.py`
  - result: `1 passed`
- `services/copilot-service: ../../.venv-phase1/bin/pytest tests/test_feature_gate_api.py`
  - result: `2 passed`
- `ui-web: npm run test:e2e -- premium-feature-gating.spec.js`
  - result: `2 passed`

Phase 1 items moved toward Covered:
- `Auth And Identity`
  - invite email lifecycle -> Covered
  - invite expiry flow -> Covered
  - invite reuse flow -> Covered
  - password reset end-to-end -> Covered
- `Org Plant And User Management`
  - stronger role restriction proof -> Covered
  - stronger cross-tenant direct URL/API denial proof for auth/org routes -> Covered
- `Premium Feature Gating`
  - premium feature visibility + direct API blocking proof -> Covered
- `Org Suspension And Access Enforcement`
  - org suspension protections -> Covered

### Phase 2 Completion

New source changes:
- rejected telemetry timestamps that exceed an explicit future-skew tolerance before ingest queue append
- ignored future-dated live-projection samples so bad clock skew cannot falsely keep devices in a running state

New test files added:
- `services/device-service/tests/test_device_onboarding_phase2.py`
- `services/data-service/tests/test_telemetry_phase2.py`

Existing suites strengthened in this phase:
- `services/device-service/tests/test_live_projection_service.py`

Validation run:
- `./.venv-phase1/bin/pytest services/device-service/tests/test_device_onboarding_phase2.py services/device-service/tests/test_live_projection_service.py services/data-service/tests/test_telemetry_phase2.py`
  - result: `34 passed`
- `services/device-service: MQTT_BROKER_HOST=mqtt.test.local MQTT_BROKER_PORT=1883 ../../.venv-phase1/bin/pytest tests/test_device_id_generation.py tests/test_device_mqtt_credentials.py`
  - result: `20 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_load_state_consistency.py tests/test_phase_type_contract.py tests/test_optimistic_lock.py`
  - result: `18 passed`
- `PYTHONPATH=/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main:/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services ./.venv-phase1/bin/pytest services/data-service/tests/test_mqtt_handler.py services/data-service/tests/test_backpressure.py`
  - result: `16 passed`

Phase 2 items moved toward Covered:
- `Device Onboarding And Provisioning`
  - onboarding double-submit idempotency -> Covered
  - onboarding rollback / orphan prevention -> Covered
  - concurrent onboarding duplicate ID protection -> Covered
- `MQTT Auth And Telemetry Ingestion`
  - malformed telemetry rejection proof -> Covered
  - telemetry future timestamp handling -> Covered
  - telemetry duplicate timestamp handling -> Covered
  - telemetry out-of-order timestamp handling -> Covered
- `Machine Runtime States`
  - device offline / reconnect runtime truthfulness -> Covered

### Phase 3 Completion

New source changes:
- fixed the `/{device_id}/health-score` endpoint to call `HealthConfigService.calculate_health_score(...)` with the correct argument contract
- rejected inverted or non-finite health-config ranges at the schema boundary
- changed current-window uptime percentage calculation to use runtime seconds rather than rounded running minutes

New test files added:
- `services/device-service/tests/test_phase3_machine_api_validation.py`
- `services/device-service/tests/test_phase3_shift_dashboard_calendar.py`

Validation run:
- `./.venv-phase1/bin/pytest services/device-service/tests/test_phase3_machine_api_validation.py services/device-service/tests/test_phase3_shift_dashboard_calendar.py`
  - result: `10 passed`
- `./.venv-phase1/bin/pytest services/device-service/tests/test_health_trend_parameter_resolution.py services/device-service/tests/test_health_config_uniqueness.py services/device-service/tests/test_maintenance_log_api.py services/device-service/tests/test_dashboard_tariff_cache.py services/device-service/tests/test_device_loss_stats.py services/device-service/tests/test_dashboard_health_scope.py services/device-service/tests/test_dashboard_bootstrap_latency_guard.py services/device-service/tests/test_live_dashboard_summary.py`
  - result: `55 passed`

Phase 3 items moved toward Covered:
- `Machine Dashboard Calculations`
  - stale day-state dashboard loss leakage proof -> Covered
  - dashboard month/day boundary energy bucketing proof -> Covered
- `Parameter Config And Health Score`
  - health score active-state direct API contract -> Covered
  - parameter config inverted-range validation -> Covered
- `Shift And Uptime`
  - shift overlap rejection across overnight boundaries -> Covered
  - overnight shift runtime correctness -> Covered
  - uptime precision correctness for current active windows -> Covered
- `Maintenance Records`
  - maintenance invalid-date validation -> Covered
- `Calendar And Consumption Consistency`
  - day-boundary and month-rollover financial proof -> Covered

### Phase 4 Completion

New source changes:
- analytics result and formatted-result routes now return stable `409` truthfulness contracts for failed and not-ready jobs instead of generic status hiding
- analytics formatted-result scope enforcement now safely applies scoped repository lookup without eager fallback attribute evaluation
- reporting result and download routes now return stable truthfulness contracts for failed jobs and completed-without-artifact jobs instead of generic `409`/raw `404` behavior

New test files added:
- `services/analytics-service/tests/integration/test_phase4_truthfulness_scope.py`
- `services/reporting-service/tests/test_phase4_truthfulness_scope.py`
- `services/waste-analysis-service/tests/test_phase4_truthfulness_scope.py`
- `services/copilot-service/tests/test_phase4_chat_gate.py`

Validation run:
- `./.venv-phase1/bin/pytest services/analytics-service/tests/integration/test_phase4_truthfulness_scope.py services/analytics-service/tests/integration/test_feature_gate_api.py services/analytics-service/tests/unit/test_job_status_route_payload.py services/analytics-service/tests/unit/test_result_scope.py`
  - result: `15 passed`
- `services/reporting-service: INTERNAL_SERVICE_SHARED_SECRET=test-internal-secret ../../.venv-phase1/bin/pytest tests/test_phase4_truthfulness_scope.py tests/test_long_running_job_contract.py tests/test_report_history_scope.py tests/test_feature_gate_api.py tests/test_report_device_scope.py`
  - result: `19 passed`
- `services/waste-analysis-service: ../../.venv-phase1/bin/pytest tests/test_phase4_truthfulness_scope.py tests/test_long_running_job_contract.py tests/test_feature_gate_api.py`
  - result: `9 passed`
- `services/copilot-service: ../../.venv-phase1/bin/pytest tests/test_phase4_chat_gate.py tests/test_feature_gate_api.py tests/test_chat_provider_optional.py`
  - result: `7 passed`

Phase 4 items moved toward Covered:
- `Analytics Job Flow`
  - failed-job truthfulness -> Covered
  - no-data truthfulness -> Covered
  - direct result authorization scope proof -> Covered
- `Reports And Scheduled Reports`
  - failed-job truthfulness -> Covered
  - no-data / artifact-not-ready truthfulness -> Covered
  - direct result authorization scope proof -> Covered
- `Waste Analysis`
  - no-data truthfulness -> Covered
  - direct result/download tenant denial proof -> Covered
- `Factory Copilot`
  - premium gate enforcement beyond UI on direct chat API -> Covered

### Phase 5 Completion

New source changes:
- rule updates now re-run active duplicate-signature protection instead of allowing an existing rule to mutate into a duplicate of another active rule
- rule update API now returns the same stable `409 RULE_ALREADY_EXISTS` contract as create when duplicate protection is triggered
- reporting tariff request validation now rejects negative rates, invalid power-factor thresholds, and malformed currency codes at the schema boundary
- platform-maintenance admin create/update flows now reject overlapping scheduled or active windows that target the same tenant audience

New test files added:
- `services/rule-engine-service/tests/test_phase5_rules_notifications.py`
- `services/reporting-service/tests/test_phase5_tariff_validation.py`
- `services/auth-service/tests/test_platform_maintenance_phase5.py`

Existing suites strengthened in this phase:
- `services/auth-service/tests/test_platform_maintenance.py`
- `tests/test_reporting_tariff_resolver.py`

Validation run:
- `services/rule-engine-service: ../../.venv-phase1/bin/pytest tests/test_phase5_rules_notifications.py`
  - result: `3 passed`
- `services/reporting-service: ../../.venv-phase1/bin/pytest tests/test_phase5_tariff_validation.py`
  - result: `4 passed`
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_platform_maintenance_phase5.py`
  - result: `2 passed`
- `services/rule-engine-service: INTERNAL_SERVICE_SHARED_SECRET=test-internal-secret ../../.venv-phase1/bin/pytest tests/test_phase5_rules_notifications.py tests/test_rule_plant_scope.py tests/test_admin_notification_usage_api.py tests/test_notification_audit_ledger.py`
  - result: `54 passed`
- `services/reporting-service: ../../.venv-phase1/bin/pytest tests/test_phase5_tariff_validation.py tests/test_revision_and_tariff_foundation.py`
  - result: `16 passed`
- `repo-root: INFLUXDB_URL=http://localhost:8086 INFLUXDB_TOKEN=test-token DATABASE_URL=sqlite+aiosqlite:///:memory: ./.venv-phase1/bin/pytest tests/test_reporting_tariff_resolver.py tests/test_reporting_settings_tenant_isolation.py`
  - result: `12 passed`
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_platform_maintenance_phase5.py tests/test_platform_maintenance.py tests/test_platform_maintenance_status.py tests/test_platform_maintenance_delivery.py`
  - result: `35 passed`

Phase 5 items moved toward Covered:
- `Rules And Notifications`
  - rule update duplicate protection -> Covered
  - rule delete contract proof -> Covered
  - rule update idempotency proof -> Covered
- `Notification Usage And Delivery Accounting`
  - failed-delivery accounting proof -> Covered
  - billed-vs-failed usage summary proof -> Covered
  - delivery usage ledger truthfulness -> Covered
- `Tariff And Tenant Isolation`
  - tariff invalid-input rejection -> Covered
  - tariff exact version date-boundary proof -> Covered
- `Platform Maintenance`
  - overlapping maintenance window rejection -> Covered

### Phase 6 Completion

New source changes:
- auth-service `/health` now reports database and Redis dependency truthfully instead of always claiming `ok`
- data-service `/health` now reports Redis queue/outbox degradation, Influx unavailability, and MQTT disconnect state explicitly instead of silently surfacing a healthy contract
- auth user-plant access writes now lock the target user row before replace-insert and normalize duplicate plant IDs at the repository boundary

New test files added:
- `services/auth-service/tests/test_phase6_runtime_truthfulness.py`
- `services/data-service/tests/test_phase6_runtime_truthfulness.py`

Existing suites strengthened in this phase:
- `services/data-service/tests/test_circuit_breaker.py`

Validation run:
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_phase6_runtime_truthfulness.py tests/test_token_cleanup_service.py tests/test_tenant_identity_hard_cut.py`
  - result: `13 passed`
- `repo-root: PYTHONPATH=/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main:/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services JWT_SECRET_KEY=test-secret REDIS_URL=redis://localhost:6379/0 ./.venv-phase1/bin/pytest services/data-service/tests/test_phase6_runtime_truthfulness.py services/data-service/tests/test_mqtt_handler.py services/data-service/tests/test_circuit_breaker.py`
  - result: `19 passed`
- attempted but environment-blocked:
  - `services/analytics-service: ../../.venv-phase1/bin/pytest tests/unit/test_worker_heartbeat.py tests/unit/test_worker_restart_cleanup.py`
    - blocked by missing local dependency: `sklearn`
  - `services/data-service: ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
    - blocked by unavailable local MySQL test host `mysql`

Phase 6 items moved toward Covered:
- `Runtime Stability And Recovery`
  - DB outage runtime truthfulness -> Covered
  - Redis outage runtime truthfulness -> Covered
  - Influx outage runtime truthfulness -> Covered
  - EMQX outage runtime truthfulness -> Covered
  - dependency health/runtime truthfulness contract -> Covered
- `Database Integrity And Concurrency`
  - concurrent access-mapping mutation integrity -> Covered
  - rollback cleanup on failed outbox batch writes -> Covered

### Residual Closure Sweep

New source changes:
- analytics job-runner now loads optional ML runtime dependencies lazily so worker heartbeat, restart cleanup, and non-ML runtime contracts can boot and validate without forcing `sklearn` at module import time

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/rule-engine-service/tests/test_phase5_rules_notifications.py`
- `services/data-service/tests/test_backpressure.py`
- `services/data-service/tests/test_circuit_breaker.py`
- `services/analytics-service/tests/unit/test_job_runner.py`

Validation run:
- `services/analytics-service: ../../.venv-phase1/bin/pytest tests/unit/test_job_runner.py tests/unit/test_worker_heartbeat.py tests/unit/test_worker_restart_cleanup.py`
  - result: `18 passed`
- `repo-root: PYTHONPATH=/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main:/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services JWT_SECRET_KEY=test-secret REDIS_URL=redis://localhost:6379/0 ./.venv-phase1/bin/pytest services/data-service/tests/test_phase6_runtime_truthfulness.py services/data-service/tests/test_backpressure.py services/data-service/tests/test_mqtt_handler.py services/data-service/tests/test_circuit_breaker.py`
  - result: `26 passed`
- `services/rule-engine-service: INTERNAL_SERVICE_SHARED_SECRET=test-internal-secret ../../.venv-phase1/bin/pytest tests/test_phase5_rules_notifications.py tests/test_rule_plant_scope.py`
  - result: `25 passed`

Still environment-blocked after this sweep:
- `services/data-service: ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
  - blocked by unavailable local MySQL host `mysql`

Residual sweep items moved toward Covered:
- `Runtime Stability And Recovery`
  - analytics worker runtime recovery validation parity -> Covered
- `Database Integrity And Concurrency`
  - concurrent rule mutation integrity -> Covered

### Next Broad CI Closure Sweep

New source changes:
- device-service health-config creation now verifies the referenced device inside the requested tenant scope before accepting configuration writes
- device-service shift creation now verifies the referenced device inside the requested tenant scope before accepting shift writes

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/auth-service/tests/test_org_user_scope.py`
- `services/auth-service/tests/test_org_plant_lifecycle.py`
- `services/device-service/tests/test_phase3_machine_api_validation.py`
- `services/device-service/tests/test_phase3_shift_dashboard_calendar.py`
- `services/device-service/tests/test_health_config_uniqueness.py`
- `services/device-service/tests/test_maintenance_log_api.py`
- `services/copilot-service/tests/test_chat_provider_optional.py`
- `services/rule-engine-service/tests/test_admin_notification_usage_api.py`

Validation run:
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_org_user_scope.py tests/test_org_plant_lifecycle.py`
  - result: `44 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_phase3_machine_api_validation.py tests/test_phase3_shift_dashboard_calendar.py tests/test_health_config_uniqueness.py tests/test_maintenance_log_api.py`
  - result: `22 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_dashboard_health_scope.py tests/test_health_trend_parameter_resolution.py tests/test_live_dashboard_summary.py`
  - result: `40 passed`
- `services/copilot-service: ../../.venv-phase1/bin/pytest tests/test_phase4_chat_gate.py tests/test_feature_gate_api.py tests/test_chat_provider_optional.py`
  - result: `9 passed`
- `services/rule-engine-service: ../../.venv-phase1/bin/pytest tests/test_admin_notification_usage_api.py`
  - result: `12 passed`
- `services/rule-engine-service: INTERNAL_SERVICE_SHARED_SECRET=test-internal-secret ../../.venv-phase1/bin/pytest tests/test_phase5_rules_notifications.py tests/test_rule_plant_scope.py tests/test_notification_audit_ledger.py tests/test_admin_notification_usage_api.py`
  - result: `57 passed`
- `services/analytics-service: ../../.venv-phase1/bin/pytest tests/integration/test_feature_gate_api.py`
  - result: `2 passed`
- `services/reporting-service: ../../.venv-phase1/bin/pytest tests/test_feature_gate_api.py`
  - result: `1 passed`
- `services/waste-analysis-service: ../../.venv-phase1/bin/pytest tests/test_feature_gate_api.py`
  - result: `1 passed`

Still environment-blocked after this sweep:
- `services/data-service: ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
  - blocked by unavailable local MySQL host `mysql`

Sweep items moved toward Covered:
- `Org Plant And User Management`
  - plant delete guard clean-path truthfulness -> Covered
- `Premium Feature Gating`
  - invalid premium-feature grant rejection contract -> Covered
  - admin entitlement payload-shape enforcement -> Covered
- `Parameter Config And Health Score`
  - non-finite parameter range validation -> Covered
  - cross-tenant health-config device reference rejection -> Covered
- `Shift And Uptime`
  - cross-tenant shift device reference rejection -> Covered
- `Maintenance Records`
  - maintenance-date move that invalidates next_due_date -> Covered
- `Factory Copilot`
  - missing auth-context rejection proof -> Covered
  - tenant-scope-required rejection proof -> Covered
- `Notification Usage And Delivery Accounting`
  - date-range and search filter truthfulness -> Covered
  - include-metadata opt-in contract -> Covered

### Follow-up Broad CI Closure Sweep

New source changes:
- no product-source changes were required in this sweep
- reporting-service isolated test bootstrap now uses repo-relative local service paths instead of stale external-repo absolute paths

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/device-service/tests/test_device_onboarding_phase2.py`
- `services/device-service/tests/test_device_mqtt_credentials.py`
- `services/reporting-service/tests/test_report_history_scope.py`
- `services/reporting-service/tests/test_scheduler_reliability.py`

Validation run:
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_device_onboarding_phase2.py tests/test_device_mqtt_credentials.py`
  - result: `18 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_device_id_generation.py`
  - result: `12 passed`
- `services/reporting-service: ../../.venv-phase1/bin/pytest tests/test_report_history_scope.py tests/test_scheduler_reliability.py tests/test_phase4_truthfulness_scope.py tests/test_long_running_job_contract.py tests/test_feature_gate_api.py`
  - result: `20 passed`

Still environment-blocked after this sweep:
- `services/data-service: ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
  - blocked by unavailable local MySQL host `mysql`

Sweep items moved toward Covered:
- `Device Onboarding And Provisioning`
  - viewer onboarding role restriction proof -> Covered
  - plant-scoped onboarding foreign-plant denial proof -> Covered
- `MQTT Auth And Telemetry Ingestion`
  - missing credential status/revoke/rotate denial contract -> Covered
  - revoke-then-rotate lifecycle reactivation truthfulness -> Covered
- `Reports And Scheduled Reports`
  - scheduled-report update tenant-scope no-op proof -> Covered

### Next Broad CI Closure Sweep

New source changes:
- no product-source changes were required in this sweep

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/auth-service/tests/test_org_user_scope.py`
- `services/device-service/tests/test_maintenance_log_api.py`

Validation run:
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_org_user_scope.py tests/test_org_plant_lifecycle.py`
  - result: `49 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_maintenance_log_api.py tests/test_phase3_machine_api_validation.py`
  - result: `12 passed`

Still environment-blocked after this sweep:
- `services/data-service: ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
  - blocked by unavailable local MySQL host `mysql`

Sweep items moved toward Covered:
- `Org Plant And User Management`
  - org-admin plant-access restriction proof for existing org_admin users -> Covered
  - cross-tenant deactivate user denial proof -> Covered
  - cross-tenant reactivate user denial proof -> Covered
- `Premium Feature Gating`
  - invalid role-key rejection in entitlement delegation payload -> Covered
  - operator premium-feature assignment denial proof -> Covered
- `Maintenance Records`
  - missing maintenance-record delete truthfulness -> Covered

### Follow-up Device And Machine Sweep

New source changes:
- no product-source changes were required in this sweep
- device-service plant lifecycle guard tests now set a local internal-service shared secret so tenant-scoped internal route validation runs cleanly in this workspace

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/device-service/tests/test_plant_lifecycle_guards.py`
- `services/device-service/tests/test_phase3_machine_api_validation.py`
- `services/device-service/tests/test_phase3_shift_dashboard_calendar.py`

Validation run:
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_plant_lifecycle_guards.py tests/test_phase3_machine_api_validation.py tests/test_phase3_shift_dashboard_calendar.py`
  - result: `19 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_device_onboarding_phase2.py tests/test_maintenance_log_api.py tests/test_health_config_uniqueness.py`
  - result: `16 passed`

Still environment-blocked after this sweep:
- `services/data-service: ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
  - blocked by unavailable local MySQL host `mysql`

Sweep items moved toward Covered:
- `Device Onboarding And Provisioning`
  - inactive-plant rejection on one-time onboarding bundle flow -> Covered
- `Parameter Config And Health Score`
  - health-config missing-record 404 truthfulness -> Covered
- `Shift And Uptime`
  - missing-shift delete truthfulness -> Covered
  - no-active-shifts uptime truthfulness -> Covered

### Follow-up Auth And Plant Lifecycle Sweep

New source changes:
- no product-source changes were required in this sweep

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/auth-service/tests/test_org_plant_lifecycle.py`

Validation run:
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_org_plant_lifecycle.py tests/test_org_user_scope.py tests/test_token_version_revocation.py`
  - result: `73 passed`

Still environment-blocked after this sweep:
- `services/data-service: ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
  - blocked by unavailable local MySQL host `mysql`

Sweep items moved toward Covered:
- `Org Plant And User Management`
  - plant deactivate already-inactive truthfulness -> Covered
  - plant reactivate already-active truthfulness -> Covered
- `Org Suspension And Access Enforcement`
  - suspended-org resend-invite denial truthfulness -> Covered

### Follow-up Dashboard And Outbox Sweep

New source changes:
- dashboard summary truthfulness now reports `stopped_devices` from the actual stopped-status aggregate instead of deriving it as `total - running`
- outbox batch enqueue now uses a portable non-MySQL fallback that assigns surrogate ids explicitly for local SQLite-backed validation while preserving the MySQL bulk insert path

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/device-service/tests/test_live_dashboard_summary.py`
- `services/device-service/tests/test_snapshot_storage.py`
- `services/data-service/tests/test_phase6_runtime_truthfulness.py`

Validation run:
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_live_dashboard_summary.py tests/test_snapshot_storage.py tests/test_dashboard_bootstrap_latency_guard.py tests/test_load_state_consistency.py`
  - result: `33 passed`
- `services/data-service: ../../.venv-phase1/bin/pytest tests/test_phase6_runtime_truthfulness.py`
  - result: `4 passed`

Still environment-blocked after this sweep:
- `services/data-service: ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
  - blocked by unavailable local MySQL test host and missing local MySQL server binaries (`mysql`, `mysqld`, `mariadbd`)

Sweep items moved toward Covered:
- `Machine Dashboard Calculations`
  - unknown-state devices no longer misreported as stopped in dashboard summaries -> Covered
- `Database Integrity And Concurrency`
  - portable outbox batch persistence proof for non-MySQL validation backends -> Covered

### Follow-up MySQL Outbox Validation Sweep

New source changes:
- no product-source changes were required in this sweep
- MySQL outbox integrity tests now use the shared data-service bootstrap path and reset outbox repository singletons / circuit-breaker state between cases so the full MySQL-backed suite runs cleanly in this workspace

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/data-service/tests/test_outbox_integrity.py`

Validation run:
- `services/data-service: MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_DATABASE=ai_factoryops MYSQL_USER=energy MYSQL_PASSWORD=energy ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py`
  - result: `11 passed`
- `services/data-service: MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_DATABASE=ai_factoryops MYSQL_USER=energy MYSQL_PASSWORD=energy ../../.venv-phase1/bin/pytest tests/test_outbox_integrity.py tests/test_phase6_runtime_truthfulness.py`
  - result: `15 passed`

Still environment-blocked after this sweep:
- none for the previously-missing MySQL outbox validation path

Sweep items moved toward Covered:
- `Database Integrity And Concurrency`
  - MySQL-backed outbox integrity validation -> Covered

### Follow-up Machine Tail Truthfulness Sweep

New source changes:
- no product-source changes were required in this sweep
- machine-tail residual closure came from direct API and summary proof strengthening for empty dashboard state, missing shift routes, and zero-record maintenance summaries

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/device-service/tests/test_live_dashboard_summary.py`
- `services/device-service/tests/test_phase3_machine_api_validation.py`
- `services/device-service/tests/test_maintenance_log_api.py`

Validation run:
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_live_dashboard_summary.py tests/test_phase3_machine_api_validation.py tests/test_maintenance_log_api.py tests/test_phase3_shift_dashboard_calendar.py tests/test_snapshot_storage.py tests/test_dashboard_bootstrap_latency_guard.py tests/test_health_config_uniqueness.py`
  - result: `56 passed`

Still environment-blocked after this sweep:
- none for the machine-tail suites touched in this pass

Sweep items moved toward Covered:
- `Machine Dashboard Calculations`
  - empty-tenant dashboard summary returns truthful zero-state metrics -> Covered
- `Shift And Uptime`
  - missing shift GET/PUT/DELETE routes return truthful `SHIFT_NOT_FOUND` contract -> Covered
- `Maintenance Records`
  - zero-record maintenance summary returns truthful zero-state contract -> Covered

### Follow-up Auth MQTT And Analytics Sweep

New source changes:
- suspended-org write protection now applies to tenant user update routes as well, closing a real mutation-path gap that was still bypassing the org suspension contract
- the rest of this sweep came from direct proof strengthening rather than new product behavior

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/auth-service/tests/test_org_plant_lifecycle.py`
- `services/device-service/tests/test_device_mqtt_credentials.py`
- `services/analytics-service/tests/integration/test_phase4_truthfulness_scope.py`

Validation run:
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_org_plant_lifecycle.py tests/test_org_user_scope.py tests/test_invite_and_reset_lifecycle.py`
  - result: `61 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_device_mqtt_credentials.py tests/test_device_onboarding_phase2.py tests/test_live_projection_service.py`
  - result: `50 passed`
- `services/analytics-service: ../../.venv-phase1/bin/pytest tests/integration/test_phase4_truthfulness_scope.py tests/integration/test_feature_gate_api.py tests/unit/test_job_status_route_payload.py tests/unit/test_result_scope.py`
  - result: `17 passed`

Still environment-blocked after this sweep:
- none for the suites touched in this pass

Sweep items moved toward Covered:
- `Org Suspension And Access Enforcement`
  - suspended tenants can no longer update existing users through the tenant user mutation route -> Covered
- `MQTT Auth And Telemetry Ingestion`
  - direct MQTT credential status/revoke/rotate routes now have explicit cross-tenant denial proof and revoked-status truthfulness proof -> Covered
- `Analytics Job Flow`
  - formatted-results failed-job path now has direct truthful `409` proof -> Covered
  - formatted-results completed-without-payload path now has direct truthful `404` proof -> Covered

### Follow-up Org Suspension And Analytics Status Sweep

New source changes:
- suspended-org write protection now also covers user deactivate/reactivate and plant deactivate/reactivate mutation routes, closing the remaining tenant mutation bypasses in this area
- analytics status-route proof was strengthened without additional product changes

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/auth-service/tests/test_org_plant_lifecycle.py`
- `services/auth-service/tests/test_org_user_scope.py`
- `services/analytics-service/tests/integration/test_phase4_truthfulness_scope.py`

Validation run:
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_org_plant_lifecycle.py tests/test_org_user_scope.py tests/test_invite_and_reset_lifecycle.py`
  - result: `65 passed`
- `services/analytics-service: ../../.venv-phase1/bin/pytest tests/integration/test_phase4_truthfulness_scope.py tests/integration/test_feature_gate_api.py tests/unit/test_job_status_route_payload.py tests/unit/test_result_scope.py`
  - result: `18 passed`

Still environment-blocked after this sweep:
- none for the suites touched in this pass

Sweep items moved toward Covered:
- `Org Suspension And Access Enforcement`
  - suspended tenants can no longer deactivate/reactivate users or deactivate/reactivate plants through tenant mutation routes -> Covered
- `Analytics Job Flow`
  - queued in-memory job fallback on `/status/{job_id}` now has direct truthful pending-contract proof -> Covered

### Follow-up Copilot And Rules Sweep

New source changes:
- no product-source changes were required in this sweep
- copilot closure came from direct API fallback-contract proof
- rules closure came from delete-path proof strengthening and a local rule-suite bootstrap fix so the plant-scope tests run against this repo instead of a stale external checkout path

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/copilot-service/tests/test_chat_provider_optional.py`
- `services/rule-engine-service/tests/test_phase5_rules_notifications.py`
- `services/rule-engine-service/tests/test_rule_plant_scope.py`

Validation run:
- `services/copilot-service: ../../.venv-phase1/bin/pytest tests/test_chat_provider_optional.py tests/test_feature_gate_api.py`
  - result: `9 passed`
- `services/rule-engine-service: INTERNAL_SERVICE_SHARED_SECRET=test-internal-secret ../../.venv-phase1/bin/pytest tests/test_phase5_rules_notifications.py tests/test_rule_plant_scope.py tests/test_notification_audit_ledger.py`
  - result: `48 passed`

Still environment-blocked after this sweep:
- none for the suites touched in this pass

Sweep items moved toward Covered:
- `Factory Copilot`
  - direct `AI_UNAVAILABLE` chat fallback contract -> Covered
  - direct `INTERNAL_ERROR` chat fallback contract -> Covered
- `Rules And Notifications`
  - soft delete archives rules truthfully and hides them from active listings -> Covered
  - hard delete removes rule rows completely -> Covered
  - out-of-scope delete stays hidden and non-mutating -> Covered

### Follow-up Auth And Device Runtime Cluster Sweep

New source changes:
- no product-source changes were required in this sweep
- auth closure came from direct identity-contract proof strengthening plus fixture alignment for the now-enforced active-org mutation guard
- runtime closure came from direct live-projection truthfulness proof strengthening and a device route stub refresh to match the current live-update signature

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/auth-service/tests/test_invite_and_reset_lifecycle.py`
- `services/auth-service/tests/test_token_version_revocation.py`
- `services/device-service/tests/test_live_projection_service.py`
- `services/device-service/tests/test_live_update_unknown_device.py`

Validation run:
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_invite_and_reset_lifecycle.py tests/test_token_version_revocation.py tests/test_auth_cookie_security.py`
  - result: `43 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_live_projection_service.py tests/test_load_state_consistency.py tests/test_live_update_unknown_device.py tests/test_startup_reconcile.py`
  - result: `43 passed`

Still environment-blocked after this sweep:
- none for the auth and device-runtime suites touched in this pass

Sweep items moved toward Covered:
- `Auth And Identity`
  - login rejects users with pending invite password setup before password verification -> Covered
  - login rejects disabled users before password verification -> Covered
  - login rejects unknown email with generic invalid-credentials contract -> Covered
  - password reset rejects mismatched confirmation without consuming the action token -> Covered
  - password reset rejects consumed-valid token context when the target user no longer exists -> Covered
- `Machine Runtime States`
  - reconcile path leaves runtime truth unchanged when no latest telemetry sample exists -> Covered
  - snapshot lookup returns truthful unknown-device error contract -> Covered

### Follow-up Org And Onboarding Cluster Sweep

New source changes:
- no product-source changes were required in this sweep
- closure came from direct org-user and onboarding route-contract proof strengthening only

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/auth-service/tests/test_org_user_scope.py`
- `services/device-service/tests/test_device_onboarding_phase2.py`

Validation run:
- `services/auth-service: ../../.venv-phase1/bin/pytest tests/test_org_user_scope.py tests/test_org_plant_lifecycle.py tests/test_invite_and_reset_lifecycle.py tests/test_token_version_revocation.py`
  - result: `94 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_device_onboarding_phase2.py tests/test_plant_lifecycle_guards.py tests/test_device_id_generation.py tests/test_device_mqtt_credentials.py`
  - result: `42 passed`

Still environment-blocked after this sweep:
- none for the org and onboarding suites touched in this pass

Sweep items moved toward Covered:
- `Org Plant And User Management`
  - reactivate route rejects already-active users with truthful `USER_ALREADY_ACTIVE` contract -> Covered
  - resend-invite route rejects active users with truthful `INVITE_NOT_PENDING` contract -> Covered
  - update-user route rejects inactive plant assignment before mutating access -> Covered
- `Device Onboarding And Provisioning`
  - plant-scoped roles can complete assigned-plant onboarding bundle flow -> Covered
  - onboard route rejects blank/missing plant payloads at the public contract boundary -> Covered
  - onboard route maps duplicate device conflicts to truthful `409 DEVICE_ALREADY_EXISTS` -> Covered
  - onboard route maps device ID allocation failures to truthful `503 DEVICE_ID_ALLOCATION_FAILED` -> Covered
  - assigned-plant onboarding success is directly proven for both `plant_manager` and `operator` roles -> Covered

### Final Machine Business-Logic Cluster Sweep

New source changes:
- no product-source changes were required in this sweep
- closure came from strengthening the remaining dashboard, health-config, shift, and calendar truthfulness branches already implemented in product code

New test files added:
- none

Existing suites strengthened in this sweep:
- `services/device-service/tests/test_device_loss_stats.py`
- `services/device-service/tests/test_live_dashboard_summary.py`
- `services/device-service/tests/test_health_config_uniqueness.py`
- `services/device-service/tests/test_phase3_shift_dashboard_calendar.py`
- `services/device-service/tests/test_snapshot_storage.py`

Validation run:
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_device_loss_stats.py tests/test_live_dashboard_summary.py tests/test_health_config_uniqueness.py tests/test_phase3_shift_dashboard_calendar.py tests/test_snapshot_storage.py tests/test_phase3_machine_api_validation.py`
  - result: `60 passed`
- `services/device-service: ../../.venv-phase1/bin/pytest tests/test_live_dashboard_summary.py tests/test_device_loss_stats.py tests/test_snapshot_storage.py tests/test_phase3_machine_api_validation.py tests/test_phase3_shift_dashboard_calendar.py tests/test_health_config_uniqueness.py tests/test_dashboard_health_scope.py tests/test_health_trend_parameter_resolution.py tests/test_dashboard_bootstrap_latency_guard.py tests/test_maintenance_log_api.py`
  - result: `96 passed`

Still environment-blocked after this sweep:
- none for the machine/business-logic suites touched in this pass

Sweep items moved toward Covered:
- `Machine Dashboard Calculations`
  - loss statistics ignore previous-day live-state leakage -> Covered
  - plant-scoped live dashboard summary truthfully uses scoped month totals without unnecessary month refresh -> Covered
  - empty-fleet dashboard materialization returns zeroed truthfulness contract -> Covered
- `Parameter Config And Health Score`
  - active weight validation accepts canonical `100%` sums while ignoring inactive configs -> Covered
  - bulk update rejects duplicate canonical parameters in one payload before mutation -> Covered
- `Shift And Uptime`
  - current-window uptime returns truthful no-active-window contract when configured shifts exist but none is active -> Covered
- `Calendar And Consumption Consistency`
  - monthly energy route returns truthful unavailable zero-payload contract when both snapshot and refresh are unavailable -> Covered

## Next Working Step

Next execution step:
- maintain the now-fully-covered matrix as product logic evolves
- require CI.md updates whenever new business logic changes alter the checklist surface
- keep the suite map aligned with GitHub Actions so regressions stay readable

Success definition for the full effort:
- every suite stays covered
- every checklist item becomes `Covered`
- GitHub Actions exposes readable suite names
- `Dev-Testing` becomes the release-grade validation branch

Workflow implementation now aligned to this contract:
- workflow: `.github/workflows/validation.yml`
- broad CI suite runner: `scripts/ci_broad_validation.py`
- local mirror: `scripts/run-broad-ci-validation.sh`
