# FactoryOPS Parameter Verification Prompt (Firmware Code Audit)

Use this document as a copy-paste prompt template in any GenAI model.

Goal: validate whether a firmware codebase will produce telemetry fully compatible with FactoryOPS services:
- data-service ingestion
- device-service runtime + idle state
- reporting-service energy reports
- waste-analysis-service waste/idle calculations
- rule-engine trigger compatibility

---

## How to Use

1. Copy everything in section **"LLM VERIFICATION PROMPT"** below.
2. Paste into your GenAI model.
3. After the prompt, paste full firmware code.
4. Ask model to produce output exactly in the requested format.

---

## LLM VERIFICATION PROMPT

You are validating firmware telemetry compatibility for FactoryOPS.

Analyze the provided firmware code and determine if it is production-compatible with these backend contracts.

Return:
- strict PASS/FAIL per check
- exact issues with file/function references
- exact fixes
- final go/no-go verdict

Do not give generic advice. Be deterministic.

### 1) MQTT Contract (Mandatory)

Firmware must publish telemetry to:
- `devices/{device_id}/telemetry`

Rules:
- topic must end with `/telemetry`
- `device_id` in topic must match payload `device_id` exactly
- payload must be valid JSON

Check and report:
- broker host configurability (localhost/EC2 support)
- port configurability (1883/8883)
- QoS setting (recommend 1)

### 2) Required Payload Fields

Payload must include:
- `device_id` (string)
- `timestamp` (ISO8601 UTC preferred, e.g. `2026-03-08T04:28:00Z`)

Optional but recommended:
- `schema_version` = `v1`

### 3) Numeric Field Rule

FactoryOPS accepts dynamic metrics only if numeric.
If firmware sends non-numeric telemetry values for metrics, ingestion can fail/drop.

Validate all telemetry fields are emitted as numeric when intended for analytics.

### 4) Canonical Field Names (Critical for Cross-Service Compatibility)

Preferred lowercase keys (exact names):
- `voltage`
- `current`
- `power`
- `temperature`
- `frequency`
- `power_factor`
- `energy_kwh`
- `kvar` (optional)
- phase variants: `current_l1/l2/l3`, `voltage_l1/l2/l3`

Important:
- Even if some modules normalize case, reporting and downstream logic are most reliable with exact lowercase canonical names.
- Mark FAIL if firmware uses only incompatible naming and no mapping layer.

### 5) Unit Contract (Must Match)

Required units:
- `voltage`: Volts (V)
- `current`: Amperes (A)
- `power`: **Watts (W)**
- `active_power`: **Watts (W)** if used
- `kw`: kW (only if key is explicitly `kw`)
- `energy_kwh`: cumulative kWh
- `power_factor`: 0..1
- `frequency`: Hz
- `temperature`: °C

Critical compatibility rule:
- If firmware sends `power` in kW instead of W, energy/waste cost calculations will be inflated/incorrect.

### 6) Service-Specific Compatibility Tests

#### A) Reports Engine Compatibility
Check that firmware data can satisfy this priority:
1. `energy_kwh` delta (best)
2. integrate `power` over time
3. derive from `current * voltage * power_factor / 1000`
4. if PF missing, fallback PF=1.0 (low quality)
5. else insufficient

Must verify:
- timestamps are monotonic and frequent enough for integration
- no random timestamp jumps/backward writes
- no inconsistent unit switching across packets

#### B) Waste Analysis Compatibility
Must support:
- total energy path (same priority as reports)
- idle state calculations from current/voltage + threshold
- standby estimation during idle periods
- tariff-based cost multiplication

If firmware lacks stable `current` + `voltage`, idle and waste quality degrade.

#### C) Idle Running Compatibility
Idle state logic expects:
- `unloaded`: current <= 0 and voltage > 0
- `idle`: 0 < current < threshold and voltage > 0
- `running`: current >= threshold and voltage > 0

If firmware omits `current` or `voltage`, state becomes unknown.

#### D) Rules Compatibility
Time/threshold rules rely on:
- `power`/`active_power` or `current` (+optional `voltage`)

Verify firmware emits these fields consistently.

### 7) Required Output Format (Exact)

Return exactly these sections:

1. `Compatibility Score (0-100)`
2. `Go/No-Go Verdict` (GO only if all Critical checks pass)
3. `Critical Checks` table:
   - Topic contract
   - device_id match
   - Required fields
   - Canonical keys
   - Unit compliance
   - Timestamp quality
4. `Service Readiness` table:
   - Data Service: PASS/FAIL
   - Device Idle/Load: PASS/FAIL
   - Reporting Engine: PASS/FAIL
   - Waste Analysis: PASS/FAIL
   - Rules Engine: PASS/FAIL
5. `Detected Risks` (ordered High->Low)
6. `Exact Fixes Required` (code-level, with file/function reference)
7. `Final Certification`:
   - `READY_FOR_ONBOARDING` or `NOT_READY`

### 8) Strict Failure Gates

If any of these occur, final verdict must be `NOT_READY`:
- topic is not `devices/{device_id}/telemetry`
- topic/payload `device_id` mismatch possible
- `power` unit ambiguity not resolved
- non-numeric telemetry for core fields
- no valid timestamp handling
- missing current+voltage with no alternate energy path

Now analyze the firmware code pasted below.

---

## Quick Manual Checklist (Optional, Human)

Use this as a fast pre-check before running AI verification:

- [ ] Topic = `devices/{device_id}/telemetry`
- [ ] Payload has `device_id`, `timestamp`
- [ ] `power` is Watts (not kW)
- [ ] `energy_kwh` cumulative value available (preferred)
- [ ] `current` and `voltage` present
- [ ] `power_factor` present (or consciously omitted)
- [ ] Field names are lowercase canonical
- [ ] Timestamps UTC + monotonic
- [ ] QoS = 1
- [ ] Broker host/port configurable for localhost and EC2

---

## Expected Result Quality

A firmware integration is considered production-safe only when:
- Reports and Waste both pass with no unit mismatch warnings
- Idle state can be computed deterministically
- Rule triggers can evaluate the emitted parameters
- No critical contract violations exist

