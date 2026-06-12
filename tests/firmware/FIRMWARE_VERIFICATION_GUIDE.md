# FactoryOPS Firmware Verification Guide
## How to use this document
Read the firmware source code statically and decide whether the implementation matches the FactoryOPS telemetry contract below.

Important backend reality:
- The MQTT ingest path subscribes to `devices/+/telemetry` with QoS 1.
- The validator only strictly requires `device_id` and `timestamp`.
- Any additional payload fields are accepted only if they are numeric, except `schema_version`, which is skipped by validation and defaults to `v1` downstream.
- The current backend does not hard-reject recommended operating ranges in `validation.py`; those ranges are the platform's normal operating envelope.

Scope note:
- This guide verifies the telemetry ingestion lane only.
- In the production Shivex SaaS MQTT auth flow, the provisioning bundle may also instruct firmware to publish a device status topic and subscribe to command/config/OTA topics. Those control topics are outside the payload-validation scope of this guide.

## Section 1 — MQTT Connection Requirements
Exact broker settings the firmware must use:
- Broker host format: use the FactoryOPS MQTT broker host configured for deployment. The current data-service defaults to `localhost`.
- Port (1883 for TCP, 8083 for WebSocket): the backend consumer connects over MQTT TCP on `1883`; use `8083` only if your deployment explicitly uses a WebSocket broker endpoint.
- QoS level required (QoS 1): publish with QoS 1.
- Client ID format: any unique, stable client ID is acceptable; keep it device-specific to avoid session collisions. Recommended format: `firmware-{device_id}`.
- Clean session requirements: use a persistent session (`clean_session=false`) so QoS 1 state is retained across reconnects.
- Reconnection requirements: reconnect automatically on disconnect; use exponential backoff with jitter, do not drop buffered telemetry, and retry until the broker is reachable again.

## Section 2 — MQTT Topic Format
Exact topic the firmware must publish to:
- Topic pattern with example: `devices/{device_id}/telemetry`
- What device_id must look like: letters, numbers, hyphens, and underscores only. This matches the backend topic parser and repository validation regex.
- Case sensitivity rules: publish the canonical lowercase `telemetry` suffix. The backend accepts the suffix case-insensitively, but the device ID segment is compared as an exact string.

## Section 3 — Payload Schema (REQUIRED)
Exact JSON schema the firmware must send.

Backend contract summary:
- `device_id` and `timestamp` are required by validation.
- `schema_version` is optional, but the backend defaults it to `v1` in downstream models and storage.
- Any additional fields are allowed only if they are numeric at validation time.
- The backend accepts additional numeric telemetry fields such as `voltage`, `current`, `power`, `power_factor`, `energy_kwh`, `temperature`, and `frequency`, plus other numeric metrics defined by the platform.

For every field:
  - Field name (exact, case sensitive)
  - Data type (must be number, not string)
  - Required or optional
  - Valid range
  - Example value
  - What happens if missing or wrong type

Show the complete example payload:
{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-22T10:00:00+00:00",
  "schema_version": "v1",
  "voltage": 231.0,
  "current": 12.5,
  "power": 2875.0,
  "power_factor": 0.97,
  "energy_kwh": 1200.0
}

Additional numeric fields accepted by the backend and commonly used by FactoryOPS:
- `active_power`
- `apparent_power`
- `current_l1`
- `current_l2`
- `current_l3`
- `energy`
- `energy_kwh`
- `frequency`
- `kva`
- `kvar`
- `pf`
- `power`
- `power_factor`
- `reactive_power`
- `run_hours`
- `temperature`
- `thd`
- `voltage`
- `voltage_l1`
- `voltage_l2`
- `voltage_l3`

## Section 4 — Field Validation Rules
For every field, exact rules:

| Field | Type | Required | Min | Max | Format | Fail condition |
|-------|------|----------|-----|-----|--------|----------------|
| device_id | string | YES | - | - | letters, numbers, hyphens, underscores | missing = rejected |
| timestamp | string | YES | - | - | ISO 8601 UTC (`Z` or `+00:00`) | missing or invalid format = rejected |
| schema_version | string | NO | - | - | should be `"v1"` | missing = defaults to `v1`; wrong type is not hard-rejected by validation.py |
| voltage | number | NO | recommended 200-250 | - | numeric JSON value | non-numeric string = rejected; numeric strings may still pass current validation |
| current | number | NO | recommended 0-2 | - | numeric JSON value | non-numeric string = rejected; numeric strings may still pass current validation |
| power | number | NO | recommended 0-500 | - | numeric JSON value | non-numeric string = rejected |
| power_factor | number | NO | recommended 0.0-1.0 | - | numeric JSON value | non-numeric string = rejected; values > 1.0 are still accepted by current validation |
| energy_kwh | number | NO | 0 | - | numeric JSON value | non-numeric string = rejected |
| temperature | number | NO | recommended 20-80 | - | numeric JSON value | non-numeric string = rejected |
| frequency | number | NO | recommended 48-52 | - | numeric JSON value | non-numeric string = rejected |

