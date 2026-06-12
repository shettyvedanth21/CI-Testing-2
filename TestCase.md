# Test Case Plan

This document lists the high-signal tests needed to prevent energy, loss, cost, dashboard, calendar, and report drift from reaching production.

The goal is not to add a huge test suite. The goal is to make the bug classes we already saw fail in CI before they reach a server.

## Important Rule

`today_energy_kwh == today_loss_kwh` is not a global rule.

It is expected only in a full-waste scenario, for example:

- one active device
- all current-day energy is classified as off-hours, idle, or overconsumption
- no productive/normal-load interval exists

For mixed or productive operation:

- `today_loss_kwh <= today_energy_kwh`
- the same energy truth must still match across dashboard, calendar, energy report, and waste report
- the same loss truth must still match across dashboard, waste report, and canonical loss endpoints where exposed

## CI Layers Needed

### 1. Backend Unit And Regression Tests

Purpose: protect core calculation rules and low-level service behavior.

Required tests:

- `test_overconsumption_full_interval_counts_as_loss`
- `test_normal_running_inside_shift_energy_not_loss`
- `test_offhours_energy_counts_as_full_loss`
- `test_idle_energy_counts_as_full_loss`
- `test_mixed_operation_loss_never_exceeds_energy`
- `test_zero_loss_canonical_overrides_stale_live_loss`
- `test_current_day_persisted_plus_live_delta_never_decreases_truth`

Expected invariants:

- full-waste interval: `energy_kwh == loss_kwh`
- normal productive interval: `loss_kwh == 0`, `energy_kwh > 0`
- mixed day: `0 <= loss_kwh <= energy_kwh`
- canonical zero must override stale non-zero live values

Recommended location:

- `tests/test_energy_accounting.py`
- `tests/test_energy_service_cost_alignment.py`
- `services/device-service/tests/test_live_dashboard_summary.py`
- `services/energy-service/tests/test_device_range_live_overlay.py`

## 2. Current-Day Truth Sync Tests

Purpose: ensure repaired live truth is also written into canonical persisted truth.

Required tests:

- `test_recompute_today_loss_syncs_energy_device_day`
- `test_projection_reconciler_syncs_repaired_devices_to_energy_service`
- `test_energy_device_day_sync_rebuilds_device_month_and_fleet_day`
- `test_energy_device_day_sync_preserves_canonical_zero_loss`
- `test_energy_device_day_sync_skips_missing_live_rows_without_crashing`

Expected invariants:

- after live recompute, `DeviceLiveState.today_energy_kwh == EnergyDeviceDay.energy_kwh`
- after live recompute, `DeviceLiveState.today_loss_kwh == EnergyDeviceDay.loss_kwh`
- fleet day totals equal the sum of repaired device day rows
- sync failure must not break dashboard reads

Recommended location:

- `services/device-service/tests/test_device_loss_stats.py`
- `services/device-service/tests/test_startup_reconcile.py`
- `services/energy-service/tests/test_reconciliation_apply_service.py`

## 3. API Parity Tests

Purpose: catch endpoint-level drift that unit tests can miss.

Required API checks:

- `GET /api/v1/devices/dashboard/summary`
- `GET /api/v1/devices/dashboard/today-loss-breakdown`
- `GET /api/v1/devices/calendar/monthly-energy`
- `GET /api/v1/energy/device/{device_id}/range`
- energy report generation result
- waste report generation result

Full-waste single-device scenario:

- dashboard summary `today_energy_kwh == today_loss_kwh`
- today loss breakdown `today_energy_kwh == total_loss_kwh`
- calendar today `energy_kwh == loss_kwh`
- energy-service range today `energy_kwh == loss_kwh`
- waste report `total_energy_kwh == total_loss_kwh`
- energy report `total_kwh == energy-service range energy_kwh`

Mixed-operation scenario:

- dashboard summary energy equals calendar today energy within rounding tolerance
- dashboard summary loss equals today loss breakdown total within rounding tolerance
- energy-service range energy equals calendar today energy within rounding tolerance
- loss is never greater than energy

Tolerance:

- same API snapshot: exact after rounding to 4 decimals
- independently generated reports: tolerate at most `max(0.01 kWh, 0.1%)` unless a shared snapshot mechanism is implemented

Recommended location:

- `tests/api/test_current_day_truth_parity.py` (created in Phase 2 — pure-function level)
- `tests/api/__init__.py` (created in Phase 2)
- future: upgrade to real service client integration tests

## 4. Report Artifact Tests

Purpose: distinguish old generated report artifacts from newly generated truth.

Required tests:

- `test_new_energy_report_uses_current_canonical_day_truth`
- `test_new_waste_report_uses_current_canonical_energy_and_loss_truth`
- `test_old_report_artifact_is_not_treated_as_live_truth`
- `test_energy_and_waste_reports_match_for_same_single_device_full_waste_day`
- `test_report_all_devices_scope_is_not_compared_to_single_device_dashboard`

