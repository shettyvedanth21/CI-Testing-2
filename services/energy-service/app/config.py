from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "energy-service"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    DATABASE_URL: str = "mysql+aiomysql://energy:energy@mysql:3306/ai_factoryops"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 1800

    REDIS_URL: Optional[str] = "redis://redis:6379/0"
    ENERGY_STREAM_REDIS_CHANNEL: str = "factoryops:energy_stream:v1"

    REPORTING_SERVICE_BASE_URL: str = "http://reporting-service:8085"
    DEVICE_SERVICE_BASE_URL: str = "http://device-service:8000"
    DATA_SERVICE_BASE_URL: Optional[str] = None
    PLATFORM_TIMEZONE: str = "Asia/Kolkata"

    TARIFF_CACHE_TTL_SECONDS: int = 60
    MAX_FALLBACK_GAP_SECONDS: int = 300
    LIVE_UPDATE_MAX_REORDER_SECONDS: int = 2
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_OPEN_TIMEOUT_SEC: int = 30
    CIRCUIT_BREAKER_SUCCESS_THRESHOLD: int = 2
    ENERGY_BATCH_CHUNK_SIZE: int = 50

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
