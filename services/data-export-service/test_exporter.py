from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from exporter import TelemetryExporter
from models import ExportStatus


class _FakeSettings:
    max_force_export_window_hours = 48
    export_format = "parquet"
    max_export_window_hours = 24
    lookback_hours = 24
    export_batch_size = 1000


class _FakeCheckpointRepo:
    def __init__(self):
        self.saved = []
        self.last = None

    async def get_last_checkpoint(self, device_id: str):
        return self.last

    async def save_checkpoint(self, checkpoint):
        self.saved.append(checkpoint)
        self.last = checkpoint
        return checkpoint


class _FakeDataSource:
    def __init__(self, *, count: int, records=None):
        self._count = count
        self._records = records or []

    async def count_records(self, device_id, start_time, end_time):
        return self._count

    async def query_telemetry(self, **kwargs):
        return list(self._records)


class _FakeS3Writer:
    async def write_batch(self, batch, format):
        return SimpleNamespace(file_size_bytes=123)

    def _build_s3_key(self, device_id, start_time, end_time, export_format):
        return f"datasets/{device_id}/fake.parquet"


@pytest.mark.asyncio
async def test_force_range_without_telemetry_persists_terminal_checkpoint():
    checkpoint_repo = _FakeCheckpointRepo()
    exporter = TelemetryExporter(
        settings=_FakeSettings(),
        data_source=_FakeDataSource(count=0),
        s3_writer=_FakeS3Writer(),
        checkpoint_repo=checkpoint_repo,
    )
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    end = datetime.now(timezone.utc)

    result = await exporter.export_device_data(
        "AD00000002",
        force_start_time=start,
        force_end_time=end,
    )

    assert result.success is True
    assert result.record_count == 0
    assert checkpoint_repo.saved
    checkpoint = checkpoint_repo.saved[-1]
    assert checkpoint.status == ExportStatus.FAILED
    assert checkpoint.error_message == "No telemetry found in selected range"
    assert checkpoint.last_exported_at == end


@pytest.mark.asyncio
async def test_get_export_status_includes_error_message():
    checkpoint_repo = _FakeCheckpointRepo()
    checkpoint_repo.last = SimpleNamespace(
        status=ExportStatus.FAILED,
        last_exported_at=datetime.now(timezone.utc),
        record_count=0,
        s3_key=None,
        error_message="No telemetry found in selected range",
        updated_at=datetime.now(timezone.utc),
    )
    exporter = TelemetryExporter(
        settings=_FakeSettings(),
        data_source=_FakeDataSource(count=0),
        s3_writer=_FakeS3Writer(),
        checkpoint_repo=checkpoint_repo,
    )

    status = await exporter.get_export_status("AD00000002")

    assert status["status"] == "failed"
    assert status["error_message"] == "No telemetry found in selected range"
