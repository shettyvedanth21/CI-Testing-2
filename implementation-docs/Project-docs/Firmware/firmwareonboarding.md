# FactoryOPS Firmware Onboarding Guide

This document is the firmware contract for sending telemetry into FactoryOPS.

It is based on the current implementation in:
- `services/data-service/src/handlers/mqtt_handler.py`
- `services/data-service/src/utils/validation.py`
- `services/data-service/src/models/telemetry.py`
- `services/data-service/src/repositories/influxdb_repository.py`
- `services/reporting-service/src/services/report_engine.py`
- `services/waste-analysis-service/src/services/waste_engine.py`

---

## 1) Transport & Broker

## MQTT Broker (default stack)
- Broker: `EMQX`
- Protocol: `mqtt`
- Default port: `1883` (non-TLS)
- TLS port (if enabled): `8883`

## Localhost (developer machine)
- Host: `localhost`
- Port: `1883`

## Docker internal network (service-to-service)
- Host: `emqx`
- Port: `1883`

## EC2 deployment
Use your public DNS/IP for clients outside the VPC.
- Host: `<EC2_PUBLIC_IP_OR_DNS>`
- Port: `1883` (or `8883` for TLS)

Example:
```text
mqtt://<EC2_PUBLIC_IP_OR_DNS>:1883
```

---

## 2) Topic Contract (Mandatory)

## Primary supported publish topic
```text
devices/{device_id}/telemetry
```

Example:
```text
devices/COMPRESSOR-003/telemetry
```

## Important
- `data-service` is configured to subscribe to `devices/+/telemetry` by default.
- The service also validates topic suffix semantics (`.../{device_id}/telemetry`) and extracts `device_id` from topic.
- If payload `device_id` does not match topic `device_id`, the message is dropped.

So for production reliability, publish only to:
- `devices/{device_id}/telemetry`

---

## 3) Payload Schema Contract

Payload must be JSON object with:

## Required fields
- `device_id` (string)
- `timestamp` (ISO8601 string preferred, UTC)

## Optional standard field
- `schema_version` (string), default expected: `"v1"`

## Dynamic telemetry fields
- Any additional field is accepted **if numeric** (`int`/`float`/numeric string).
- Non-numeric custom fields are rejected by validator.

## Recommended timestamp format
Use UTC ISO8601 with `Z`:
```text
2026-03-08T04:28:00Z
```

Also accepted by validator:
- unix epoch (seconds)
- datetime parseable string

---

## 4) Unit Contract (Critical)

To keep Analytics/Reports/Waste calculations consistent, firmware must follow these units:

| Field | Unit required | Notes |
|---|---|---|
| `voltage` | Volts (V) | numeric |
| `current` | Amperes (A) | numeric |
| `power` | **Watts (W)** | service normalizes to kW internally |
| `active_power` | **Watts (W)** | same as `power` handling |
| `kw` | kW | if you send this key, it is treated as already kW |
| `energy_kwh` | kWh (cumulative meter reading) | preferred for highest-quality energy math |
| `power_factor` | ratio 0..1 | if missing, some calculations assume PF=1.0 with warning |
| `frequency` | Hz | optional |
| `temperature` | °C | optional |
| `kvar` / `reactive_power` | kVAr | optional |

## Three-phase naming (supported)
Current extraction supports aliases including:
- `current_l1`, `current_l2`, `current_l3`
- `i_l1` (and similar)

Voltage extraction supports aliases including:
- `voltage_l1`, `voltage_l2`, `voltage_l3`
- `v_l1` (and similar)

If all 3 phase currents exist, internal logic may use phase-based handling (max/avg depending on module).

---

## 5) Example Payloads

## Minimal valid payload
```json
{
  "device_id": "COMPRESSOR-003",
  "timestamp": "2026-03-08T04:28:00Z",
  "power": 245.7
}
```

