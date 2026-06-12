"""Test configuration and fixtures."""

import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from src.config.settings import Settings
from src.config.settings import get_settings

os.environ.setdefault("APP_ROLE", "api")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-at-least-32-chars")
os.environ.setdefault(
    "INTERNAL_SERVICE_SHARED_SECRET",
    "test-internal-service-secret-at-least-32-chars",
)

@pytest.fixture(autouse=True)
def _force_test_settings_env():
    old = os.environ.get("APP_ENV")
    old_role = os.environ.get("APP_ROLE")
    old_jwt_secret = os.environ.get("JWT_SECRET_KEY")
    old_internal_secret = os.environ.get("INTERNAL_SERVICE_SHARED_SECRET")
    os.environ["APP_ENV"] = "test"
    os.environ["APP_ROLE"] = "api"
    os.environ["JWT_SECRET_KEY"] = old_jwt_secret or "test-jwt-secret-key-at-least-32-chars"
    os.environ["INTERNAL_SERVICE_SHARED_SECRET"] = (
        old_internal_secret or "test-internal-service-secret-at-least-32-chars"
    )
    get_settings.cache_clear()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("APP_ENV", None)
        else:
            os.environ["APP_ENV"] = old
        if old_role is None:
            os.environ.pop("APP_ROLE", None)
        else:
            os.environ["APP_ROLE"] = old_role
        if old_jwt_secret is None:
            os.environ.pop("JWT_SECRET_KEY", None)
        else:
            os.environ["JWT_SECRET_KEY"] = old_jwt_secret
        if old_internal_secret is None:
            os.environ.pop("INTERNAL_SERVICE_SHARED_SECRET", None)
        else:
            os.environ["INTERNAL_SERVICE_SHARED_SECRET"] = old_internal_secret
        get_settings.cache_clear()


@pytest.fixture
def test_settings() -> Settings:
    """Test settings fixture."""
    return Settings(
        app_env="test",
        log_level="DEBUG",
        mysql_database="test_energy_analytics_db",
        s3_bucket_name="test-bucket",
    )


@pytest.fixture
def sample_telemetry_data() -> pd.DataFrame:
    """Create sample telemetry data for testing."""
    timestamps = pd.date_range(
        start=datetime.now() - timedelta(days=7),
        periods=1000,
        freq="5min",
    )
    
    data = {
        "_time": timestamps,
        "device_id": ["D1"] * 1000,
        "voltage": [230.0 + (i % 10) for i in range(1000)],
        "current": [0.85 + (i % 5) * 0.01 for i in range(1000)],
        "power": [195.0 + (i % 20) for i in range(1000)],
        "temperature": [45.0 + (i % 15) for i in range(1000)],
    }
    
    return pd.DataFrame(data)


@pytest.fixture
def mock_s3_client():
    """Mock S3 client fixture."""
    with patch("src.infrastructure.s3_client.S3Client") as mock:
        instance = mock.return_value
        instance.download_file = AsyncMock(return_value=b"mock_parquet_data")
        instance.list_objects = AsyncMock(return_value=[])
        yield instance


@pytest.fixture
def mock_result_repository():
    """Mock result repository fixture."""
    repo = MagicMock()
    repo.create_job = AsyncMock()
    repo.get_job = AsyncMock()
    repo.update_job_status = AsyncMock()
    repo.update_job_progress = AsyncMock()
    repo.save_results = AsyncMock()
    repo.update_job_queue_metadata = AsyncMock()
    repo.rollback = AsyncMock()
    repo.list_jobs = AsyncMock(return_value=[])
    repo.count_jobs = AsyncMock(return_value=0)
    repo.list_tenant_job_counts = AsyncMock(return_value=[])
    repo.get_model_artifact = AsyncMock(return_value=None)
    repo.upsert_model_artifact = AsyncMock()
    return repo
