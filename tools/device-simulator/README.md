# Device Simulator for Energy Intelligence Platform

Production-grade MQTT device simulator for generating realistic telemetry data.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run simulator
python main.py --device-id D1 --tenant-id SH00000001 --interval 5

# With custom broker
python main.py --device-id D1 --interval 5 --broker localhost --port 1883

# With the normal authenticated device path on MQTT 1883
python main.py \
  --device-id D1 \
  --tenant-id SH00000001 \
  --broker localhost \
  --port 1883 \
  --auth-service-url http://localhost:8090 \
  --device-service-url http://localhost:8000 \
  --mqtt-credential-bootstrap-email admin@example.com \
  --mqtt-credential-bootstrap-password 'secret'

# With a QR/onboarding provisioning bundle captured from Shivex
python main.py \
  --provisioning-bundle-file /path/to/provisioning-bundle.json \
  --interval 5

# With fault injection
python main.py --device-id D1 --interval 5 --fault-mode overheating

# With fallback heartbeat during MQTT/session degradation
# When launched from a host shell, point to localhost. Inside compose, Docker DNS works.
python main.py --device-id D1 --device-service-url http://localhost:8000 --heartbeat-interval 20
```

## CLI Options

- `--device-id`: Device identifier (required)
- `--tenant-id` / `--tenant-id`: Tenant identifier used in MQTT topic (default: `SH00000001`)
- `--interval`: Publish interval in seconds (default: 5)
- `--broker`: MQTT broker host (default: localhost)
- `--port`: MQTT broker port (default: 1883)
- `--mqtt-username`: Explicit MQTT username override
- `--mqtt-password`: Explicit MQTT password override
- `--fault-mode`: Fault injection mode - 'none', 'spike', 'drop', 'overheating' (default: none)
- `--log-level`: Logging level (default: INFO)
- `--device-service-url`: Device Service URL used for heartbeat fallback. Defaults to `http://device-service:8000` when Docker DNS is available, otherwise `http://localhost:8000`.
- `--auth-service-url`: Auth Service URL used for credential bootstrap. Defaults to `http://auth-service:8090` when Docker DNS is available, otherwise `http://localhost:8090`.
- `--mqtt-credential-bootstrap-email`: Admin email used to fetch a per-device MQTT credential
- `--mqtt-credential-bootstrap-password`: Admin password used to fetch a per-device MQTT credential
- `--provisioning-bundle`: Raw JSON provisioning bundle captured from the onboarding QR
- `--provisioning-bundle-file`: Path to a JSON file containing the onboarding provisioning bundle
- `--heartbeat-interval`: Heartbeat fallback interval in seconds (default: `20`)

Heartbeat fallback and onboarding checks are tenant-aware. The simulator publishes to:

```text
<tenant_id>/devices/<device_id>/telemetry
```

When a provisioning bundle is supplied, it becomes the source of truth for:

- broker host
- broker port
- tenant ID
- device ID
- MQTT username
- MQTT password
- canonical telemetry topic

Expected provisioning bundle shape:

```json
{
  "version": 1,
  "broker": "shivex.ai",
  "port": 1883,
  "tenant_id": "SH00000001",
  "device_id": "TD00000001",
  "username": "device:SH00000001:TD00000001",
  "password": "one-time-secret",
  "topic": "SH00000001/devices/TD00000001/telemetry"
}
```

## CSV Replay

Replay an exported telemetry CSV through the same MQTT ingestion path:

```bash
python csv_replay.py \
  --csv /path/to/td00000001.csv \
  --device-id TD00000002 \
  --tenant-id SH00000001 \
  --broker localhost \
  --port 1883
```

The replay utility:

- skips Influx export metadata rows that begin with `#`
- preserves the original `_time` timestamp in each published payload
- sleeps for the original row-to-row gap before publishing the next sample
- publishes to `<tenant_id>/devices/<device_id>/telemetry`

The compose-managed `local-device-simulator` uses the same device contract as production:

- MQTT TCP `1883`
- per-device username/password fetched from Shivex before the simulator connects
- MySQL-backed EMQX auth and ACL enforcement with anonymous access denied

To seed historical data quickly while keeping the original payload timestamps intact:

```bash
python csv_replay.py \
  --csv /path/to/td00000001.csv \
  --device-id TD00000002 \
  --tenant-id SH00000001 \
  --broker localhost \
  --port 1883 \
  --no-delay
```

## Telemetry Schema

```json
{
  "device_id": "D1",
  "timestamp": "2026-02-07T11:26:00Z",
  "schema_version": "v1",
  "voltage": 230.5,
  "current": 0.85,
  "power": 195.9,
  "temperature": 45.2
}
```

## Features

- Realistic time-series data generation with smooth variation and noise
- MQTT QoS 1 publishing with automatic reconnect
- Exponential backoff + jitter for reconnection (no retry cap)
- Buffered replay of telemetry after reconnect
- Heartbeat fallback to `device-service` only during broker/session disruption
- Graceful shutdown handling
- Structured JSON logging
- Multiple fault injection modes
- Production-ready error handling