Expected invariants:

- a newly generated report reads current canonical truth
- an old report remains historical and does not automatically update
- `ALL` devices report must not be compared against a single-device dashboard value
- selected device report must match the selected device dashboard/canonical lane

Recommended location:

- `services/reporting-service/tests/test_report_task_tariff_warning.py`
- `services/waste-analysis-service/tests/test_waste_historical_loss_parity.py`
- new cross-service API test file if report tasks are tested through API/job boundaries

## 5. CI Smoke Gates

Purpose: keep CI fast but meaningful.

Required CI gates:

- core accounting tests
- energy-service current-day overlay tests
- device-service dashboard summary tests
- waste canonical loss parity tests
- one API parity smoke test for full-waste single-device scenario

Minimum command set:

```bash
PYTHONPATH=. python -m pytest tests/test_energy_accounting.py -q
PYTHONPATH=. python -m pytest tests/test_energy_service_cost_alignment.py services/energy-service/tests/test_device_range_live_overlay.py -q
PYTHONPATH=. JWT_SECRET_KEY=test-secret python -m pytest services/device-service/tests/test_live_dashboard_summary.py services/device-service/tests/test_device_loss_stats.py -q
PYTHONPATH=. python -m pytest services/waste-analysis-service/tests/test_waste_historical_loss_parity.py -q
PYTHONPATH=. python -m pytest tests/api/test_current_day_truth_parity.py -q
```

API parity command (now exists):

```bash
PYTHONPATH=. python -m pytest tests/api/test_current_day_truth_parity.py -q
```

## 6. Completed And Missing CI/API Gates

### Completed (Phase 1B — Local-Invariant Hardening)

6 tests added to existing files, validated, zero regressions:

- `services/device-service/tests/test_live_dashboard_summary.py` — 2 tests: dashboard loss ≤ energy, dashboard=calendar parity
- `services/device-service/tests/test_device_loss_stats.py` — 1 test: scope-guard for canonical loss override
- `services/energy-service/tests/test_device_range_live_overlay.py` — 2 tests: calendar loss fields, loss ≤ energy invariant
- `tests/test_energy_service_cost_alignment.py` — 1 test: today loss ≤ energy at summary API

### Completed (Phase 2 — Cross-Service Current-Day Truth Parity)

6 pure-function-level tests proving canonical energy truth agreement between energy report and waste report paths:

- `tests/api/__init__.py` — empty package init
- `tests/api/test_current_day_truth_parity.py` — 6 tests:
  1. `test_energy_report_total_kwh_matches_canonical_when_accepted`
  2. `test_waste_report_total_energy_kwh_matches_canonical_when_financial_accepted`
  3. `test_waste_report_total_loss_kwh_le_total_energy_kwh_under_canonical`
  4. `test_waste_report_full_waste_scenario_loss_equals_energy_under_canonical`
  5. `test_both_reports_reflect_same_canonical_energy_truth_when_accepted`
  6. `test_canonical_rejection_is_explicit_for_placeholder_zero_no_false_parity`

These use sequential `importlib.util` loading (report_task then waste_task) with `sys.modules` cleanup to avoid `src.*` namespace conflicts. No HTTP, no database, no async task-runner orchestration.

### Completed (Phase 3 — CI Enforcement + API-Level Parity Assertions)

3 CI/infrastructure changes + 5 API-level parity assertions, validated, zero regressions:

- `scripts/run-truth-parity-gate.sh` — NEW: 6-step sequential parity gate script with per-invocation `env PYTHONPATH=...` to avoid `app.*/src.*` namespace conflicts (108 tests across 6 invocations)
- `scripts/lib/test_layers.sh` — Added `TRUTH_PARITY_GATE_TARGETS` array (documentation-oriented; actual invocation is in the gate script)
- `Jenkinsfile` — Added `Truth Parity Gate` stage after `Fast Checks`, before `Smoke E2E`; calls `./scripts/run-truth-parity-gate.sh`
- `tests/e2e/test_19_energy_dashboard_regression.py` — 5 parity assertions added to `test_dashboard_summary_calendar_and_loss_breakdown_show_energy`:
  1. `summary_widgets.today_energy_kwh == breakdown_totals.today_energy_kwh` (abs=0.01)
  2. `summary_widgets.today_loss_kwh == breakdown_totals.total_loss_kwh` (abs=0.01)
  3. `summary_widgets.today_loss_kwh <= summary_widgets.today_energy_kwh + 0.01`
  4. `breakdown_totals.total_loss_kwh <= breakdown_totals.today_energy_kwh + 0.01`
  5. `calendar_today.energy_kwh == summary_widgets.today_energy_kwh` (abs=0.5), with `calendar_today.loss_kwh <= calendar_today.energy_kwh + 0.01` guard

### Still Missing — P0: Must Fail CI

