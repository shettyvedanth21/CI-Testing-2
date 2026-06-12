#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
DEVICE_ID="${DEVICE_ID:-COMPRESSOR-001}"
EXPECTED_FIELDS_JSON="${EXPECTED_FIELDS_JSON:-[\"current\",\"power\"]}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd jq

echo "Verifying dashboard widget config for device: ${DEVICE_ID}"
echo "Device service: ${BASE_URL}"

echo "1) PUT selected_fields -> ${EXPECTED_FIELDS_JSON}"
PUT_BODY="$(jq -cn --argjson fields "${EXPECTED_FIELDS_JSON}" '{selected_fields:$fields}')"
PUT_RES="$(curl -fsS -X PUT "${BASE_URL}/api/v1/devices/${DEVICE_ID}/dashboard-widgets" \
  -H "Content-Type: application/json" \
  -d "${PUT_BODY}")"
echo "${PUT_RES}" | jq .

echo "2) GET loop (stability check)"
for i in $(seq 1 5); do
  RES="$(curl -fsS "${BASE_URL}/api/v1/devices/${DEVICE_ID}/dashboard-widgets")"
  SEL="$(echo "${RES}" | jq -c '.selected_fields')"
  EFF="$(echo "${RES}" | jq -c '.effective_fields')"
  DEF="$(echo "${RES}" | jq -r '.default_applied')"
  echo "  - Read ${i}: selected=${SEL} effective=${EFF} default_applied=${DEF}"
  sleep 1
done

FINAL="$(curl -fsS "${BASE_URL}/api/v1/devices/${DEVICE_ID}/dashboard-widgets")"
ACTUAL_SELECTED="$(echo "${FINAL}" | jq -c '.selected_fields')"
ACTUAL_EFFECTIVE="$(echo "${FINAL}" | jq -c '.effective_fields')"
DEFAULT_APPLIED="$(echo "${FINAL}" | jq -r '.default_applied')"

if [[ "${ACTUAL_SELECTED}" != "${EXPECTED_FIELDS_JSON}" ]]; then
  echo "FAIL: selected_fields mismatch. expected=${EXPECTED_FIELDS_JSON} actual=${ACTUAL_SELECTED}" >&2
  exit 1
fi

if [[ "${ACTUAL_EFFECTIVE}" != "${EXPECTED_FIELDS_JSON}" ]]; then
  echo "FAIL: effective_fields mismatch. expected=${EXPECTED_FIELDS_JSON} actual=${ACTUAL_EFFECTIVE}" >&2
  exit 1
fi

if [[ "${DEFAULT_APPLIED}" != "false" ]]; then
  echo "FAIL: default_applied should be false after explicit selection. actual=${DEFAULT_APPLIED}" >&2
  exit 1
fi

echo "PASS: Dashboard widget configuration is persisted and stable."
