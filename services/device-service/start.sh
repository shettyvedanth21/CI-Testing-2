#!/bin/sh
set -e

echo "Running device-service migration guard..."
python scripts/migration_guard.py

DEVICE_SERVICE_RUNTIME="${DEVICE_SERVICE_RUNTIME:-api}"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

if [ "$DEVICE_SERVICE_RUNTIME" = "scheduler" ]; then
  echo "Starting device-service scheduler runtime..."
  exec python -m app.scheduler_runner
fi

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

echo "Starting device-service..."
exec uvicorn app:app --host 0.0.0.0 --port 8000 --workers "$UVICORN_WORKERS"
