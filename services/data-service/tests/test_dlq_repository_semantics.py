from __future__ import annotations

from datetime import datetime, timezone

from tests._bootstrap import bootstrap_paths

bootstrap_paths()

from src.config import settings
from src.repositories.dlq_repository import DLQRepository


class _CaptureBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send(self, entry, *, initial_status: str = "pending", dead_reason=None):  # noqa: ANN001
        self.calls.append(
            {
                "entry": entry,
                "initial_status": initial_status,
                "dead_reason": dead_reason,
            }
        )
        return True

    def get_operational_stats(self):  # noqa: ANN201
        return {}

    def fetch_pending_retries(self, **kwargs):  # noqa: ANN003, ANN201
        return []

    def mark_retry_reprocessed(self, **kwargs):  # noqa: ANN003
        return None

    def mark_retry_failed(self, **kwargs):  # noqa: ANN003
        return "pending"

    def mark_dead_without_retry_increment(self, **kwargs):  # noqa: ANN003
        return "dead"

    def purge_expired(self, *, created_before: datetime, batch_size: int):  # noqa: ARG002
        return 0

    def reclassify_non_retryable_pending(self, *, retryable_error_types, batch_size):  # noqa: ANN001, ARG002
        return 0

    def close(self) -> None:
        return None


def test_send_defaults_non_retryable_errors_to_dead(monkeypatch):
    monkeypatch.setattr(settings, "dlq_retryable_error_types", ["parse_error"])
    backend = _CaptureBackend()
    repo = DLQRepository(backend=backend)

    repo.send(
        original_payload={"device_id": "D-1"},
        error_type="rule_engine_circuit_open",
        error_message="circuit open",
    )

    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["initial_status"] == "dead"
    assert call["dead_reason"] == "circuit open"


def test_send_defaults_retryable_errors_to_pending(monkeypatch):
    monkeypatch.setattr(settings, "dlq_retryable_error_types", ["parse_error"])
    backend = _CaptureBackend()
    repo = DLQRepository(backend=backend)

    repo.send(
        original_payload={"device_id": "D-2"},
        error_type="parse_error",
        error_message="bad payload",
    )

    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["initial_status"] == "pending"
    assert call["dead_reason"] is None

