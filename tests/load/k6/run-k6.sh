#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="${1:-${K6_SCENARIO:-mixed}}"

load_env_defaults() {
  local env_file="$1"
  local line
  local key
  local value

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line}" ]] && continue
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" != *"="* ]] && continue

    key="${line%%=*}"
    value="${line#*=}"

    key="$(printf '%s' "${key}" | xargs)"
    [[ -z "${key}" ]] && continue

    if [[ -z "${!key+x}" ]]; then
      if [[ "${value}" =~ ^\".*\"$ || "${value}" =~ ^\'.*\'$ ]]; then
        value="${value:1:${#value}-2}"
      fi
      export "${key}=${value}"
    fi
  done < "${env_file}"
}

if [[ -f "${ROOT_DIR}/.env" ]]; then
  # Load file values only as defaults so explicit CLI env vars always win.
  load_env_defaults "${ROOT_DIR}/.env"
fi

if ! command -v k6 >/dev/null 2>&1; then
  echo "k6 is not installed. Install k6 or run the scripts on the disposable test server later." >&2
  exit 127
fi

case "${SCENARIO}" in
  analytics|reports|waste|rules-alerts|mixed) ;;
  *)
    echo "Unknown scenario '${SCENARIO}'. Expected one of: analytics, reports, waste, rules-alerts, mixed." >&2
    exit 2
    ;;
esac

SCRIPT_PATH="${ROOT_DIR}/scenarios/${SCENARIO}.js"
shift || true
exec k6 run "${SCRIPT_PATH}" "$@"
