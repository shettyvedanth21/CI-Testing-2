"""Application configuration management for Rule Engine Service."""

import logging
import os

from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    
    # Application
    SERVICE_NAME: str = "rule-engine-service"
    APP_NAME: str = "rule-engine-service"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    
    # Database
    DATABASE_URL: str | None = os.getenv("DATABASE_URL", None)
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 1800
    WORKER_DATABASE_POOL_SIZE: int = 15
    WORKER_DATABASE_MAX_OVERFLOW: int = 25
    
    # API
    API_PREFIX: str = "/api/v1"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    # Rule Engine
    RULE_EVALUATION_TIMEOUT: int = 5  # seconds
    NOTIFICATION_COOLDOWN_MINUTES: int = 15
    MAX_RULES_PER_DEVICE: int = 100
    PLATFORM_TIMEZONE: str = "Asia/Kolkata"
    
    # Notification Adapters
    EMAIL_ENABLED: bool = True
    EMAIL_SMTP_HOST: str | None = os.getenv("EMAIL_SMTP_HOST", None)
    EMAIL_SMTP_PORT: int = 587
    EMAIL_SMTP_USERNAME: str | None = os.getenv("EMAIL_SMTP_USERNAME", None)
    EMAIL_SMTP_PASSWORD: str | None = os.getenv("EMAIL_SMTP_PASSWORD", None)
    EMAIL_FROM_ADDRESS: str = "alerts@energy-platform.com"

    SMS_ENABLED: bool = False
    TWILIO_ACCOUNT_SID: str | None = os.getenv("TWILIO_ACCOUNT_SID", None)
    TWILIO_AUTH_TOKEN: str | None = os.getenv("TWILIO_AUTH_TOKEN", None)
    TWILIO_SMS_FROM_NUMBER: str | None = os.getenv("TWILIO_SMS_FROM_NUMBER", None)

    WHATSAPP_ENABLED: bool = False
    TWILIO_WHATSAPP_FROM_NUMBER: str | None = os.getenv("TWILIO_WHATSAPP_FROM_NUMBER", None)
    DEVICE_SERVICE_URL: str | None = os.getenv("DEVICE_SERVICE_URL", None)
    REDIS_URL: str | None = os.getenv("REDIS_URL", None)
    APP_ROLE: str = "api"
    QUEUE_BACKEND: str = "redis"
    NOTIFICATION_OUTBOX_STREAM: str = "rule-engine:notification-outbox"
    NOTIFICATION_OUTBOX_DEAD_LETTER_STREAM: str = "rule-engine:notification-outbox:dead"
    NOTIFICATION_OUTBOX_CONSUMER_GROUP: str = "rule-engine-notification-workers"
    NOTIFICATION_OUTBOX_CONSUMER_NAME: str = os.getenv("NOTIFICATION_OUTBOX_CONSUMER_NAME", f"{SERVICE_NAME}-worker")
    NOTIFICATION_WORKER_CONCURRENCY: int = 4
    NOTIFICATION_OUTBOX_MAX_RETRIES: int = 4
    NOTIFICATION_OUTBOX_QUEUE_MAXLEN: int = 20000
    NOTIFICATION_OUTBOX_CLAIM_IDLE_MS: int = 30000
    NOTIFICATION_OUTBOX_READ_BLOCK_MS: int = 5000
    NOTIFICATION_OUTBOX_REQUEUE_BATCH_SIZE: int = 100
    NOTIFICATION_OUTBOX_REQUEUE_INTERVAL_SECONDS: int = 5
    NOTIFICATION_BACKOFF_BASE_SECONDS: int = 5
    NOTIFICATION_BACKOFF_MAX_SECONDS: int = 300
    NOTIFICATION_DELIVERY_TIMEOUT_SECONDS: int = 30
    NOTIFICATION_TENANT_MAX_PENDING_NOTIFICATIONS: int = 50
    NOTIFICATION_QUEUE_REJECT_THRESHOLD: int = 500

    # Multi-tenancy (Phase-2 ready)
    TENANT_ID_HEADER: str = "X-Tenant-ID"
    
    # Notification delivery ledger retention (explicitly opt-in cleanup)
    NOTIFICATION_DELIVERY_RETENTION_ENABLED: bool = False
    NOTIFICATION_DELIVERY_RETENTION_MONTHS: int = 18
    NOTIFICATION_USAGE_EXPORT_STREAM_BATCH_SIZE: int = 500

    def model_post_init(self, __context):
        if self.EMAIL_SMTP_PASSWORD is not None:
            self.EMAIL_SMTP_PASSWORD = self.EMAIL_SMTP_PASSWORD.replace(" ", "")
    

settings = Settings()

for _name in (
    "DATABASE_URL",
    "EMAIL_SMTP_HOST",
    "EMAIL_SMTP_USERNAME",
    "EMAIL_SMTP_PASSWORD",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_SMS_FROM_NUMBER",
    "TWILIO_WHATSAPP_FROM_NUMBER",
    "DEVICE_SERVICE_URL",
    "REDIS_URL",
):
    if getattr(settings, _name) is None:
        logger.warning("Missing environment variable for rule-engine-service setting: %s", _name)
