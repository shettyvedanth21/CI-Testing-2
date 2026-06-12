#!/usr/bin/env bash

set -euo pipefail

WAIT_FOR_REDIS="${WAIT_FOR_REDIS:-0}"
WAIT_FOR_MYSQL="${WAIT_FOR_MYSQL:-0}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-90}"

if [[ "${WAIT_FOR_REDIS}" != "1" && "${WAIT_FOR_MYSQL}" != "1" ]]; then
  exit 0
fi

python3 - <<'PY'
import os
import socket
import sys
import time

timeout = float(os.environ.get("WAIT_TIMEOUT_SECONDS", "90"))
checks: list[tuple[str, str, int]] = []

if os.environ.get("WAIT_FOR_REDIS") == "1":
    checks.append(("redis", "127.0.0.1", 6379))

if os.environ.get("WAIT_FOR_MYSQL") == "1":
    checks.append(("mysql", os.environ.get("MYSQL_HOST", "127.0.0.1"), int(os.environ.get("MYSQL_PORT", "3306"))))

deadline = time.monotonic() + timeout

for name, host, port in checks:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"{name} ready on {host}:{port}")
                break
        except OSError as exc:
            last_error = exc
            time.sleep(1)
    else:
        print(f"Timed out waiting for {name} on {host}:{port}: {last_error}", file=sys.stderr)
        sys.exit(1)
PY
