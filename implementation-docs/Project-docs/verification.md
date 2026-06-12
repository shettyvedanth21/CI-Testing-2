# FactoryOPS Firmware Compatibility Verifier (Canonical)

This is the canonical firmware verification spec for FactoryOPS.
Use this file with firmware code in any GenAI model to get a deterministic `GO` / `NO_GO` verdict, exact fixes, and service-by-service readiness.

Reference legacy helper (kept as supporting material):
- `Project-docs/Firmware/parameterverification.md`

---

## 1) Purpose

Use this verifier to confirm that real sensor firmware telemetry will behave correctly across FactoryOPS services, not just simulator mode.

This verifier is a permanent contract-style gate focused on:
- field naming and aliases
- unit correctness
- timestamp quality
- MQTT topic and `device_id` integrity
- downstream compatibility across services

---

## 2) How To Use (External GenAI Workflow)

1. Copy the full prompt in **Section 12: VERIFIER_PROMPT (Copy/Paste)**.
2. Paste it into your target GenAI model.
3. Immediately paste your firmware codebase (or relevant files/functions that publish telemetry).
4. Ask the model to return output in the exact required schema from **Section 10**.
5. Treat the result as release-gating evidence for firmware onboarding.

---

## 3) Mandatory Input Artifacts

Required:
- Firmware source code (full preferred; minimum includes MQTT connect/publish code and telemetry payload builder).

Optional but recommended context:
- Device config defaults (idle threshold, overconsumption threshold).
- Shift windows (for off-hours logic).
- Tariff assumptions.
- Publish interval and retry policy.

If optional context is missing, verifier must explicitly state assumptions.

---

## 4) Hard-Gated Compatibility Contract (Critical)

All checks below are **release-gating**.
If any critical check fails, final verdict must be `NO_GO`.

### 4.1 MQTT Topic + Identity Contract

Required publish topic format:
- `devices/{device_id}/telemetry`

Critical checks:
- Topic suffix is exactly `/telemetry`.
- Topic path contains a valid `device_id` token before `telemetry`.
- Payload `device_id` exactly matches topic `device_id`.
- Payload is valid JSON object.

### 4.2 Required Payload Keys

Required:
- `device_id` (string)
- `timestamp` (parseable timestamp; ISO8601 UTC recommended)

Recommended:
- `schema_version: "v1"`

### 4.3 Numeric-Only Telemetry Rule

All telemetry metrics must be numeric values (`int`/`float` or safely numeric strings).

Critical fail examples:
- `"voltage": "230V"`
- `"current": "1.2A"`
- `"power": "0.8kw"`

### 4.4 Canonical Fields + Accepted Aliases

Canonical preferred fields:
- `voltage`
- `current`
- `power`
- `active_power`
- `kw`
- `power_factor`
- `energy_kwh`
- `frequency`
- `temperature`
- `kvar` / `reactive_power`

Accepted aliases seen in system logic:
- Current: `phase_current`, `i_l1`, `current_l1`, `current_l2`, `current_l3`
- Voltage: `voltage_l1`, `voltage_l2`, `voltage_l3`, `v_l1`, `v_l2`, `v_l3`
- PF: `pf`
- Energy: `kwh`, `energy`

Rule:
- If only non-canonical names are used, firmware must contain an explicit mapping layer.
- If no canonical output and no clear mapping, fail as compatibility risk.

### 4.5 Unit Contract (Critical)

Required units:
- `voltage`: V
- `current`: A
- `power`: **W**
- `active_power`: **W**
- `kw`: kW
- `energy_kwh`: cumulative kWh
- `power_factor`: 0..1
- `frequency`: Hz
- `temperature`: deg C
- `kvar` / `reactive_power`: kVAr (or clearly documented equivalent)

Critical rule:
- If firmware sends kW value under `power` / `active_power`, treat as unit mismatch and fail (`NO_GO`) unless firmware clearly converts to W before publish.

### 4.6 Timestamp Contract (Critical)

Timestamp checks:
- Parseable timestamp format.
- Monotonic sequence preferred.
- Duplicate timestamps must be limited/intentional.
- Large gaps should be explained by device state/network behavior.

Risk flags:
- Non-monotonic jumps (backward time).
- Frequent duplicate timestamps.
- Excessive interval gaps causing integration blind spots.

---

## 5) Decision Tree (Energy + State Logic)

Verifier must evaluate firmware against this processing order.

### 5.1 Energy Derivation Priority

1. Use `energy_kwh` delta (highest quality; cumulative expected).
2. Else integrate normalized power over time.
3. Else derive using `voltage * current * power_factor`.
4. If PF missing, derived path may assume fallback PF (quality downgrade).
5. Else insufficient telemetry for robust energy.

