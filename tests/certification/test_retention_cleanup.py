from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    ordered_paths = [str(ROOT), str(path.parent.parent.parent), str(path.parent.parent), str(path.parent)]
    remainder = [entry for entry in sys.path if entry not in ordered_paths]
    sys.path[:] = ordered_paths + remainder
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_export_checkpoint_retention_deletes_only_latest_referenced_artifacts():
    module = _load_module("data_export_retention", "services/data-export-service/retention.py")

    deleted_keys: list[str] = []

    class FakeCheckpointStore:
        async def list_checkpoints_for_retention(self, *, updated_before, limit):
            return [
                {"id": 1, "s3_key": "datasets/dev-a/old.parquet"},
                {"id": 2, "s3_key": "datasets/dev-a/shared.parquet"},
            ]

        async def is_latest_reference_for_key(self, *, checkpoint_id, s3_key):
            return checkpoint_id == 1

        async def delete_checkpoints_by_ids(self, checkpoint_ids):
            assert checkpoint_ids == [1, 2]
            return 2

    class FakeS3Writer:
        async def delete_object_if_exists(self, s3_key):
            deleted_keys.append(s3_key)

    worker = SimpleNamespace(
        settings=SimpleNamespace(
            checkpoint_retention_days=30,
            checkpoint_retention_batch_size=50,
        ),
        checkpoint_store=FakeCheckpointStore(),
        s3_writer=FakeS3Writer(),
    )

    summary = await module.apply_checkpoint_retention(worker)

    assert summary["deleted_rows"] == 2
    assert summary["deleted_artifacts"] == 1
    assert deleted_keys == ["datasets/dev-a/old.parquet"]


@pytest.mark.asyncio
async def test_analytics_retention_calls_job_and_artifact_cleanup_once(monkeypatch):
    sys.modules.setdefault("services", types.ModuleType("services"))
    sys.modules.setdefault("services.shared", types.ModuleType("services.shared"))
    tenant_context_module = types.ModuleType("services.shared.tenant_context")
    tenant_context_module.TenantContext = object
    sys.modules["services.shared.tenant_context"] = tenant_context_module
    module = _load_module("analytics_retention", "services/analytics-service/src/services/retention.py")

    calls: dict[str, object] = {}

    class FakeRepo:
        async def purge_terminal_jobs_older_than(self, *, cutoff, batch_size):
            calls["job_cutoff"] = cutoff
            calls["job_batch_size"] = batch_size
            return 3

        async def purge_expired_model_artifacts(self, *, now, grace_period_hours, batch_size):
            calls["artifact_now"] = now
            calls["artifact_grace_period_hours"] = grace_period_hours
            calls["artifact_batch_size"] = batch_size
            return 2

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            job_retention_days=90,
            retention_batch_size=500,
            artifact_retention_grace_hours=24,
        ),
    )
    monkeypatch.setattr(module, "async_session_maker", lambda: FakeSessionContext())
    monkeypatch.setattr(module, "MySQLResultRepository", lambda session: FakeRepo())

    summary = await module.apply_retention()

    assert summary["deleted_jobs"] == 3
    assert summary["deleted_artifacts"] == 2
    assert calls["job_batch_size"] == 500
    assert calls["artifact_grace_period_hours"] == 24
