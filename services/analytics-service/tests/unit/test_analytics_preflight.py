from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.api.routes import analytics
from src.models.schemas import AnalyticsPreflightDeviceStatus


@pytest.mark.asyncio
async def test_build_preflight_response_marks_guaranteed_no_data(monkeypatch):
    monkeypatch.setattr(
        analytics,
        "_check_device_telemetry_availability",
        AsyncMock(
            side_effect=[
                AnalyticsPreflightDeviceStatus(
                    device_id="D1",
                    has_telemetry_in_range=False,
                    reason="no_telemetry_in_range",
                    message="No telemetry found in the selected time range.",
                ),
                AnalyticsPreflightDeviceStatus(
                    device_id="D2",
                    has_telemetry_in_range=False,
                    reason="no_telemetry_in_range",
                    message="No telemetry found in the selected time range.",
                ),
            ]
        ),
    )

    response = await analytics._build_preflight_response(
        ["D1", "D2"],
        start_time=datetime(2026, 4, 1),
        end_time=datetime(2026, 4, 2),
        tenant_id="ORG-A",
    )

    assert response.guaranteed_no_data is True
    assert response.devices_with_telemetry == 0
    assert response.devices_without_telemetry == 2
    assert response.devices_unverified == 0
    assert response.coverage_result["level"] == "no_coverage"
    assert response.coverage_result["usable_for_business_decisions"] is False


@pytest.mark.asyncio
async def test_build_preflight_response_preserves_partial_warning(monkeypatch):
    monkeypatch.setattr(
        analytics,
        "_check_device_telemetry_availability",
        AsyncMock(
            side_effect=[
                AnalyticsPreflightDeviceStatus(
                    device_id="D1",
                    has_telemetry_in_range=True,
                    reason="telemetry_available",
                    message="Telemetry is available in the selected time range.",
                ),
                AnalyticsPreflightDeviceStatus(
                    device_id="D2",
                    has_telemetry_in_range=False,
                    reason="no_telemetry_in_range",
                    message="No telemetry found in the selected time range.",
                ),
            ]
        ),
    )

    response = await analytics._build_preflight_response(
        ["D1", "D2"],
        start_time=datetime(2026, 4, 1),
        end_time=datetime(2026, 4, 2),
        tenant_id="ORG-A",
    )

    assert response.guaranteed_no_data is False
    assert response.devices_with_telemetry == 1
    assert response.devices_without_telemetry == 1
    assert "do not have telemetry" in response.message
    assert response.coverage_result["level"] == "partial_coverage"
    assert response.coverage_result["coverage_pct"] == 50.0
