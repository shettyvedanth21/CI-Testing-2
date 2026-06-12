import logging
import os
from datetime import datetime, timezone

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    DATABASE_URL: str | None = os.getenv("DATABASE_URL", None)
    INFLUXDB_URL: str | None = os.getenv("INFLUXDB_URL", None)
    INFLUXDB_TOKEN: str | None = os.getenv("INFLUXDB_TOKEN", None)
    INFLUXDB_ORG: str = "my-org"
    INFLUXDB_BUCKET: str = "telemetry"
    INFLUXDB_MEASUREMENT: str = "device_telemetry"
    INFLUX_POWER_FIELD: str = "power"
    INFLUX_VOLTAGE_FIELD: str = "voltage"
    INFLUX_CURRENT_FIELD: str = "current"
    INFLUX_POWER_FACTOR_FIELD: str = "power_factor"
    INFLUX_REACTIVE_POWER_FIELD: str = "reactive_power"
    INFLUX_FREQUENCY_FIELD: str = "frequency"
    INFLUX_THD_FIELD: str = "thd"
    INFLUX_AGGREGATION_WINDOW: str = "5m"
    INFLUX_ACCOUNTING_WINDOW: str = "1m"
    INFLUX_ACCOUNTING_CHUNK_HOURS: int = 24
    INFLUX_MAX_POINTS: int = 10000
    DEVICE_SERVICE_URL: str | None = os.getenv("DEVICE_SERVICE_URL", None)
    ENERGY_SERVICE_URL: str | None = os.getenv("ENERGY_SERVICE_URL", None)
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 3600
    MINIO_ENDPOINT: str | None = os.getenv("MINIO_ENDPOINT", None)
    MINIO_EXTERNAL_URL: str | None = os.getenv("MINIO_EXTERNAL_URL", None)
    MINIO_ACCESS_KEY: str | None = os.getenv("MINIO_ACCESS_KEY", None)
    MINIO_SECRET_KEY: str | None = os.getenv("MINIO_SECRET_KEY", None)
    MINIO_BUCKET: str = "energy-platform-datasets"
    MINIO_SECURE: bool = False
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    AWS_ENDPOINT_URL: str | None = os.getenv("AWS_ENDPOINT_URL", None)
    AWS_ACCESS_KEY_ID: str | None = os.getenv("AWS_ACCESS_KEY_ID", None)
    AWS_SECRET_ACCESS_KEY: str | None = os.getenv("AWS_SECRET_ACCESS_KEY", None)
    PLATFORM_TIMEZONE: str = "Asia/Kolkata"
    DEMAND_WINDOW_MINUTES: int = 15
    REPORT_JOB_TIMEOUT_SECONDS: int = 600
    SERVICE_NAME: str = "reporting-service"
    APP_ROLE: str = "api"
    ENVIRONMENT: str = "development"
    QUEUE_BACKEND: str = "redis"
    REDIS_URL: str | None = os.getenv("REDIS_URL", None)
    REPORT_QUEUE_STREAM: str = "reporting:jobs"
    REPORT_QUEUE_DEAD_LETTER_STREAM: str = "reporting:jobs:dead"
    REPORT_QUEUE_CONSUMER_GROUP: str = "reporting-workers"
    REPORT_QUEUE_CONSUMER_NAME: str = os.getenv("REPORT_QUEUE_CONSUMER_NAME", f"{SERVICE_NAME}-worker")
    REPORT_WORKER_CONCURRENCY: int = 2
    REPORT_WORKER_HEARTBEAT_SECONDS: int = 30
    REPORT_WORKER_HEARTBEAT_TTL_SECONDS: int = 120
    REPORT_QUEUE_MAXLEN: int = 10000
    REPORT_QUEUE_REJECT_THRESHOLD: int = 5000
    REPORT_JOB_MAX_RETRIES: int = 3
    REPORT_TENANT_MAX_PENDING_JOBS: int = 25
    REPORT_TENANT_MAX_ACTIVE_JOBS: int = 4
    REPORT_SUBMIT_RATE_LIMIT: str = "10/minute"
    REPORT_SCHEDULE_RATE_LIMIT: str = "10/hour"
    REPORT_QUEUE_CLAIM_IDLE_MS: int = 30000
    REPORT_QUEUE_READ_BLOCK_MS: int = 5000
    REPORT_METRICS_CACHE_SECONDS: int = 5
    REPORTS_CANONICAL_FINANCIAL_SHADOW_ENABLED: bool = True
    REPORTS_CANONICAL_FINANCIAL_APPLY_ENABLED: bool = True
    REPORT_RETENTION_ENABLED: bool = True
    REPORT_RETENTION_DAYS: int = 90
    REPORT_RETENTION_BATCH_SIZE: int = 250
    REPORT_RETENTION_INTERVAL_SECONDS: int = 3600
    LOCAL_BOOTSTRAP_ENABLED: bool = False
    LOCAL_BOOTSTRAP_TENANT_ID: str = "SH00000001"
    LOCAL_BOOTSTRAP_TARIFF_RATE: float = 8.0
    LOCAL_BOOTSTRAP_TARIFF_CURRENCY: str = "INR"
    LOCAL_BOOTSTRAP_TARIFF_DEMAND_CHARGE_PER_KW: float = 0.0
    LOCAL_BOOTSTRAP_TARIFF_REACTIVE_PENALTY_RATE: float = 0.0
    LOCAL_BOOTSTRAP_TARIFF_FIXED_MONTHLY_CHARGE: float = 0.0
    LOCAL_BOOTSTRAP_TARIFF_POWER_FACTOR_THRESHOLD: float = 0.9
    LOCAL_BOOTSTRAP_TARIFF_EFFECTIVE_START_AT: str = "2026-01-01T00:00:00+00:00"

    @property
    def local_bootstrap_tariff_effective_start_at(self) -> datetime:
        raw = self.LOCAL_BOOTSTRAP_TARIFF_EFFECTIVE_START_AT.strip()
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


settings = Settings()


def is_production_environment() -> bool:
    return settings.ENVIRONMENT.strip().lower() == "production"

for _name in (
    "DATABASE_URL",
    "INFLUXDB_URL",
    "INFLUXDB_TOKEN",
    "DEVICE_SERVICE_URL",
    "ENERGY_SERVICE_URL",
    "MINIO_ENDPOINT",
    "MINIO_EXTERNAL_URL",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "REDIS_URL",
):
    if getattr(settings, _name) is None:
        logger.warning("Missing environment variable for reporting-service setting: %s", _name)
