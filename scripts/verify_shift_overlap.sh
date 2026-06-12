#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
DEVICE_ID="${DEVICE_ID:-SHIFT-OVLP-VERIFY-001}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd jq

echo "Verifying shift overlap behavior on ${DEVICE_ID}"

echo "1) Ensure device exists"
DEVICE_GET_CODE="$(curl -sS -o /tmp/shift_verify_device_get.json -w '%{http_code}' "${BASE_URL}/api/v1/devices/${DEVICE_ID}")"
if [[ "${DEVICE_GET_CODE}" != "200" ]]; then
  DEVICE_CREATE_CODE="$(curl -sS -o /tmp/shift_verify_device_create.json -w '%{http_code}' -X POST "${BASE_URL}/api/v1/devices" \
    -H "Content-Type: application/json" \
    -d "{
      \"device_id\":\"${DEVICE_ID}\",
      \"device_name\":\"Shift Verify Device\",
      \"device_type\":\"compressor\",
      \"data_source_type\":\"metered\",
      \"phase_type\":\"three\"
    }")"
  if [[ "${DEVICE_CREATE_CODE}" != "201" ]]; then
    echo "Failed to create verification device. status=${DEVICE_CREATE_CODE}" >&2
    cat /tmp/shift_verify_device_create.json >&2 || true
    exit 1
  fi
fi

echo "2) Clear existing shifts on test device"
EXISTING_IDS="$(curl -sS "${BASE_URL}/api/v1/devices/${DEVICE_ID}/shifts" | jq -r '.data[].id')"
for id in ${EXISTING_IDS:-}; do
  curl -sS -X DELETE "${BASE_URL}/api/v1/devices/${DEVICE_ID}/shifts/${id}" >/dev/null
done

echo "3) Create baseline shift 09:00-10:00"
curl -sS -X POST "${BASE_URL}/api/v1/devices/${DEVICE_ID}/shifts" \
  -H "Content-Type: application/json" \
  -d '{"shift_name":"S1","shift_start":"09:00","shift_end":"10:00","maintenance_break_minutes":0,"is_active":true}' | jq .

echo "4) Create touching shift 10:00-11:00 (must pass)"
HTTP_TOUCH="$(curl -sS -o /tmp/shift_touch.json -w '%{http_code}' -X POST "${BASE_URL}/api/v1/devices/${DEVICE_ID}/shifts" \
  -H "Content-Type: application/json" \
  -d '{"shift_name":"S2","shift_start":"10:00","shift_end":"11:00","maintenance_break_minutes":0,"is_active":true}')"
echo "Touch status: ${HTTP_TOUCH}"
cat /tmp/shift_touch.json | jq .
test "${HTTP_TOUCH}" = "201"

echo "5) Create overlapping shift 09:30-10:30 (must fail with 409)"
HTTP_OVLP="$(curl -sS -o /tmp/shift_overlap.json -w '%{http_code}' -X POST "${BASE_URL}/api/v1/devices/${DEVICE_ID}/shifts" \
  -H "Content-Type: application/json" \
  -d '{"shift_name":"S3","shift_start":"09:30","shift_end":"10:30","maintenance_break_minutes":0,"is_active":true}')"
echo "Overlap status: ${HTTP_OVLP}"
cat /tmp/shift_overlap.json | jq .
test "${HTTP_OVLP}" = "409"

echo "6) Create cross-midnight shift 22:00-02:00"
curl -sS -X POST "${BASE_URL}/api/v1/devices/${DEVICE_ID}/shifts" \
  -H "Content-Type: application/json" \
  -d '{"shift_name":"N1","shift_start":"22:00","shift_end":"02:00","maintenance_break_minutes":0,"is_active":true}' | jq .

echo "7) Create overlapping cross-midnight shift 01:00-03:00 (must fail with 409)"
HTTP_OVLP_NIGHT="$(curl -sS -o /tmp/shift_overlap_night.json -w '%{http_code}' -X POST "${BASE_URL}/api/v1/devices/${DEVICE_ID}/shifts" \
  -H "Content-Type: application/json" \
  -d '{"shift_name":"N2","shift_start":"01:00","shift_end":"03:00","maintenance_break_minutes":0,"is_active":true}')"
echo "Overlap-night status: ${HTTP_OVLP_NIGHT}"
cat /tmp/shift_overlap_night.json | jq .
test "${HTTP_OVLP_NIGHT}" = "409"

echo "PASS: shift overlap policy enforced."
