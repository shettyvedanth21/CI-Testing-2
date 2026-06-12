# Rule Engine Service

FactoryOPS real-time rule evaluation service.

Supports threshold rules and time-based rules, per-rule/per-device cooldown, no-repeat behavior, alert history, and activity events.

## Base URL
- `http://<host>:8002` (compose default)
- API prefix: `/api/v1`

## Health Endpoints
- `GET /health`
- `GET /ready`

## Rule APIs (`/api/v1/rules`)
- `GET /api/v1/rules`
- `GET /api/v1/rules/{rule_id}`
- `POST /api/v1/rules`
- `PUT /api/v1/rules/{rule_id}`
- `PATCH /api/v1/rules/{rule_id}/status`
- `DELETE /api/v1/rules/{rule_id}`
- `POST /api/v1/rules/evaluate`

## Alert / Activity APIs (`/api/v1/alerts`)
- `GET /api/v1/alerts`
- `PATCH /api/v1/alerts/{alert_id}/acknowledge`
- `PATCH /api/v1/alerts/{alert_id}/resolve`
- `GET /api/v1/alerts/events`
- `GET /api/v1/alerts/events/unread-count`
- `PATCH /api/v1/alerts/events/mark-all-read`
- `DELETE /api/v1/alerts/events`
- `GET /api/v1/alerts/events/summary`

## Rule Types
Schema source: `app/schemas/rule.py`

### 1) Threshold Rule
- `rule_type = threshold`
- required fields: `property`, `condition`, `threshold`

### 2) Time-Based Rule
- `rule_type = time_based`
- required fields: `time_window_start`, `time_window_end`
- `time_condition` default: `running_in_window`
- timezone default: `Asia/Kolkata`

## Cooldown Modes
- `interval` + `cooldown_minutes` (0..1440)
- `no_repeat` (fires once until reset)

No-repeat reset behavior:
- reset when rule transitions `paused -> active`
- reset when rule definition is edited in a way that changes trigger semantics

## Time-Based Running Signal Logic
Code: `app/services/evaluator.py`

Priority:
1. if `power` or `active_power` exists -> running when value `> 0`
2. else if `current` exists:
   - if `voltage` exists -> running when `current>0 && voltage>0`
   - else running when `current>0`
3. otherwise not running

Window evaluation:
- telemetry timestamp converted to IST (`Asia/Kolkata`)
- supports same-day and overnight windows (e.g., `20:00 -> 06:00`)

## Cooldown Enforcement Semantics
- Cooldown is evaluated per rule + per device context.
- One device triggering does not suppress alerts for a different device unless scope/cooldown conditions independently match.

## Notification Delivery
- Email recipients are fetched dynamically from settings (`notification_channels`) via reporting-service integration.
- No hardcoded alert-recipient target is used in send path.
- If no active recipients exist: evaluation continues, warning is logged, service does not crash.

## Error Handling
- API returns structured JSON responses.
- Validation and runtime errors are normalized to service error envelope.
