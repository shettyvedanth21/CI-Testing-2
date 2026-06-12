#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${VALIDATION_VENV_DIR:-${REPO_ROOT}/.validation-venv}"
REQUIREMENTS_FILE="${REPO_ROOT}/requirements-validation.txt"

resolve_bootstrap_python() {
  if [[ -n "${VALIDATION_BOOTSTRAP_PYTHON:-}" ]]; then
    printf '%s\n' "${VALIDATION_BOOTSTRAP_PYTHON}"
    return
  fi

  local candidates=(
    "python3.11"
    "python3.10"
    "python3.12"
    "python3"
    "python"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return
    fi
  done

  printf 'ERROR: No usable Python interpreter found for validation runtime bootstrap.\n' >&2
  exit 1
}

PYTHON_BIN="$(resolve_bootstrap_python)"

if [[ ! -f "${REQUIREMENTS_FILE}" ]]; then
  printf 'ERROR: Missing validation requirements file at %s.\n' "${REQUIREMENTS_FILE}" >&2
  exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install -r "${REQUIREMENTS_FILE}"
