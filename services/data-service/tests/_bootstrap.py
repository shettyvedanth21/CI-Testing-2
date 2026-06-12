"""Shared test path bootstrap for data-service tests."""

from __future__ import annotations

import sys
import os
from pathlib import Path


def bootstrap_paths() -> None:
    current = Path(__file__).resolve()
    candidates = [current.parent]
    candidates.extend(current.parents)
    for candidate in candidates:
        if (candidate / "services").exists():
            project_root = candidate
            services_dir = candidate / "services"
            data_service_dir = services_dir / "data-service"
            for path in (project_root, services_dir, data_service_dir):
                path_str = str(path)
                if path_str not in sys.path:
                    sys.path.insert(0, path_str)
            return


bootstrap_paths()

os.environ.setdefault(
    "INTERNAL_SERVICE_SHARED_SECRET",
    "test-internal-service-secret-at-least-32-chars",
)
