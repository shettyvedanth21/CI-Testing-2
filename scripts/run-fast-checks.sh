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

ensure_python_modules "${PYTHON_BIN}" pytest fastapi pydantic pytest_asyncio httpx jose sqlalchemy redis aiomysql
ensure_node_runtime
ensure_ui_dependencies

run_step \
  "Python fast checks" \
  "${PYTHON_BIN}" -m pytest "${FAST_CHECKS_PYTEST_TARGETS[@]}" -q

run_step \
  "UI typecheck" \
  bash -lc "cd '${TEST_LAYERS_UI_ROOT}' && npx tsc --noEmit"

run_step \
  "UI unit tests" \
  bash -lc "cd '${TEST_LAYERS_UI_ROOT}' && npm run test:unit"

log_section "Fast checks completed successfully"