## Recommended full payload
```json
{
  "device_id": "COMPRESSOR-003",
  "timestamp": "2026-03-08T04:28:00Z",
  "schema_version": "v1",
  "voltage": 229.8,
  "current": 1.07,
  "power": 245.7,
  "temperature": 46.2,
  "frequency": 49.98,
  "power_factor": 0.96,
  "energy_kwh": 1275.442
}
```

## Three-phase payload example
```json
{
  "device_id": "COMPRESSOR-003",
  "timestamp": "2026-03-08T04:28:00Z",
  "schema_version": "v1",
  "voltage_l1": 228.4,
  "voltage_l2": 229.1,
  "voltage_l3": 227.9,
  "current_l1": 1.10,
  "current_l2": 1.04,
  "current_l3": 1.08,
  "power": 246.2,
  "power_factor": 0.95,
  "energy_kwh": 1275.455
}
```

---

## 6) Publish Examples

## A) Localhost test (`mosquitto_pub`)
```bash
mosquitto_pub -h localhost -p 1883 \
  -t devices/COMPRESSOR-003/telemetry \
  -q 1 \
  -m '{"device_id":"COMPRESSOR-003","timestamp":"2026-03-08T04:28:00Z","voltage":229.8,"current":1.07,"power":245.7,"energy_kwh":1275.442,"schema_version":"v1"}'
```

## B) EC2 test
```bash
mosquitto_pub -h <EC2_PUBLIC_IP_OR_DNS> -p 1883 \
  -t devices/COMPRESSOR-003/telemetry \
  -q 1 \
  -m '{"device_id":"COMPRESSOR-003","timestamp":"2026-03-08T04:28:00Z","power":245.7}'
```

If authentication is enabled in your broker:
```bash
mosquitto_pub -h <BROKER_HOST> -p <PORT> -u <USERNAME> -P <PASSWORD> ...
```

If TLS is enabled:
```bash
mosquitto_pub -h <BROKER_HOST> -p 8883 --cafile <ca.crt> ...
```

---

## 7) Data Acceptance Rules (Drop Conditions)

Message is dropped if:
- topic does not end with `/telemetry`
- topic does not contain valid `{device_id}` token
- payload is not valid JSON
- required fields missing (`device_id`, `timestamp`)
- any dynamic metric is non-numeric
- payload `device_id` != topic `device_id`

---

## 8) Verification Steps (After Firmware Integration)

1. Onboard device in platform first:
- `POST /api/v1/devices`

2. Start publishing to topic:
- `devices/{device_id}/telemetry`

3. Verify telemetry arrival:
- `GET /api/v1/data/telemetry/{device_id}`

4. Verify runtime heartbeat updated:
- `GET /api/v1/devices/{device_id}` and check `last_seen_timestamp`

5. Verify rules can evaluate:
- create a threshold rule and confirm alerts fire when values cross threshold

---

## 9) Recommended Firmware Publish Behavior

- QoS: `1`
- Retain: `false`
- Publish interval: `5s` (or as required)
- Clock source: NTP-synced UTC
- Do not send stale/buffered old timestamps without flagging at firmware layer
- Keep numeric precision stable (avoid random field type changes int/str)

---

## 10) Troubleshooting Quick Map

## Symptom: Device shows no telemetry
Check:
- topic exactly `devices/{device_id}/telemetry`
- payload JSON validity
- `device_id` topic/payload match
- broker reachability (`1883`/security group/firewall)

## Symptom: Energy calculations look wrong
Check units:
- `power` must be Watts
- or send `kw` key if value is already kW
- send cumulative `energy_kwh` where possible (best quality)

## Symptom: Idle/load state unknown
Check:
- `current` and `voltage` are present and numeric
- idle threshold configured in Parameter Configuration for that device

---

## 11) Final Firmware Hand-off Checklist

- [ ] Topic format implemented exactly
- [ ] Payload has `device_id` + UTC `timestamp`
- [ ] All telemetry metrics numeric
- [ ] Unit contract implemented (`power` in W)
- [ ] QoS 1 enabled
- [ ] Broker host/port configurable for localhost + EC2
- [ ] Device IDs match exactly platform onboarding IDs

