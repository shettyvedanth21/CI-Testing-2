#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/test_layers.sh
source "${SCRIPT_DIR}/lib/test_layers.sh"

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

ensure_validation_runtime
PYTHON_BIN="$(resolve_repo_python)"
ensure_python_modules "${PYTHON_BIN}" pytest pydantic pytest_asyncio httpx sqlalchemy

REPO="${TEST_LAYERS_ROOT}"

run_step "Device-service parity invariants" \
  env JWT_SECRET_KEY=test-secret \
      PYTHONPATH="${REPO}/services/device-service:${REPO}/services" \
      "${PYTHON_BIN}" -m pytest \
      services/device-service/tests/test_live_dashboard_summary.py \
      services/device-service/tests/test_device_loss_stats.py \
      -q

run_step "Energy-service parity invariants" \
  env PYTHONPATH="${REPO}/services/energy-service:${REPO}/services" \
      "${PYTHON_BIN}" -m pytest \
      services/energy-service/tests/test_device_range_live_overlay.py \
      -q

run_step "Energy cost alignment parity" \
  env PYTHONPATH="${REPO}" \
      "${PYTHON_BIN}" -m pytest \
      tests/test_energy_service_cost_alignment.py \
      -q

run_step "Reporting-service parity invariants" \
  env PYTHONPATH="${REPO}/services/reporting-service:${REPO}/services" \
      "${PYTHON_BIN}" -m pytest \
      services/reporting-service/tests/test_report_task_tariff_warning.py \
      -q

run_step "Waste-analysis parity invariants" \
  env PYTHONPATH="${REPO}/services/waste-analysis-service:${REPO}/services" \
      "${PYTHON_BIN}" -m pytest \
      services/waste-analysis-service/tests/test_waste_historical_loss_parity.py \
      -q

run_step "Cross-service current-day truth parity" \
  env PYTHONPATH="${REPO}" \
      "${PYTHON_BIN}" -m pytest \
      tests/api/test_current_day_truth_parity.py \
      -q

log_section "Truth Parity Gate completed successfully"
