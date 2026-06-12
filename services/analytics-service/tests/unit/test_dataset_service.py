from datetime import datetime, timedelta, timezone
import io
import sys
import types
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

sys.modules.setdefault("aioboto3", types.SimpleNamespace(Session=lambda *args, **kwargs: None))

from src.services.dataset_service import DatasetService


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self._idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, headers=None):
        payload = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return _FakeResponse(payload, status_code=200)


@pytest.mark.asyncio
async def test_load_dataset_falls_back_to_data_service_when_s3_key_missing():
    s3 = AsyncMock()
    s3.download_file = AsyncMock(side_effect=RuntimeError("NoSuchKey"))
    s3.list_objects = AsyncMock(return_value=[])
    svc = DatasetService(s3)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "data": {
            "items": [
                {"timestamp": (now - timedelta(minutes=2)).isoformat(), "power": 1000.0, "current": 5.0},
                {"timestamp": (now - timedelta(minutes=1)).isoformat(), "power": 1100.0, "current": 5.2},
            ]
        }
    }

    with patch("src.services.dataset_service.httpx.AsyncClient", return_value=_FakeClient([payload])):
        with patch("src.services.dataset_service.get_settings") as get_settings:
            cfg = get_settings.return_value
            cfg.ml_require_exact_dataset_range = True
            cfg.app_env = "development"
            cfg.data_service_url = "http://data-service:8081"
            cfg.data_service_query_timeout_seconds = 10
            cfg.data_service_query_limit = 10000
            cfg.data_service_fallback_chunk_hours = 24

            df = await svc.load_dataset(
                device_id="D1",
                start_time=now - timedelta(hours=1),
                end_time=now,
                s3_key=None,
            )

    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "timestamp" in df.columns


@pytest.mark.asyncio
async def test_data_service_fallback_chunks_and_dedupes():
    s3 = AsyncMock()
    s3.download_file = AsyncMock(side_effect=RuntimeError("NoSuchKey"))
    s3.list_objects = AsyncMock(return_value=[])
    svc = DatasetService(s3)

    base = datetime.now(timezone.utc).replace(microsecond=0)
    t1 = (base - timedelta(hours=7)).isoformat()
    t2 = (base - timedelta(hours=6, minutes=30)).isoformat()
    responses = [
        {"data": {"items": [{"timestamp": t1, "power": 1000.0}, {"timestamp": t2, "power": 1200.0}]}},
        {"data": {"items": [{"timestamp": t2, "power": 1200.0}]}},  # duplicate across chunk boundary
    ]

    with patch("src.services.dataset_service.httpx.AsyncClient", return_value=_FakeClient(responses)):
        with patch("src.services.dataset_service.get_settings") as get_settings:
            cfg = get_settings.return_value
            cfg.ml_require_exact_dataset_range = True
            cfg.app_env = "development"
            cfg.data_service_url = "http://data-service:8081"
            cfg.data_service_query_timeout_seconds = 10
            cfg.data_service_query_limit = 10000
            cfg.data_service_fallback_chunk_hours = 6

            df = await svc.load_dataset(
                device_id="D2",
                start_time=base - timedelta(hours=13),
                end_time=base - timedelta(hours=1),
                s3_key=None,
            )

    assert len(df) == 2


@pytest.mark.asyncio
async def test_load_dataset_normalizes_signed_power_with_device_config_and_active_power_alias():
    source = pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 4, 9, 14, 36, tzinfo=timezone.utc),
                "device_id": "VAL-SIGN-ALIAS",
                "voltage": -230.0,
                "current": -5.0,
                "power": -1000.0,
                "active_power": -4000.0,
                "power_factor": -0.85,
                "temperature": 41.0,
            }
        ]
    )
    buf = io.BytesIO()
    source.to_parquet(buf, index=False)

    s3 = AsyncMock()
    s3.download_file = AsyncMock(return_value=buf.getvalue())
    s3.list_objects = AsyncMock(return_value=[])
    svc = DatasetService(s3)

    device_payload = {
        "data": {
            "device_id": "VAL-SIGN-ALIAS",
            "energy_flow_mode": "consumption_only",
            "polarity_mode": "inverted",
        }
    }

    with patch("src.services.dataset_service.httpx.AsyncClient", return_value=_FakeClient([device_payload])):
        with patch("src.services.dataset_service.get_settings") as get_settings:
            cfg = get_settings.return_value
            cfg.device_service_url = "http://device-service:8000"
            cfg.max_dataset_size_mb = 500
            cfg.ml_max_dataset_rows = 500000

            df = await svc.load_dataset(
                device_id="VAL-SIGN-ALIAS",
                start_time=datetime(2026, 4, 9, 14, 34, tzinfo=timezone.utc),
                end_time=datetime(2026, 4, 9, 14, 38, tzinfo=timezone.utc),
                s3_key="datasets/VAL-SIGN-ALIAS/20260409_20260409.parquet",
                tenant_id="SH00000001",
            )

    row = df.iloc[0]
    assert float(row["power"]) == 4000.0
    assert float(row["current"]) == 5.0
    assert float(row["voltage"]) == 230.0
    assert float(row["power_factor"]) == 0.85
    assert row["power_direction"] == "import"
    assert row["polarity_mode"] == "inverted"
    assert "active_power" not in df.columns


@pytest.mark.asyncio
async def test_load_dataset_drops_phase_diagnostics_from_business_dataset():
    source = pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 4, 9, 14, 36, tzinfo=timezone.utc),
                "device_id": "VAL-PHASE-DIAG",
                "voltage": 230.0,
                "current": 10.0,
                "power": 1200.0,
                "power_factor": 0.9,
                "current_l1": 999.0,
                "current_l2": 0.0,
                "current_l3": -999.0,
                "voltage_l1": 500.0,
                "voltage_l2": 0.0,
                "voltage_l3": -100.0,
                "power_factor_l1": 0.1,
            }
        ]
    )
    buf = io.BytesIO()
    source.to_parquet(buf, index=False)

    s3 = AsyncMock()
    s3.download_file = AsyncMock(return_value=buf.getvalue())
    s3.list_objects = AsyncMock(return_value=[])
    svc = DatasetService(s3)

    with patch("src.services.dataset_service.get_settings") as get_settings:
        cfg = get_settings.return_value
        cfg.device_service_url = ""
        cfg.max_dataset_size_mb = 500
        cfg.ml_max_dataset_rows = 500000

        df = await svc.load_dataset(
            device_id="VAL-PHASE-DIAG",
            start_time=datetime(2026, 4, 9, 14, 34, tzinfo=timezone.utc),
            end_time=datetime(2026, 4, 9, 14, 38, tzinfo=timezone.utc),
            s3_key="datasets/VAL-PHASE-DIAG/20260409_20260409.parquet",
            tenant_id="SH00000001",
        )

    row = df.iloc[0]
    assert float(row["current"]) == 10.0
    assert float(row["voltage"]) == 230.0
    assert "current_l1" not in df.columns
    assert "current_l2" not in df.columns
    assert "current_l3" not in df.columns
    assert "voltage_l1" not in df.columns
    assert "voltage_l2" not in df.columns
    assert "voltage_l3" not in df.columns
    assert "power_factor_l1" not in df.columns
