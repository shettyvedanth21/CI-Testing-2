#!/usr/bin/env bash

set -euo pipefail

TEST_LAYERS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_LAYERS_UI_ROOT="${TEST_LAYERS_ROOT}/ui-web"
TEST_LAYERS_VALIDATION_VENV="${TEST_LAYERS_ROOT}/.validation-venv"
TEST_LAYERS_VALIDATION_SENTINELS=(
  "pytest"
  "fastapi"
  "pydantic"
  "pytest_asyncio"
  "httpx"
  "paho.mqtt.client"
  "pymysql"
  "boto3"
  "numpy"
  "influxdb_client"
  "email_validator"
  "prometheus_client"
)

log_section() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

resolve_repo_python() {
  if [[ -n "${CERTIFY_PYTHON:-}" && -x "${CERTIFY_PYTHON}" ]]; then
    printf '%s\n' "${CERTIFY_PYTHON}"
    return
  fi

  local candidates=(
    "${TEST_LAYERS_ROOT}/.validation-venv/bin/python"
    "${TEST_LAYERS_ROOT}/.venv/bin/python"
    "${TEST_LAYERS_ROOT}/.venv-reporting/bin/python"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi

  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi

  die "Unable to find a Python interpreter for the test orchestration scripts."
}

ensure_validation_runtime() {
  local bootstrap_script="${TEST_LAYERS_ROOT}/scripts/bootstrap-validation-runtime.sh"
  if [[ ! -x "${TEST_LAYERS_VALIDATION_VENV}/bin/python" ]]; then
    run_step "Bootstrap validation runtime" "${bootstrap_script}"
    return
  fi

  if ! python_has_modules "${TEST_LAYERS_VALIDATION_VENV}/bin/python" "${TEST_LAYERS_VALIDATION_SENTINELS[@]}"; then
    run_step "Bootstrap validation runtime" "${bootstrap_script}"
  fi
}

python_has_modules() {
  local python_bin="$1"
  shift
  local module_list=("$@")
  local imports
  imports="$(printf '%s,' "${module_list[@]}")"
  imports="${imports%,}"

  "${python_bin}" - <<PY >/dev/null 2>&1
import importlib
missing = []
for name in "${imports}".split(","):
    if not name:
        continue
    try:
        importlib.import_module(name)
    except Exception:
        missing.append(name)
if missing:
    raise SystemExit(",".join(missing))
PY
}

ensure_python_modules() {
  local python_bin="$1"
  shift
  local module_list=("$@")
  local imports
  imports="$(printf '%s,' "${module_list[@]}")"
  imports="${imports%,}"

  if ! python_has_modules "${python_bin}" "${module_list[@]}"; then
    die "Python runtime ${python_bin} is missing required modules (${imports}). Create a repo venv or let Jenkins bootstrap .validation-venv first."
  fi
}

ensure_node_runtime() {
  command -v node >/dev/null 2>&1 || die "node is required for ui-web checks."
  command -v npm >/dev/null 2>&1 || die "npm is required for ui-web checks."
}

ensure_ui_dependencies() {
  [[ -d "${TEST_LAYERS_UI_ROOT}/node_modules" ]] || die "ui-web/node_modules is missing. Run 'cd ui-web && npm ci' first."
}

run_step() {
  local label="$1"
  shift
  log_section "${label}"
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

append_env_json() {
  local python_bin="$1"
  local json_file="$2"
  local export_name="$3"
  export "${export_name}"="$("${python_bin}" - <<PY
from pathlib import Path
print(Path("${json_file}").read_text(encoding="utf-8"))
PY
)"
}

# Local-only suites intended to fail fast before we spend time on a live stack.
# Keep this list to tests that are import-stable from the repo root without
# cross-service PYTHONPATH switching; the broader service and live-stack suites
# are exercised by the smoke and full certification layers.
FAST_CHECKS_PYTEST_TARGETS=(
  "scripts/tests"
  "tests/test_e2e_analytics_runtime_hygiene.py"
  "tests/test_tenant_context_resolution.py"
  "tests/test_shared_tenant_guardrails.py"
  "tests/test_energy_accounting.py"
  "tests/test_reporting_overtime.py"
  "tests/test_copilot_tariff_client.py"
)

# Parity test targets run by scripts/run-truth-parity-gate.sh.
# Each entry requires its own PYTHONPATH context (app.*/src.* namespace conflicts).
# Do NOT add these to FAST_CHECKS_PYTEST_TARGETS.
TRUTH_PARITY_GATE_TARGETS=(
  "services/device-service/tests/test_live_dashboard_summary.py"
  "services/device-service/tests/test_device_loss_stats.py"
  "services/energy-service/tests/test_device_range_live_overlay.py"
  "tests/test_energy_service_cost_alignment.py"
  "services/reporting-service/tests/test_report_task_tariff_warning.py"
  "services/waste-analysis-service/tests/test_waste_historical_loss_parity.py"
  "tests/api/test_current_day_truth_parity.py"
)
