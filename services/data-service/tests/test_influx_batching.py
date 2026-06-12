from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio  # noqa: F401


def _import_settings():
    try:
        from src.config.settings import Settings
    except Exception as exc:  # pragma: no cover - explicit failure path
        pytest.fail(f"Failed to import Settings: {exc}")
    return Settings


def _import_repository_module():
    try:
        import src.repositories.influxdb_repository as influxdb_repository
    except Exception as exc:  # pragma: no cover - explicit failure path
        pytest.fail(f"Failed to import influxdb_repository module: {exc}")
    return influxdb_repository


def _import_repository_class():
    try:
        from src.repositories.influxdb_repository import InfluxDBRepository
    except Exception as exc:  # pragma: no cover - explicit failure path
        pytest.fail(f"Failed to import InfluxDBRepository: {exc}")
    return InfluxDBRepository


def _import_payload_model():
    try:
        from src.models import TelemetryPayload
    except Exception as exc:  # pragma: no cover - explicit failure path
        pytest.fail(f"Failed to import TelemetryPayload: {exc}")
    return TelemetryPayload


def test_batch_size_config_loaded():
    Settings = _import_settings()
    config = Settings()

    assert isinstance(config.influx_batch_size, int)
    assert config.influx_batch_size > 0
    assert isinstance(config.influx_flush_interval_ms, int)
    assert config.influx_flush_interval_ms > 0
    assert isinstance(config.influx_max_retries, int)
    assert config.influx_max_retries > 0


def test_write_options_uses_batch_size():
    influxdb_repository = _import_repository_module()

    mock_client = MagicMock()
    mock_client.query_api.return_value = MagicMock()
    mock_client.write_api.return_value = MagicMock()
    mock_write_options = MagicMock(name="write_options")

    with patch.object(influxdb_repository, "DLQRepository", return_value=MagicMock()), patch.object(
        influxdb_repository, "InfluxDBClient", return_value=mock_client
    ), patch.object(influxdb_repository, "WriteOptions", return_value=mock_write_options) as write_options_cls:
        repo = influxdb_repository.InfluxDBRepository()

    write_options_cls.assert_called_once()
    _, kwargs = write_options_cls.call_args
    assert kwargs["batch_size"] == influxdb_repository.settings.influx_batch_size
    assert kwargs["flush_interval"] == influxdb_repository.settings.influx_flush_interval_ms
    assert kwargs["max_retries"] == influxdb_repository.settings.influx_max_retries
    mock_client.write_api.assert_called_once_with(
        write_options=mock_write_options,
        error_callback=repo._on_write_error,
    )


def test_error_callback_logs_on_failure():
    influxdb_repository = _import_repository_module()

    mock_client = MagicMock()
    mock_client.query_api.return_value = MagicMock()
    mock_client.write_api.return_value = MagicMock()

    with patch.object(influxdb_repository, "DLQRepository", return_value=MagicMock()), patch.object(
        influxdb_repository, "InfluxDBClient", return_value=mock_client
    ), patch.object(influxdb_repository, "logger") as mock_logger:
        repo = influxdb_repository.InfluxDBRepository()
        repo._on_write_error(
            ("telemetry", "energy-platform", "ns"),
            "device_telemetry,device_id=DEVICE-1 power=12.3 123\n",
            RuntimeError("batch failed"),
        )

    assert mock_logger.error.called


def test_write_method_signature_unchanged():
    InfluxDBRepository = _import_repository_class()
    TelemetryPayload = _import_payload_model()

    mock_write_api = MagicMock()
    mock_client = MagicMock()
    mock_client.write_api.return_value = mock_write_api
    mock_client.query_api.return_value = MagicMock()

    with patch("src.repositories.influxdb_repository.DLQRepository", return_value=MagicMock()), patch(
        "src.repositories.influxdb_repository.InfluxDBClient",
        return_value=mock_client,
    ):
        repo = InfluxDBRepository()

    assert hasattr(repo, "write_telemetry")

    payload = TelemetryPayload(
        device_id="DEVICE-1",
        timestamp=datetime.now(timezone.utc),
        power=123.4,
        current=1.2,
        voltage=230.0,
    )

    result = repo.write_telemetry(payload)

    assert result is True
    mock_write_api.write.assert_called_once()


def test_phase_diagnostic_fields_are_valid_query_fields():
    InfluxDBRepository = _import_repository_class()

    assert {"current_l1", "current_l2", "current_l3", "voltage_l1", "voltage_l2", "voltage_l3"}.issubset(
        InfluxDBRepository.ALLOWED_FIELDS
    )
    assert {"power_l1", "power_l2", "power_l3", "power_factor_l1", "power_factor_l2", "power_factor_l3"}.issubset(
        InfluxDBRepository.ALLOWED_FIELDS
    )


def test_shutdown_calls_flush():
    InfluxDBRepository = _import_repository_class()

    mock_write_api = MagicMock()
    mock_client = MagicMock()
    mock_client.write_api.return_value = mock_write_api
    mock_client.query_api.return_value = MagicMock()
    mock_dlq = MagicMock()

    with patch("src.repositories.influxdb_repository.DLQRepository", return_value=mock_dlq), patch(
        "src.repositories.influxdb_repository.InfluxDBClient",
        return_value=mock_client,
    ):
        repo = InfluxDBRepository()

    repo.close()

    mock_write_api.flush.assert_called_once()
    mock_write_api.close.assert_called_once()
    mock_client.close.assert_called_once()
