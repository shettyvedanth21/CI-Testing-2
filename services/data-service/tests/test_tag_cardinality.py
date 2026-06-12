from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import EnrichmentStatus, TelemetryPayload
from src.repositories import influxdb_repository as influxdb_repo_module
from src.repositories.influxdb_repository import InfluxDBRepository


class FakePoint:
    def __init__(self, measurement: str):
        self.measurement = measurement
        self.tags: dict[str, object] = {}
        self.fields: dict[str, object] = {}
        self.timestamp = None

    def tag(self, key: str, value: object):
        self.tags[key] = value
        return self

    def field(self, key: str, value: object):
        self.fields[key] = value
        return self

    def time(self, value):
        self.timestamp = value
        return self


def _payload() -> TelemetryPayload:
    return TelemetryPayload(
        device_id="DEVICE-TAG-1",
        timestamp=datetime.now(timezone.utc),
        schema_version="v1",
        enrichment_status=EnrichmentStatus.SUCCESS,
        power=123.4,
    )


def _repo_with_mocks():
    mock_client = MagicMock()
    mock_write_api = MagicMock()
    mock_client.write_api.return_value = mock_write_api
    mock_client.query_api.return_value = MagicMock()
    mock_dlq = MagicMock()

    patches = [
        patch.object(influxdb_repo_module, "DLQRepository", return_value=mock_dlq),
        patch.object(influxdb_repo_module, "InfluxDBClient", return_value=mock_client),
        patch.object(influxdb_repo_module, "WriteOptions", return_value=MagicMock()),
        patch.object(influxdb_repo_module, "Point", FakePoint),
    ]
    return patches, mock_client, mock_write_api, mock_dlq


def test_allowed_tags_pass_through():
    patches, mock_client, mock_write_api, _ = _repo_with_mocks()
    with patches[0], patches[1], patches[2], patches[3]:
        repo = InfluxDBRepository()
        payload = _payload()
        ok = repo.write_telemetry(
            payload,
            additional_tags={"tenant_id": "tenant-a"},
        )

    assert ok is True
    record = mock_write_api.write.call_args.kwargs["record"]
    assert record.tags["device_id"] == "DEVICE-TAG-1"
    assert record.tags["tenant_id"] == "tenant-a"


def test_metadata_tags_are_not_persisted():
    patches, _, mock_write_api, _ = _repo_with_mocks()
    with patches[0], patches[1], patches[2], patches[3]:
        repo = InfluxDBRepository()
        payload = _payload()
        payload.device_metadata = MagicMock(type="compressor", location="plant-a")
        ok = repo.write_telemetry(
            payload,
            additional_tags={
                "schema_version": "v2",
                "enrichment_status": "ready",
                "device_type": "sensor-x",
                "location": "plant-a",
            },
        )

    assert ok is True
    record = mock_write_api.write.call_args.kwargs["record"]
    assert record.tags == {"device_id": "DEVICE-TAG-1"}
    assert "schema_version" not in record.fields
    assert "enrichment_status" not in record.fields
    assert "device_type_field" not in record.fields
    assert "location_field" not in record.fields


def test_unknown_tag_rejected_with_warning():
    patches, _, mock_write_api, _ = _repo_with_mocks()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patch.object(influxdb_repo_module, "logger") as mock_logger,
    ):
        repo = InfluxDBRepository()
        payload = _payload()
        ok = repo.write_telemetry(payload, additional_tags={"custom_tag": "abc"})

    assert ok is True
    record = mock_write_api.write.call_args.kwargs["record"]
    assert "custom_tag_field" not in record.fields
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["device_id"] == "DEVICE-TAG-1"
    assert mock_logger.warning.call_args.kwargs["ignored_keys"] == ["custom_tag"]


def test_parse_record_defaults_operational_metadata_when_not_stored():
    patches, _, _, _ = _repo_with_mocks()
    fake_record = MagicMock()
    fake_record.get_time.return_value = datetime(2026, 4, 8, tzinfo=timezone.utc)
    fake_record.values = {
        "device_id": "DEVICE-TAG-1",
        "tenant_id": "tenant-a",
        "power": 123.4,
    }

    with patches[0], patches[1], patches[2], patches[3]:
        repo = InfluxDBRepository()
        point = repo._parse_record_to_point(fake_record)

    assert point is not None
    assert point.device_id == "DEVICE-TAG-1"
    assert point.schema_version == "v1"
    assert point.enrichment_status == EnrichmentStatus.PENDING
    assert point.power == 123.4


@pytest.mark.asyncio
async def test_audit_runs_at_startup():
    patches, _, _, _ = _repo_with_mocks()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patch.object(InfluxDBRepository, "audit_tag_cardinality", new=AsyncMock(return_value={"device_id": 1})) as audit_mock,
    ):
        repo = InfluxDBRepository()
        await asyncio.sleep(0)

    assert repo is not None
    audit_mock.assert_awaited_once()
