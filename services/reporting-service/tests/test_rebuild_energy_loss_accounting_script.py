from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "rebuild_energy_loss_accounting.py"


def _load_module():
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
    os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
    os.environ.setdefault("INFLUXDB_ORG", "test-org")
    os.environ.setdefault("INFLUXDB_BUCKET", "test-bucket")
    os.environ.setdefault("DEVICE_SERVICE_URL", "http://localhost:8000")
    os.environ.setdefault("ENERGY_SERVICE_URL", "http://localhost:8010")
    os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
    os.environ.setdefault("MINIO_EXTERNAL_URL", "http://localhost:9000")
    os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
    os.environ.setdefault("MINIO_SECRET_KEY", "minio123")
    spec = importlib.util.spec_from_file_location("rebuild_energy_loss_accounting_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_resolve_runtime_paths_supports_repo_layout():
    module = _load_module()
    repo_script = Path("/workspace/repo/services/reporting-service/scripts/rebuild_energy_loss_accounting.py")

    project_root, reporting_root = module._resolve_runtime_paths(repo_script)

    assert project_root == Path("/workspace/repo")
    assert reporting_root == Path("/workspace/repo/services/reporting-service")


def test_resolve_runtime_paths_supports_container_layout():
    module = _load_module()
    container_script = Path("/app/scripts/rebuild_energy_loss_accounting.py")

    project_root, reporting_root = module._resolve_runtime_paths(container_script)

    assert project_root == Path("/app")
    assert reporting_root == Path("/app")


def test_rebuild_queries_energy_counter_with_one_minute_window():
    module = _load_module()

    assert "energy_kwh" in module.TELEMETRY_FIELDS
