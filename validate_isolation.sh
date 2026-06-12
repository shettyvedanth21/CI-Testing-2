#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python "${REPO_ROOT}/scripts/validate_isolation.py" "$@"
