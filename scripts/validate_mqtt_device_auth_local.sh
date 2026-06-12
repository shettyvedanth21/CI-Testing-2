#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.local}"
COMPOSE_FILES="-f $ROOT_DIR/docker-compose.yml -f $ROOT_DIR/docker-compose.local.yml"
AUTH_BASE_URL="${AUTH_BASE_URL:-http://localhost:8090}"
DEVICE_BASE_URL="${DEVICE_BASE_URL:-http://localhost:8000}"
DATA_BASE_URL="${DATA_BASE_URL:-http://localhost:8081}"
TENANT_ID="${TENANT_ID:-SH00000001}"
PROBE_DEVICE_ID="${PROBE_DEVICE_ID:-AD00000002}"
SIMULATOR_CONTAINER="${SIMULATOR_CONTAINER:-local_device_simulator}"
SIMULATOR_DEVICE_ID="${SIMULATOR_DEVICE_ID:-AD00000001}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd docker
require_cmd python3

compose() {
  # shellcheck disable=SC2086
  docker compose --env-file "$ENV_FILE" $COMPOSE_FILES "$@"
}

compose_project_name() {
  compose config 2>/dev/null | sed -n 's/^name: //p' | head -n 1
}

compose_network_name() {
  local project
  project="$(compose_project_name)"
  if [ -z "$project" ]; then
    project="$(basename "$ROOT_DIR" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-')"
  fi
  printf '%s_energy-net\n' "$project"
}

