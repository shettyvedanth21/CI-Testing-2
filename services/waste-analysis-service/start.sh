#!/bin/sh
set -e

echo "Running waste-analysis-service migration guard..."
python scripts/migration_guard.py

echo "Starting waste-analysis-service..."
if [ "${APP_ROLE:-api}" = "worker" ]; then
  exec python -m src.worker_main
fi

UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

case "$UVICORN_WORKERS" in
  ''|*[!0-9]*)
    echo "Invalid UVICORN_WORKERS value: '$UVICORN_WORKERS'. Expected a positive integer." >&2
    exit 1
    ;;
esac

if [ "$UVICORN_WORKERS" -lt 1 ]; then
  echo "Invalid UVICORN_WORKERS value: '$UVICORN_WORKERS'. Expected a positive integer." >&2
  exit 1
fi

if [ "${DEBUGPY_ENABLE:-false}" = "true" ] && [ "$UVICORN_WORKERS" -gt 1 ]; then
  echo "DEBUGPY_ENABLE=true is incompatible with multi-worker uvicorn startup. Set UVICORN_WORKERS=1 or disable debugpy." >&2
  exit 1
fi

exec uvicorn src.main:app --host 0.0.0.0 --port 8087 --workers "$UVICORN_WORKERS"
