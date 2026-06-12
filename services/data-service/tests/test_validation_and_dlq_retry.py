from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from tests._bootstrap import bootstrap_paths

bootstrap_paths()

from src.models import DeviceMetadata
from src.services.dlq_retry_service import DLQRetryService
from src.utils.validation import TelemetryValidator


def _enriched_payload() -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "device_id": "DEVICE-ENRICH-1",
        "timestamp": now,
        "tenant_id": "tenant-a",
        "tenant_id": "tenant-a",
        "schema_version": "v1",
        "power": 123.4,
        "current": 1.2,
        "enrichment_status": "success",
        "device_metadata": DeviceMetadata(
            id="DEVICE-ENRICH-1",
            name="Machine 1",
            type="meter",
            status="active",
        ).model_dump(mode="json"),
        "enriched_at": now,
    }


def test_validator_accepts_enriched_retry_payload():
    is_valid, error_type, error_message = TelemetryValidator.validate_payload(_enriched_payload())

    assert is_valid is True
    assert error_type is None
    assert error_message is None


class FakeDLQRepository:
    def __init__(self, rows: list[dict[str, object]]):
        self.rows = rows
        self.fetch_kwargs: dict[str, object] | None = None
        self.retry_failed_kwargs: dict[str, object] | None = None
        self.retry_reprocessed_kwargs: dict[str, object] | None = None
        self.dead_kwargs: dict[str, object] | None = None

    def fetch_pending_retries(self, **kwargs):
        self.fetch_kwargs = kwargs
        return list(self.rows)

    def retryable_error_types(self):
        return (
            "invalid_numeric_fields",
            "influxdb_write_error",
            "outbox_enqueue_error",
            "parse_error",
            "processing_error",
            "unexpected_error",
        )

    def mark_retry_reprocessed(self, **kwargs):
        self.retry_reprocessed_kwargs = kwargs

    def mark_retry_failed(self, **kwargs):
        self.retry_failed_kwargs = kwargs
        max_retry_count = int(kwargs.get("max_retry_count") or 5)
        retry_count = int(kwargs["retry_count"])
        return "dead" if retry_count >= max_retry_count else "pending"

    def mark_dead_without_retry_increment(self, **kwargs):
        self.dead_kwargs = kwargs
        return "dead"


class FakeTelemetryService:
    def __init__(self, accepted: bool = True):
        self.accepted = accepted
        self.calls: list[dict[str, object]] = []

    async def process_telemetry_message(self, payload, correlation_id=None):
        self.calls.append({"payload": payload, "correlation_id": correlation_id})
        return self.accepted


@pytest.mark.asyncio
async def test_dlq_retry_filters_to_retryable_error_types_and_uses_configured_limit():
    repo = FakeDLQRepository(
        rows=[
            {
                "id": 1,
                "retry_count": 0,
                "error_type": "invalid_numeric_fields",
                "original_payload": _enriched_payload(),
            }
        ]
    )
    service = DLQRetryService(
        telemetry_service=FakeTelemetryService(accepted=False),
        dlq_repository=repo,  # type: ignore[arg-type]
        max_retry_count=3,
        retry_grace_period=timedelta(seconds=0),
        batch_limit=10,
        base_backoff_seconds=1,
    )

    processed = await service._process_batch()

    assert processed == 1
    assert repo.fetch_kwargs is not None
    assert "invalid_numeric_fields" in repo.fetch_kwargs["error_types"]
    assert "outbox_delivery_dead" not in repo.fetch_kwargs["error_types"]
    assert repo.retry_failed_kwargs is not None
    assert repo.retry_failed_kwargs["max_retry_count"] == 3
    assert repo.retry_failed_kwargs["retry_count"] == 1


@pytest.mark.asyncio
async def test_dlq_retry_reprocesses_enriched_payload_successfully():
    repo = FakeDLQRepository(
        rows=[
            {
                "id": 2,
                "retry_count": 0,
                "error_type": "invalid_numeric_fields",
                "original_payload": _enriched_payload(),
            }
        ]
    )
    telemetry_service = FakeTelemetryService(accepted=True)
    service = DLQRetryService(
        telemetry_service=telemetry_service,
        dlq_repository=repo,  # type: ignore[arg-type]
        max_retry_count=3,
        retry_grace_period=timedelta(seconds=0),
        batch_limit=10,
        base_backoff_seconds=1,
    )

    processed = await service._process_batch()

    assert processed == 1
    assert telemetry_service.calls
    assert repo.retry_reprocessed_kwargs is not None
    assert repo.retry_reprocessed_kwargs["retry_count"] == 1
    assert repo.retry_failed_kwargs is None


@pytest.mark.asyncio
async def test_dlq_retry_extracts_inner_telemetry_for_outbox_delivery_dead():
    inner_payload = _enriched_payload()
    repo = FakeDLQRepository(
        rows=[
            {
                "id": 3,
                "retry_count": 0,
                "error_type": "outbox_delivery_dead",
                "original_payload": {
                    "tenant_id": "tenant-a",
                    "telemetry": inner_payload,
                    "attempt": 5,
                },
            }
        ]
    )
    telemetry_service = FakeTelemetryService(accepted=True)
    service = DLQRetryService(
        telemetry_service=telemetry_service,
        dlq_repository=repo,  # type: ignore[arg-type]
        max_retry_count=3,
        retry_grace_period=timedelta(seconds=0),
        batch_limit=10,
        base_backoff_seconds=1,
    )

    await service._retry_row(repo.rows[0])

    assert telemetry_service.calls[0]["payload"] == inner_payload
    assert repo.retry_reprocessed_kwargs is not None