wait_for_http() {
  url="$1"
  label="$2"
  retries="${3:-60}"
  i=0
  while [ "$i" -lt "$retries" ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 2
  done
  echo "Timed out waiting for $label at $url" >&2
  exit 1
}

assert_emqx_security_runtime() {
  tcp_listener="$tmpdir/emqx_tcp_listener.txt"
  authn_chain="$tmpdir/emqx_authn_chain.txt"
  authz_sources="$tmpdir/emqx_authz_sources.txt"

  compose exec -T emqx /opt/emqx/bin/emqx eval \
    'io:format("~p~n", [emqx_config:get_raw([listeners, tcp, default], undefined)]).' \
    >"$tcp_listener"
  compose exec -T emqx /opt/emqx/bin/emqx eval \
    'io:format("~p~n", [emqx_config:get_raw([authentication], undefined)]).' \
    >"$authn_chain"
  compose exec -T emqx /opt/emqx/bin/emqx eval \
    'io:format("~p~n", [emqx_config:get_raw([authorization, sources], undefined)]).' \
    >"$authz_sources"

  if ! grep -q 'quick_deny_anonymous' "$tcp_listener"; then
    echo "EMQX runtime TCP listener is not configured to deny anonymous clients." >&2
    cat "$tcp_listener" >&2
    exit 1
  fi

  if ! grep -q '<<"backend">> => <<"mysql">>' "$authn_chain"; then
    echo "EMQX runtime did not load MySQL authentication." >&2
    cat "$authn_chain" >&2
    exit 1
  fi

  if ! grep -Eq '<<"type">> => mysql|<<"type">> => <<"mysql">>' "$authz_sources"; then
    echo "EMQX runtime did not load MySQL authorization." >&2
    cat "$authz_sources" >&2
    exit 1
  fi
}

extract_json_field() {
  json_path="$1"
  file_path="$2"
  python3 - "$json_path" "$file_path" <<'PY'
import json
import sys

path = sys.argv[1].split(".")
with open(sys.argv[2], "r", encoding="utf-8") as fh:
    data = json.load(fh)

value = data
for part in path:
    value = value[part]

if isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PY
}

env_value() {
  name="$1"
  python3 - "$ENV_FILE" "$name" <<'PY'
import sys

env_file = sys.argv[1]
target = sys.argv[2]
with open(env_file, "r", encoding="utf-8") as fh:
    for raw_line in fh:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == target:
            print(value)
            break
PY
}

echo "Starting local stack with EMQX MySQL auth/ACL wiring..."
compose up -d --build emqx mysql redis auth-service device-service data-service local-device-simulator

wait_for_http "$AUTH_BASE_URL/health" "auth-service"
wait_for_http "$DEVICE_BASE_URL/health" "device-service"
wait_for_http "$DATA_BASE_URL/api/v1/data/health" "data-service"
assert_emqx_security_runtime

email="$(env_value BOOTSTRAP_SUPER_ADMIN_EMAIL)"
password="$(env_value BOOTSTRAP_SUPER_ADMIN_PASSWORD)"

login_body="$tmpdir/login.json"
cat >"$login_body" <<EOF
{"email":"$email","password":"$password"}
EOF

login_response="$tmpdir/login_response.json"
curl -fsS \
  -H "Content-Type: application/json" \
  -d @"$login_body" \
  "$AUTH_BASE_URL/api/v1/auth/login" \
  >"$login_response"

access_token="$(extract_json_field access_token "$login_response")"

wait_for_latest_telemetry() {
  latest_response="$1"
  retries="${2:-30}"
  i=0
  while [ "$i" -lt "$retries" ]; do
    if curl -fsS \
      -H "Authorization: Bearer $access_token" \
      -H "X-Target-Tenant-Id: $TENANT_ID" \
      "$DATA_BASE_URL/api/v1/data/telemetry/$SIMULATOR_DEVICE_ID/latest" \
      >"$latest_response"
    then
      if python3 - "$latest_response" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

item = ((payload.get("data") or {}).get("item")) if isinstance(payload, dict) else None
if item and item.get("device_id") and item.get("timestamp"):
    print(json.dumps(item))
    raise SystemExit(0)
raise SystemExit(1)
PY
      then
        return 0
      fi
    fi
    i=$((i + 1))
    sleep 2
  done
  return 1
}

mqtt_probe() {
  mode="$1"
  username="$2"
  password="$3"
  topic="$4"
  payload_value="$5"
  docker run --rm \
    --network "$(compose_network_name)" \
    shivex-main-data-service \
    python - "$mode" "$username" "$password" "$topic" "$payload_value" <<'PY'
import sys
import time
import uuid

import paho.mqtt.client as mqtt

mode, username, password, topic, payload = sys.argv[1:]
state = {
    "connected": False,
    "connect_rc": None,
    "published": False,
    "unexpected_disconnect": False,
}


def on_connect(client, userdata, flags, rc):
    state["connect_rc"] = rc
    state["connected"] = rc == 0


def on_publish(client, userdata, mid):
    state["published"] = True


def on_disconnect(client, userdata, rc):
    if rc != 0:
        state["unexpected_disconnect"] = True


client = mqtt.Client(client_id=f"probe-{mode}-{uuid.uuid4().hex[:8]}")
if username:
    client.username_pw_set(username, password)
client.on_connect = on_connect
client.on_publish = on_publish
client.on_disconnect = on_disconnect
client.connect("emqx", 1883, 10)
client.loop_start()

deadline = time.time() + 10
while state["connect_rc"] is None and time.time() < deadline:
    time.sleep(0.1)

if state["connect_rc"] is None:
    client.loop_stop()
    raise SystemExit("Timed out waiting for MQTT CONNACK")

if mode == "invalid_password":
    client.loop_stop()
    raise SystemExit(0 if state["connect_rc"] != 0 else 1)

if mode == "anonymous":
    client.loop_stop()
    raise SystemExit(0 if state["connect_rc"] != 0 else 1)

if state["connect_rc"] != 0:
    client.loop_stop()
    raise SystemExit(f"MQTT connection failed with rc={state['connect_rc']}")

info = client.publish(topic, payload=payload, qos=1)
info.wait_for_publish(timeout=5)

observe_until = time.time() + 3
while time.time() < observe_until and not state["unexpected_disconnect"]:
    time.sleep(0.1)

client.loop_stop()

if mode == "success":
    raise SystemExit(0 if state["published"] and not state["unexpected_disconnect"] else 1)

if mode == "wrong_topic":
    raise SystemExit(0 if state["unexpected_disconnect"] else 1)

if mode == "unspecified_topic":
    raise SystemExit(0 if state["unexpected_disconnect"] else 1)

raise SystemExit(f"Unknown mode: {mode}")
PY
}

latest_response="$tmpdir/latest_telemetry.json"
echo "Waiting for simulator telemetry ingestion on MQTT 1883..."
if ! wait_for_latest_telemetry "$latest_response" 45; then
  echo "Timed out waiting for simulator telemetry to reach data-service over MQTT 1883." >&2
  docker logs --tail 120 "$SIMULATOR_CONTAINER" >&2 || true
  cat "$latest_response" >&2 || true
  exit 1
fi
echo "Simulator telemetry ingestion on MQTT 1883 confirmed."

probe_response="$tmpdir/probe_credential_response.json"
probe_status="201"
if ! curl -fsS \
  -o "$probe_response" \
  -w "%{http_code}" \
  -H "Authorization: Bearer $access_token" \
  -H "X-Target-Tenant-Id: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{}' \
  "$DEVICE_BASE_URL/api/v1/devices/$PROBE_DEVICE_ID/mqtt-credential/register" \
  >"$tmpdir/probe_register_status.txt"; then
  probe_status="$(cat "$tmpdir/probe_register_status.txt" 2>/dev/null || printf '000')"
  if [ "$probe_status" = "409" ]; then
    echo "Probe credential already existed; rotating instead for fresh one-time secret..."
    curl -fsS \
      -H "Authorization: Bearer $access_token" \
      -H "X-Target-Tenant-Id: $TENANT_ID" \
      -H "Content-Type: application/json" \
      -d '{}' \
      "$DEVICE_BASE_URL/api/v1/devices/$PROBE_DEVICE_ID/mqtt-credential/rotate" \
      >"$probe_response"
  else
    echo "Probe credential registration failed with HTTP $probe_status" >&2
    cat "$probe_response" >&2 || true
    exit 1
  fi
fi

mqtt_username="$(extract_json_field data.credential.mqtt_username "$probe_response")"
mqtt_password="$(extract_json_field data.mqtt_password "$probe_response")"
publish_topic="$(extract_json_field data.credential.publish_topic "$probe_response")"
other_topic="$TENANT_ID/devices/OTHERDEVICE01/telemetry"
unspecified_topic="$TENANT_ID/commands/$PROBE_DEVICE_ID"
payload='{"device_id":"'"$PROBE_DEVICE_ID"'","timestamp":"2026-04-23T00:00:00Z","schema_version":"v1","power":123.4}'

echo "Validating broker auth success on 1883..."
mqtt_probe success "$mqtt_username" "$mqtt_password" "$publish_topic" "$payload"

echo "Validating wrong password rejection..."
if mqtt_probe invalid_password "$mqtt_username" "${mqtt_password}-wrong" "$publish_topic" "$payload"; then
  :
else
  echo "Expected invalid password publish to fail, but it succeeded." >&2
  exit 1
fi

echo "Validating anonymous client rejection..."
if mqtt_probe anonymous "" "" "$publish_topic" "$payload"; then
  :
else
  echo "Expected anonymous MQTT connect to fail, but it succeeded." >&2
  exit 1
fi

echo "Validating wrong-topic publish denial..."
if mqtt_probe wrong_topic "$mqtt_username" "$mqtt_password" "$other_topic" "$payload"; then
  :
else
  echo "Expected wrong-topic publish to be denied, but it succeeded." >&2
  exit 1
fi

echo "Validating unspecified-topic publish denial..."
if mqtt_probe unspecified_topic "$mqtt_username" "$mqtt_password" "$unspecified_topic" "$payload"; then
  :
else
  echo "Expected unspecified-topic publish to be denied, but it succeeded." >&2
  exit 1
fi

health_response="$tmpdir/data_health.json"
curl -fsS "$DATA_BASE_URL/api/v1/data/health" >"$health_response"

python3 - "$health_response" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)

status = data.get("status") or data.get("data", {}).get("status")
components = data.get("components") or data.get("data", {}).get("components") or {}
checks = data.get("checks") or data.get("data", {}).get("checks") or {}
mqtt_state = components.get("mqtt") or checks.get("mqtt")

print(f"data_service_status={status}")
print(f"data_service_mqtt={mqtt_state}")
PY

echo "Latest ingested telemetry sample:"
cat "$latest_response"

echo "Validation completed successfully."
