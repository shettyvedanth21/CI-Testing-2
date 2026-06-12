#!/bin/bash
set -e

echo "[auth-service] Running migration guard..."
python scripts/migration_guard.py

LOG_LEVEL_LOWER="$(printf '%s' "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

case "$UVICORN_WORKERS" in
    ''|*[!0-9]*)
        echo "[auth-service] Invalid UVICORN_WORKERS value: '$UVICORN_WORKERS'. Expected a positive integer." >&2
        exit 1
        ;;
esac

if [ "$UVICORN_WORKERS" -lt 1 ]; then
    echo "[auth-service] Invalid UVICORN_WORKERS value: '$UVICORN_WORKERS'. Expected a positive integer." >&2
    exit 1
fi

if [ "${DEBUGPY_ENABLE:-false}" = "true" ] && [ "$UVICORN_WORKERS" -gt 1 ]; then
    echo "[auth-service] DEBUGPY_ENABLE=true is incompatible with multi-worker uvicorn startup. Set UVICORN_WORKERS=1 or disable debugpy." >&2
    exit 1
fi

echo "[auth-service] Starting Uvicorn on ${SERVICE_HOST:-0.0.0.0}:${SERVICE_PORT:-8090}..."
exec uvicorn app.main:app \
    --host "${SERVICE_HOST:-0.0.0.0}" \
    --port "${SERVICE_PORT:-8090}" \
    --log-level "${LOG_LEVEL_LOWER}" \
    --workers "${UVICORN_WORKERS}"
