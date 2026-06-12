#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/test_layers.sh
source "${SCRIPT_DIR}/lib/test_layers.sh"

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

ensure_validation_runtime
if [[ -n "${VALIDATION_VENV_DIR:-}" && -x "${VALIDATION_VENV_DIR}/bin/python" ]]; then
  PYTHON_BIN="${VALIDATION_VENV_DIR}/bin/python"
else
  PYTHON_BIN="$(resolve_repo_python)"
fi
export CERTIFY_PYTHON="${PYTHON_BIN}"

run_step \
  "Broad CI validation" \
  "${PYTHON_BIN}" "${TEST_LAYERS_ROOT}/scripts/ci_broad_validation.py" "$@"

log_section "Broad CI validation completed successfully"
