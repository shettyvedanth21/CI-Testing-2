#!/bin/sh
set -e

echo "Running rule-engine migration guard..."
python scripts/migration_guard.py

echo "Starting rule-engine service..."
if [ "${APP_ROLE:-api}" = "worker" ]; then
  exec python -m app.worker_main
fi

exec uvicorn app:app --host 0.0.0.0 --port 8002
