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

run_step \
  "CI targeted validation" \
  "${PYTHON_BIN}" "${TEST_LAYERS_ROOT}/scripts/ci_targeted_validation.py"

log_section "CI targeted validation completed successfully"