### 5.2 Quality Downgrade Rules

Downgrade quality when:
- PF missing on derived calculations.
- Partial telemetry coverage (intervals skipped).
- Non-monotonic / duplicate / large timestamp gaps.
- Missing `current` or `voltage` for idle/state logic.

### 5.3 Idle/Load State Classification Prerequisites

Expected state semantics:
- `unloaded`: `current <= 0` and `voltage > 0`
- `idle`: `0 < current < threshold` and `voltage > 0`
- `running`: `current >= threshold` and `voltage > 0`
- `unknown`: missing required telemetry or threshold

If firmware does not provide stable `current` + `voltage`, idle/waste outputs must be marked degraded.

---

## 6) Cross-Service Verification Matrix

Use this matrix in every firmware audit.

| Service | Firmware Dependency | Check Type | Must Verify |
|---|---|---|---|
| `data-service` | Direct ingest + validation + topic/device parity | Firmware-Gated | Topic format, payload `device_id` parity, required fields, numeric-only metrics, parseable timestamp |
| `device-service` | Runtime heartbeat and idle/current-state classification | Firmware-Gated | Stable `current`/`voltage`, timestamp freshness, threshold-compatible current behavior |
| `rule-engine-service` | Threshold/time-based alert evaluation | Firmware-Gated | Rule target fields are consistently emitted (`power`/`active_power`/`current`/`voltage`) with correct units |
| `analytics-service` | Dataset quality for anomaly/failure jobs | Firmware-Gated | Numeric consistency, timestamp continuity, no unstable unit switching |
| `reporting-service` | Energy, demand, load factor, cost calculations | Firmware-Gated | `energy_kwh` or consistent power/derived path, unit correctness for power, timestamp integration quality |
| `waste-analysis-service` | Idle/offhours/overconsumption energy and cost | Firmware-Gated | Idle prerequisites (`current`,`voltage`, threshold semantics), energy path availability, power-unit correctness |
| `data-export-service` | Export quality for downstream analytics/reporting datasets | Firmware-Gated | Telemetry fields are stable and numeric, timestamps parseable, schema consistency across payloads |
| `ui-web` (calendar/dashboard telemetry views) | Visualization and aggregate telemetry behavior | Firmware-Gated | Core telemetry fields renderable and coherent for summaries/charts/state indicators |
| `copilot-service` | Data-driven responses through service clients | Advisory | Firmware issues can degrade answer quality; verify telemetry availability and consistency impacts |

Blocking policy:
- `Firmware-Gated` failures can force `NO_GO`.
- `Advisory` findings alone do not force `NO_GO`, but must be reported.

---

## 7) Strict Failure Gates (`NO_GO` Triggers)

If any condition below is true, verdict must be `NO_GO`:

1. Topic is not effectively `devices/{device_id}/telemetry`.
2. Topic/payload `device_id` mismatch can occur.
3. Missing required payload keys (`device_id`, `timestamp`).
4. Core telemetry emitted as non-numeric values.
5. Power unit ambiguity unresolved (`power`/`active_power` not clearly in W).
6. No valid timestamp handling.
7. No viable energy path (`energy_kwh`, or power integration, or derivable V*I path).

---

## 8) Non-Blocking Warnings (Still Report)

Warnings do not block onboarding by themselves unless they break critical gates.

Examples:
- Missing `schema_version`.
- PF absent but fallback path available.
- Aliases used instead of canonical names with clear mapping layer present.
- Sparse/irregular publish interval with acceptable minimum coverage.

---

## 9) Anti-Ambiguity Rules For External Models

The verifier model must follow these strict rules:

1. No generic advice.
2. Every claimed issue must cite firmware location (`file`, function, and line range if available).
3. Distinguish:
   - **Confirmed issue** (proven from code)
   - **Inferred risk** (insufficient evidence, explain assumption)
4. Do not claim runtime behavior that is not supported by code evidence.
5. Never emit `GO` when any critical gate fails.
6. Do not skip sections from required output schema.

---

## 10) Required Output Schema (Exact)

The verifying model must return exactly these sections in this order:

1. `Compatibility Score (0-100)`
2. `Go/No-Go Verdict`
3. `Critical Checks`
4. `Service Readiness`
5. `Detected Risks`
6. `Exact Fixes Required`
7. `Final Certification`

### 10.1 Section Rules

`Compatibility Score (0-100)`
- Integer score with short rationale.

`Go/No-Go Verdict`
- Must be exactly `GO` or `NO_GO`.

`Critical Checks`
- Table with at least:
  - Topic contract
  - `device_id` parity
  - Required fields
  - Numeric compliance
  - Canonical/alias compatibility
  - Unit compliance
  - Timestamp quality
- Each row must include `PASS`/`FAIL` and evidence.