These tests protect money-facing truth and should run on every pull request.

- Single-device full-waste scenario at API level with live server: one running device fully classified as loss must satisfy `today_energy_kwh == today_loss_kwh` across dashboard, calendar, energy-service range, waste report, and energy report (Phase 2 covers pure-function level; Phase 3 adds API-level e2e assertions; full live-server integration test remains open).
- Mixed-operation scenario at API level with live server: a device with productive and waste intervals must satisfy `today_loss_kwh <= today_energy_kwh`, and all surfaces must agree on each metric (Phase 1B + Phase 2 + Phase 3 cover invariants at unit/pure-function/e2e-collection level; live-server integration test remains open).
- Tenant, plant, and selected-device scope parity: tenant-wide, plant-scoped, and selected-device requests must not be compared against each other accidentally.
- Cost truth parity: `energy_cost_inr`, `loss_cost_inr`, energy reports, waste reports, dashboard cards, and calendar cells must use the same tariff-resolved value for the same scope/date.

### Still Missing — P1: Should Fail CI

These tests prevent stale or misleading values from reaching users.

- Report artifact freshness: a newly generated report must use current canonical truth, while old report artifacts remain historical and are never treated as live truth.
- Current-day recompute/sync: after live recompute or startup reconciliation, `DeviceLiveState`, `EnergyDeviceDay`, fleet day, and month aggregates must converge.
- API boundary parity with live server: test the real HTTP API responses end-to-end, not only internal helper functions or collection-only e2e tests.
- Scope mismatch guard: single-device dashboard values must not be compared to `ALL` devices report values.
- Historical-day stability: past-day reports must not use live overlay and must remain deterministic.
- Failure fallback semantics: if a canonical dependency is unavailable, the response must degrade visibly and safely, not silently return stale money values.

### Still Missing — P2: Platform Confidence

These are important for production scale and release confidence, but they can be scheduled after the P0/P1 truth gates.

- Latency smoke for 100+ devices: dashboard summary, calendar, canonical range, energy report, and waste report should stay within agreed p95 targets.
- Browser smoke for dashboard widgets: verify UI displays the same values returned by the APIs and does not render stale cached data.
- PDF/report display assertions: verify report PDFs display the same canonical totals as the report payload.
- Multi-tenant isolation: one tenant's device IDs, costs, tariffs, reports, and cache keys must never leak into another tenant.
- Tariff lifecycle regression: tariff changes mid-month must produce correct cost on dashboard, calendar, energy report, and waste report.

## 7. Implemented CI Stage

The `Truth Parity Gate` stage is now in the Jenkinsfile after `Fast Checks` and before `Smoke E2E`.

Stage implementation:

- `Jenkinsfile`: calls `./scripts/run-truth-parity-gate.sh`
- `scripts/run-truth-parity-gate.sh`: 6 sequential `env PYTHONPATH=... pytest` invocations (avoids `app.*/src.*` namespace conflicts)
- `scripts/lib/test_layers.sh`: `TRUTH_PARITY_GATE_TARGETS` array for documentation

Individual invocation commands (also run by the gate script):

```bash
env JWT_SECRET_KEY=test-secret PYTHONPATH=services/device-service:services python -m pytest services/device-service/tests/test_live_dashboard_summary.py services/device-service/tests/test_device_loss_stats.py -q
env PYTHONPATH=services/energy-service:services python -m pytest services/energy-service/tests/test_device_range_live_overlay.py -q
env PYTHONPATH=. python -m pytest tests/test_energy_service_cost_alignment.py -q
env PYTHONPATH=services/reporting-service:services python -m pytest services/reporting-service/tests/test_report_task_tariff_warning.py -q
env PYTHONPATH=services/waste-analysis-service:services python -m pytest services/waste-analysis-service/tests/test_waste_historical_loss_parity.py -q
env PYTHONPATH=. python -m pytest tests/api/test_current_day_truth_parity.py -q
```

Or run all at once:

```bash
bash scripts/run-truth-parity-gate.sh
```

The `Truth Parity Gate` is mandatory for production deployment. Smoke E2E and full certification remain separate stages.

## Production Confidence Checklist

Before deployment, verify one controlled tenant/device:

- identify one device that is fully in waste mode
- dashboard today energy equals dashboard today loss
- today loss breakdown equals dashboard today loss
- calendar today equals dashboard today energy/loss
- energy-service range equals dashboard today energy/loss
- newly generated waste report equals canonical energy/loss
- newly generated energy report equals canonical energy
- old report artifacts are not used for live comparison

## Priority

Priority 1:

- current-day truth sync tests
- API parity smoke for full-waste single-device scenario
- report artifact freshness tests

Priority 2:

- mixed-operation API parity
- multi-device selected-scope parity
- `ALL` devices scope guard tests

Priority 3:

- browser E2E visual smoke for dashboard widgets
- PDF-level waste report display assertions
