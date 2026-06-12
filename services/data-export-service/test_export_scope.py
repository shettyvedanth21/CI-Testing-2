import sys
from types import SimpleNamespace

import pytest

sys.modules.setdefault("aioboto3", SimpleNamespace(Session=lambda **_kwargs: None))

from worker import ExportWorker


class _FakeExporter:
    def __init__(self):
        self.calls = []

    async def export_device_data(self, device_id, **kwargs):
        self.calls.append((device_id, kwargs))
        return SimpleNamespace(record_count=0, success=True)


@pytest.mark.asyncio
async def test_force_export_can_be_limited_to_tenant_scoped_device_list():
    worker = ExportWorker.__new__(ExportWorker)
    worker._device_ids = ["TENANT-A-1", "TENANT-B-1"]
    worker.exporter = _FakeExporter()

    await worker.force_export(device_ids=["TENANT-A-1"])

    assert [device_id for device_id, _kwargs in worker.exporter.calls] == ["TENANT-A-1"]


@pytest.mark.asyncio
async def test_force_export_rejects_ambiguous_single_and_multiple_device_scope():
    worker = ExportWorker.__new__(ExportWorker)
    worker._device_ids = ["TENANT-A-1"]
    worker.exporter = _FakeExporter()

    with pytest.raises(ValueError):
        await worker.force_export(device_id="TENANT-A-1", device_ids=["TENANT-A-1"])