`Service Readiness`
- Table containing all services listed in Section 6.
- Status per service: `PASS`, `FAIL`, or `DEGRADED`.
- Must include impact reason.

`Detected Risks`
- Ordered `High -> Medium -> Low`.
- Each item tagged `Confirmed` or `Inferred`.

`Exact Fixes Required`
- Use format from Section 11.

`Final Certification`
- Must be exactly one token:
  - `READY_FOR_ONBOARDING`
  - `NOT_READY`

Certification mapping:
- `GO` -> `READY_FOR_ONBOARDING`
- `NO_GO` -> `NOT_READY`

---

## 11) Required Fix Format

For every required fix, use this exact structure:

- `Issue`: concise failure description
- `Why it breaks`: direct service impact
- `Firmware evidence`: file/function/line reference
- `Patch guidance`: exact code-level correction
- `Expected telemetry sample after fix`: corrected JSON example
- `Retest check`: which critical check/service should pass after fix

If no fixes are required, still output:
- `Exact Fixes Required: None`

---

## 12) VERIFIER_PROMPT (Copy/Paste)

Copy everything below into another GenAI model, then paste firmware code after it.

```text
You are a deterministic firmware compatibility auditor for FactoryOPS.

Task:
Audit the provided firmware code for telemetry compatibility and return a strict GO/NO_GO verdict.

Non-negotiable rules:
1) Do not provide generic advice.
2) Every issue must include firmware evidence (file/function/line if available).
3) Separate Confirmed issues from Inferred risks.
4) If any critical gate fails, verdict must be NO_GO.
5) Use only the required output schema and section order.

Critical compatibility contract:
- MQTT publish topic must be effectively devices/{device_id}/telemetry.
- Topic device_id and payload device_id must match exactly.
- Required payload fields: device_id, timestamp.
- Telemetry metrics must be numeric (no unit-suffixed strings).
- Canonical fields preferred: voltage, current, power, active_power, kw, power_factor, energy_kwh, frequency, temperature, kvar/reactive_power.
- Accepted aliases may be used only with explicit mapping clarity.
- Unit rules:
  - power and active_power must be in Watts.
  - kw must be in kW.
  - energy_kwh must be cumulative kWh.
  - power_factor range 0..1.
- Timestamp must be parseable and integration-safe (monotonic preferred; flag duplicates/regressions/gaps).

Energy decision tree to evaluate:
1) energy_kwh delta preferred.
2) else integrate normalized power.
3) else derive from voltage*current*power_factor.
4) if PF missing, downgrade quality and flag risk.
5) else insufficient energy path.

Idle/state prerequisites:
- unloaded: current <= 0 and voltage > 0
- idle: 0 < current < threshold and voltage > 0
- running: current >= threshold and voltage > 0
- otherwise unknown/degraded

Service readiness must cover:
- data-service
- device-service
- rule-engine-service
- analytics-service
- reporting-service
- waste-analysis-service
- data-export-service
- ui-web (calendar/dashboard telemetry paths)
- copilot-service

Use blocking policy:
- Firmware-Gated service failures can block GO.
- Advisory-only impacts cannot alone force NO_GO.

Required output format (exact sections in order):
1. Compatibility Score (0-100)
2. Go/No-Go Verdict
3. Critical Checks
4. Service Readiness
5. Detected Risks
6. Exact Fixes Required
7. Final Certification

Output constraints:
- Go/No-Go Verdict must be exactly GO or NO_GO.
- Final Certification must be exactly READY_FOR_ONBOARDING or NOT_READY.
- If verdict is GO, certification must be READY_FOR_ONBOARDING.
- If verdict is NO_GO, certification must be NOT_READY.

Now analyze the firmware code pasted below.
```

---

## 13) Validation Scenarios For This Verifier (Quality Test)

Use these scenario checks to validate this verifier prompt itself:

1. Perfect payload + units -> expected `GO`.
2. Topic/payload `device_id` mismatch -> expected `NO_GO`.
3. `power` carrying kW values -> expected `NO_GO` with explicit unit fix.
4. Missing `current`/`voltage` but valid `energy_kwh` -> reporting may pass; idle/waste must be degraded.
5. Non-numeric telemetry like `"230V"` -> expected `NO_GO`.
6. Timestamp regressions/duplicates/large gaps -> must produce risk + service impact.

Acceptance target:
- Two different GenAI models produce materially consistent verdicts for the same firmware sample.
- Critical gate violations always produce `NO_GO`.
- Service impact mapping remains explicit and non-generic.

---

## 14) Stable Interface Contract

Input interface:
- `VERIFIER_PROMPT + firmware code`

Output interface:
- Deterministic sections defined in Section 10.

No runtime API/schema change is implied by this file.