Other backend facts:
- The validator rejects any additional field that cannot be converted with `float(...)`, except `schema_version`.
- The MQTT consumer and repository both treat `device_id` as valid only when it matches `^[A-Za-z0-9_-]+$`.
- `TelemetryPayload` stores `timestamp` as a datetime object internally, but the firmware should send an ISO 8601 UTC string on the wire.

## Section 5 — Publish Frequency Requirements
- Minimum: every 60 seconds
- Recommended: every 5 seconds
- Maximum: once per second (do not flood)
- What happens if too fast: the backend does not enforce a hard publish-rate limit, but overly fast publishing can overwhelm downstream queues, storage, and rule evaluation.

## Section 6 — Error Handling Requirements
- What firmware must do on MQTT disconnect: stop assuming delivery succeeded, mark the broker connection as unhealthy, and start reconnect logic immediately.
- Reconnection backoff strategy: use exponential backoff with jitter, starting around 1 second and capping near 60 seconds.
- What to do if publish fails: keep the sample buffered locally if possible and retry after reconnect; do not silently discard telemetry.
- QoS 1 acknowledgment handling: wait for PUBACK or the MQTT client's publish completion signal before removing a sample from the retry buffer.

## Section 7 — Verification Checklist for AI Model
This is the checklist the AI model fills out after reading firmware code.

For each item: PASS / FAIL / CANNOT DETERMINE + explanation

MQTT CONNECTION:
[ ] 1. Firmware connects to correct broker host format
[ ] 2. Firmware uses port 1883 (TCP) or 8083 (WebSocket)
[ ] 3. Firmware uses QoS 1 for publish
[ ] 4. Firmware has reconnection logic on disconnect
[ ] 5. Firmware has exponential backoff on reconnect

TOPIC FORMAT:
[ ] 6. Firmware publishes to correct topic: devices/{device_id}/telemetry
[ ] 7. device_id in topic matches device_id in payload
[ ] 8. Topic is hardcoded or configurable (note which)

PAYLOAD FORMAT:
[ ] 9. Payload is valid JSON
[ ] 10. device_id field present and is string
[ ] 11. timestamp field present and is ISO 8601 UTC format
[ ] 12. schema_version field present and equals "v1"
[ ] 13. voltage field present and is number (float or int)
[ ] 14. current field present and is number (float or int)
[ ] 15. All numeric fields are numbers NOT strings

DATA TYPES:
[ ] 16. voltage is float/int not "230.5" string
[ ] 17. current is float/int not "12.5" string
[ ] 18. power_factor is between 0.0 and 1.0 if present
[ ] 19. No field that should be number is sent as string

VALUE RANGES:
[ ] 20. voltage range is realistic (0-500V)
[ ] 21. current range is realistic (0-100A)
[ ] 22. power_factor is 0.0-1.0 if sent
[ ] 23. temperature range is realistic (-50 to 200°C) if sent
[ ] 24. frequency is 45-65 Hz if sent

PUBLISH BEHAVIOR:
[ ] 25. Firmware publishes at reasonable interval (1s-60s)
[ ] 26. Firmware does not flood (not faster than 1/second)
[ ] 27. Firmware publishes continuously not just once

ERROR HANDLING:
[ ] 28. Firmware handles MQTT disconnect gracefully
[ ] 29. Firmware attempts reconnection
[ ] 30. Firmware does not crash on publish failure

OVERALL VERDICT:
[ ] READY FOR PRODUCTION — all critical checks pass
[ ] NOT READY — list of failing checks that must be fixed
[ ] PARTIAL — works but has warnings that should be fixed

## Section 8 — How to use with AI model
Exact prompt to give to AI model:

"You are a firmware verification engineer. 
Read the attached firmware source code completely.
Then fill out every item in Section 7 checklist with PASS, FAIL, 
or CANNOT DETERMINE.
For every FAIL: explain exactly what is wrong and what the fix is.
For every CANNOT DETERMINE: explain what information is missing.
At the end give the OVERALL VERDICT.
Be specific — reference exact file names, function names, and 
line numbers from the firmware code."
