import logging
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    DATABASE_URL: str | None = os.getenv("DATABASE_URL", None)
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 3600

    INFLUXDB_URL: str | None = os.getenv("INFLUXDB_URL", None)
    INFLUXDB_TOKEN: str | None = os.getenv("INFLUXDB_TOKEN", None)
    INFLUXDB_ORG: str = "energy-org"
    INFLUXDB_BUCKET: str = "telemetry"
    INFLUXDB_MEASUREMENT: str = "device_telemetry"
    INFLUX_AGGREGATION_WINDOW: str = "5m"
    INFLUX_ACCOUNTING_WINDOW: str = "1m"
    INFLUX_ACCOUNTING_CHUNK_HOURS: int = 24
    INFLUX_MAX_POINTS: int = 10000

    DEVICE_SERVICE_URL: str | None = os.getenv("DEVICE_SERVICE_URL", None)
    REPORTING_SERVICE_URL: str | None = os.getenv("REPORTING_SERVICE_URL", None)
    ENERGY_SERVICE_URL: str | None = os.getenv("ENERGY_SERVICE_URL", None)

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

    TARIFF_CACHE_TTL_SECONDS: int = 60
    WASTE_STRICT_QUALITY_GATE: bool = True
    WASTE_JOB_TIMEOUT_SECONDS: int = 600
    WASTE_DEVICE_CONCURRENCY: int = 16
    WASTE_DB_BATCH_SIZE: int = 500
    WASTE_PDF_MAX_DEVICES: int = 200
    WASTE_RETENTION_ENABLED: bool = True
    WASTE_RETENTION_DAYS: int = 90
    WASTE_RETENTION_BATCH_SIZE: int = 250
    WASTE_RETENTION_INTERVAL_SECONDS: int = 3600

    APP_ROLE: str = "api"
    REDIS_URL: str | None = os.getenv("REDIS_URL", None)
    WASTE_QUEUE_BACKEND: str = "redis"
    WASTE_QUEUE_STREAM: str = "waste:analysis:jobs"
    WASTE_QUEUE_DEAD_LETTER_STREAM: str = "waste:analysis:jobs:dead"
    WASTE_QUEUE_CONSUMER_GROUP: str = "waste-workers"
    WASTE_QUEUE_CONSUMER_NAME: str = os.getenv(
        "WASTE_QUEUE_CONSUMER_NAME",
        "waste-analysis-worker",
    )
    WASTE_QUEUE_MAXLEN: int = 10000
    WASTE_QUEUE_REJECT_THRESHOLD: int = 5000
    WASTE_QUEUE_READ_BLOCK_MS: int = 5000
    WASTE_QUEUE_CLAIM_IDLE_MS: int = 30000
    WASTE_WORKER_CONCURRENCY: int = 2
    WASTE_WORKER_HEARTBEAT_SECONDS: int = 30
    WASTE_WORKER_HEARTBEAT_TTL_SECONDS: int = 120
    WASTE_JOB_MAX_RETRIES: int = 3
    WASTE_TENANT_MAX_PENDING_JOBS: int = 25
    WASTE_WORKER_LEASE_SECONDS: int = 900


settings = Settings()

for _name in (
    "DATABASE_URL",
    "INFLUXDB_URL",
    "INFLUXDB_TOKEN",
    "DEVICE_SERVICE_URL",
    "REPORTING_SERVICE_URL",
    "ENERGY_SERVICE_URL",
    "MINIO_ENDPOINT",
    "MINIO_EXTERNAL_URL",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "REDIS_URL",
):
    if getattr(settings, _name) is None:
        logger.warning("Missing environment variable for waste-analysis-service setting: %s", _name)
