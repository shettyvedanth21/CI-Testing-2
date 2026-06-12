#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/test_layers.sh
source "${SCRIPT_DIR}/lib/test_layers.sh"

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

ensure_validation_runtime
PYTHON_BIN="$(resolve_repo_python)"
export CERTIFY_PYTHON="${PYTHON_BIN}"

ensure_python_modules "${PYTHON_BIN}" pytest fastapi pydantic pytest_asyncio httpx paho.mqtt.client pymysql boto3 numpy influxdb_client email_validator prometheus_client
ensure_node_runtime
ensure_ui_dependencies

RELEASE_CERTIFICATION_MODE="${RELEASE_CERTIFICATION_MODE:-quick}"

if [[ "${SKIP_FAST_CHECKS:-0}" != "1" ]]; then
  run_step "Nested fast checks" "${TEST_LAYERS_ROOT}/scripts/run-fast-checks.sh"
fi

if [[ "${SKIP_SMOKE_E2E:-0}" != "1" ]]; then
  run_step "Nested smoke E2E" "${TEST_LAYERS_ROOT}/scripts/run-smoke-e2e.sh"
fi

if [[ "${SKIP_PREPROD_VALIDATION:-0}" != "1" ]]; then
  run_step \
    "Full live validation" \
    "${PYTHON_BIN}" "${TEST_LAYERS_ROOT}/scripts/preprod_validation.py" --mode full-validation
fi

if [[ "${SKIP_RELEASE_CERTIFICATION:-0}" != "1" ]]; then
  run_step \
    "Release certification contracts" \
    "${PYTHON_BIN}" "${TEST_LAYERS_ROOT}/scripts/certify_release_contracts.py" --mode "${RELEASE_CERTIFICATION_MODE}"
fi

log_section "Full certification completed successfully"
