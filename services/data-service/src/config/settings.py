"""Application configuration settings."""

import logging
import os
from typing import List, Optional
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, validator, AliasChoices
from pydantic_settings import BaseSettings


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = Field(default="data-service", description="Service name")
    app_version: str = Field(default="1.0.0", description="Service version")
    environment: str = Field(default="development", description="Environment")
    log_level: str = Field(default="INFO", description="Logging level")

    # Server
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8081, description="Server port")

    # MQTT Configuration
    mqtt_broker_host: str = Field(default="localhost", description="MQTT broker host")
    mqtt_broker_port: int = Field(default=1883, description="MQTT broker port")
    mqtt_username: Optional[str] = Field(default=None, description="MQTT username")
    mqtt_password: Optional[str] = Field(default=os.getenv("MQTT_PASSWORD", None), description="MQTT password")
    mqtt_topic: str = Field(default="devices/+/telemetry", description="MQTT subscription topic")
    mqtt_qos: int = Field(default=1, description="MQTT QoS level")
    mqtt_reconnect_interval: int = Field(default=5, description="MQTT reconnect interval in seconds")
    mqtt_max_reconnect_attempts: int = Field(default=10, description="Max MQTT reconnect attempts")
    mqtt_keepalive: int = Field(default=60, description="MQTT keepalive interval")
    mqtt_clean_session: bool = Field(default=True, description="Whether MQTT subscriptions use clean sessions")

    # Redis / durable telemetry streams
    redis_url: str | None = Field(default=os.getenv("REDIS_URL", None), description="Redis connection URL")
    redis_max_connections: int = Field(default=256, description="Max Redis connections per process pool")
    app_role: str = Field(default=os.getenv("APP_ROLE", "api"), description="Runtime role: api or worker")
    telemetry_ingest_stream_name: str = Field(default="telemetry_ingest_stream", description="Redis stream for raw telemetry ingest")
    telemetry_projection_stream_name: str = Field(default="telemetry_projection_stream", description="Redis stream for persisted telemetry awaiting projection")
    telemetry_broadcast_stream_name: str = Field(default="telemetry_broadcast_stream", description="Redis stream for broadcast fan-out work")
    telemetry_energy_stream_name: str = Field(default="telemetry_energy_stream", description="Redis stream for energy outbox fan-out work")
    telemetry_rules_stream_name: str = Field(default="telemetry_rules_stream", description="Redis stream for rule evaluation work")
    telemetry_dead_letter_stream_name: str = Field(default="telemetry_dead_letter_stream", description="Redis stream for dead-lettered telemetry stage entries")
    telemetry_dead_letter_stream_maxlen: int = Field(default=100000, description="Max retained dead-letter stream entries")
    telemetry_ingest_consumer_group: str = Field(default="telemetry_ingest_workers", description="Consumer group for ingest persistence stage")
    telemetry_projection_consumer_group: str = Field(default="telemetry_projection_workers", description="Consumer group for projection stage")
    telemetry_broadcast_consumer_group: str = Field(default="telemetry_broadcast_workers", description="Consumer group for broadcast stage")
    telemetry_energy_consumer_group: str = Field(default="telemetry_energy_workers", description="Consumer group for energy stage")
    telemetry_rules_consumer_group: str = Field(default="telemetry_rules_workers", description="Consumer group for rule stage")
    telemetry_worker_consumer_name: str = Field(default=os.getenv("TELEMETRY_WORKER_CONSUMER_NAME", "data-telemetry-worker"), description="Base consumer name for telemetry workers")
    telemetry_worker_maintenance_enabled: bool = Field(default=os.getenv("TELEMETRY_WORKER_MAINTENANCE_ENABLED", "true").lower() == "true", description="Whether this worker instance runs singleton maintenance loops")
    telemetry_worker_outbox_relay_enabled: bool = Field(default=os.getenv("TELEMETRY_WORKER_OUTBOX_RELAY_ENABLED", "true").lower() == "true", description="Whether this worker instance runs the outbox relay loop")
    telemetry_worker_heartbeat_ttl_seconds: int = Field(default=30, description="TTL for worker heartbeat records")
    telemetry_health_lag_warn_seconds: int = Field(default=30, description="Oldest stage age that marks telemetry health as degraded")
    telemetry_health_lag_overload_seconds: int = Field(default=90, description="Oldest stage age that marks telemetry health as overloaded")
    telemetry_ingest_stream_maxlen: int = Field(default=250000, description="Max retained raw ingest stream entries")
    telemetry_projection_stream_maxlen: int = Field(default=250000, description="Max retained projection stream entries")
    telemetry_broadcast_stream_maxlen: int = Field(default=250000, description="Max retained broadcast stream entries")
    telemetry_energy_stream_maxlen: int = Field(default=250000, description="Max retained energy stream entries")
    telemetry_rules_stream_maxlen: int = Field(default=250000, description="Max retained rules stream entries")
    telemetry_ingest_reject_threshold: int = Field(default=200000, description="Backlog threshold that rejects new raw ingest appends")
    telemetry_projection_backlog_threshold: int = Field(default=200000, description="Backlog threshold for projection stage publish")
    telemetry_broadcast_backlog_threshold: int = Field(default=200000, description="Backlog threshold for broadcast stage publish")
    telemetry_energy_backlog_threshold: int = Field(default=200000, description="Backlog threshold for energy stage publish")
    telemetry_rules_backlog_threshold: int = Field(default=200000, description="Backlog threshold for rules stage publish")
    telemetry_projection_overload_threshold: int = Field(default=20000, description="Projection backlog threshold that marks telemetry as overloaded")
    telemetry_energy_overload_threshold: int = Field(default=5000, description="Energy backlog threshold that marks telemetry as overloaded")
    telemetry_rules_overload_threshold: int = Field(default=10000, description="Rules backlog threshold that marks telemetry as overloaded")
    telemetry_projection_overload_max_defers: int = Field(default=40, description="Max projection overload defers before terminal dead-lettering")
    telemetry_projection_defer_base_seconds: float = Field(default=0.25, description="Base backoff seconds for projection overload defers")
    telemetry_projection_defer_max_seconds: float = Field(default=8.0, description="Max backoff seconds for projection overload defers")
    telemetry_stream_block_ms: int = Field(default=2000, description="Redis stream block timeout for worker reads")
    telemetry_stage_reclaim_idle_ms: int = Field(default=30000, description="Idle time before stale stage messages are reclaimed")
    telemetry_stage_max_attempts: int = Field(default=3, description="Max attempts for stage worker retries before dead-lettering")
    telemetry_ingest_publish_concurrency: int = Field(default=64, description="Max concurrent ingress publishes to Redis Streams")
    telemetry_ingest_batch_size: int = Field(default=100, description="Batch size for ingest stage reads")
    telemetry_projection_batch_size: int = Field(default=100, description="Batch size for projection stage reads")
    telemetry_broadcast_batch_size: int = Field(default=200, description="Batch size for broadcast stage reads")
    telemetry_energy_batch_size: int = Field(default=200, description="Batch size for energy stage reads")
    telemetry_rules_batch_size: int = Field(default=100, description="Batch size for rule stage reads")
    telemetry_persistence_workers: int = Field(default=8, description="Number of persistence-stage workers")
    telemetry_projection_workers: int = Field(default=4, description="Number of projection-stage workers")
    telemetry_broadcast_workers: int = Field(default=4, description="Number of broadcast-stage workers")
    telemetry_energy_workers: int = Field(default=6, description="Number of energy-stage workers")
    telemetry_rules_workers: int = Field(default=8, description="Number of rule-stage workers")

    # InfluxDB Configuration
    influxdb_url: str | None = Field(default=os.getenv("INFLUXDB_URL", None), description="InfluxDB URL")
    influxdb_token: str | None = Field(default=os.getenv("INFLUXDB_TOKEN", None), description="InfluxDB token")
    influxdb_org: str = Field(default="energy-platform", description="InfluxDB organization")
    influxdb_bucket: str = Field(default="telemetry", description="InfluxDB bucket")
    influxdb_timeout: int = Field(default=5000, description="InfluxDB timeout in milliseconds")
    influx_batch_size: int = Field(default=100, description="InfluxDB batch size")
    influx_flush_interval_ms: int = Field(default=1000, description="InfluxDB batch flush interval in milliseconds")
    influx_max_retries: int = Field(default=3, description="Max retries for batched InfluxDB writes")

    # Device Service Configuration
    device_service_url: str | None = Field(
        default=os.getenv("DEVICE_SERVICE_URL", None),
        description="Device service base URL",
        validation_alias=AliasChoices(
            "device_service_url",
            "device_service_base_url",
        ),
    )
    device_service_timeout: float = Field(default=5.0, description="Device service timeout in seconds")
    device_service_max_retries: int = Field(default=3, description="Max retries for device service")
    device_projection_transport_retries: int = Field(default=3, description="Projection transport retries per batch request")
    device_projection_retry_backoff_base_seconds: float = Field(default=0.2, description="Base backoff seconds for projection transport retries")
    device_projection_retry_backoff_max_seconds: float = Field(default=2.0, description="Max backoff seconds for projection transport retries")
    device_projection_max_inflight_requests: int = Field(default=8, description="Max in-flight projection batch requests per process")
    device_projection_http_max_keepalive_connections: int = Field(default=40, description="Projection HTTP keepalive pool size")
    device_projection_http_max_connections: int = Field(default=80, description="Projection HTTP max connection pool size")
    device_projection_http_pool_timeout_seconds: float = Field(default=1.0, description="Projection HTTP pool timeout in seconds")
    device_sync_enabled: bool = Field(default=True, description="Enable async device heartbeat/property sync")
    device_sync_workers: int = Field(default=2, description="Number of device sync workers")
    device_sync_queue_maxsize: int = Field(default=5000, description="Legacy local queue max size")
    device_sync_max_retries: int = Field(default=3, description="Max retries for each device sync task")
    device_sync_retry_backoff_sec: float = Field(default=0.5, description="Initial sync retry backoff in seconds")
    device_sync_retry_backoff_max_sec: float = Field(default=5.0, description="Max sync retry backoff in seconds")
    energy_service_url: str | None = Field(default=os.getenv("ENERGY_SERVICE_URL", None), description="Energy service base URL")
    energy_sync_enabled: bool = Field(default=True, description="Enable async energy projection sync")
    telemetry_processing_workers: int = Field(default=8, description="Legacy telemetry processing workers")
    telemetry_projection_queue_maxsize: int = Field(default=5000, description="Legacy downstream projection queue max size")
    queue_overflow_log_level: str = Field(default="WARNING", description="Log level for queue overflow events")
    queue_depth_check_interval_sec: int = Field(default=10, description="Queue depth monitoring interval in seconds")
    queue_drain_timeout_sec: int = Field(default=30, description="Maximum seconds to wait for queue drain on shutdown")

    # MySQL Configuration (for durable DLQ backend)
    mysql_host: str = Field(default="mysql", description="MySQL host")
    mysql_port: int = Field(default=3306, description="MySQL port")
    mysql_database: str = Field(default="ai_factoryops", description="MySQL database name")
    mysql_user: str = Field(default="energy", description="MySQL username")
    mysql_password: str | None = Field(default=os.getenv("MYSQL_PASSWORD", None), description="MySQL password")
    db_pool_size: int = Field(default=5, description="SQLAlchemy pool base size per engine")
    db_max_overflow: int = Field(default=10, description="SQLAlchemy pool max overflow per engine")
    db_pool_recycle: int = Field(default=3600, description="SQLAlchemy pool recycle interval seconds")
    db_pool_timeout: int = Field(default=30, description="SQLAlchemy pool timeout seconds")

    # Outbox / Reconciliation
    outbox_poll_interval_sec: float = Field(default=2.0, description="Outbox relay polling interval in seconds")
    outbox_batch_size: int = Field(default=200, description="Max outbox rows claimed per relay batch")
    outbox_energy_delivery_batch_size: int = Field(default=200, description="Max energy-service rows delivered in one batched relay request")
    outbox_max_batches_per_run: int = Field(default=4, description="Max claim/deliver loops per relay run")
    outbox_http_timeout_seconds: float = Field(default=8.0, description="HTTP timeout for outbox downstream delivery")
    outbox_http_max_keepalive_connections: int = Field(default=40, description="Outbox relay HTTP keepalive pool size")
    outbox_http_max_connections: int = Field(default=120, description="Outbox relay HTTP max connection pool size")
    outbox_max_retries: int = Field(default=5, description="Max delivery retries before dead lettering")
    outbox_retry_backoff_base_seconds: int = Field(default=5, description="Base seconds for exponential retry backoff in outbox claim query")
    outbox_device_config_cache_ttl_seconds: int = Field(default=300, description="TTL seconds for outbox relay device power-config cache")
    outbox_circuit_breaker_half_open_max_calls: int = Field(default=3, description="Max half-open probe calls for outbox relay circuit breakers")
    outbox_pending_warn_threshold: int = Field(default=2000, description="Pending outbox rows that mark telemetry as degraded")
    outbox_pending_overload_threshold: int = Field(default=10000, description="Pending outbox rows that mark telemetry as overloaded")
    outbox_delivered_retention_days: int = Field(default=7, description="Days to keep delivered outbox rows")
    outbox_dead_retention_days: int = Field(default=14, description="Days to keep dead outbox rows")
    reconciliation_log_retention_days: int = Field(default=14, description="Days to keep reconciliation log rows")
    retention_cleanup_interval_sec: int = Field(default=3600, description="Retention cleanup interval in seconds")
    retention_cleanup_batch_size: int = Field(default=5000, description="Max rows to purge per table per cleanup pass")
    reconciliation_interval_sec: int = Field(default=300, description="Reconciliation run interval in seconds")
    reconciliation_drift_warn_minutes: int = Field(default=10, description="Warn threshold in minutes")
    reconciliation_drift_resync_minutes: int = Field(default=30, description="Resync threshold in minutes")
    circuit_breaker_failure_threshold: int = Field(default=5, description="Failures before opening a circuit breaker")
    circuit_breaker_open_timeout_sec: int = Field(default=30, description="Open state timeout before half-open probe")
    circuit_breaker_success_threshold: int = Field(default=2, description="Half-open successes required to close a breaker")
    tenant_lock_cleanup_interval_sec: int = Field(default=300, description="Interval between tenant lock cleanup sweeps")
    tenant_lock_provider: str = Field(default="in_process", description="Tenant lock provider: in_process or redis")
    tenant_lock_redis_ttl_seconds: int = Field(default=30, description="Redis tenant lock TTL in seconds")
    tenant_lock_redis_acquire_timeout_seconds: float = Field(default=15.0, description="Max seconds to wait for Redis tenant lock acquisition")

    # Rule Engine Configuration
    rule_engine_url: str | None = Field(
        default=os.getenv("RULE_ENGINE_URL", None),
        description="Rule engine service URL",
        validation_alias=AliasChoices(
            "rule_engine_url",
            "rule_engine_base_url",
        ),
    )
    rule_engine_timeout: float = Field(default=5.0, description="Rule engine timeout")
    rule_engine_max_retries: int = Field(default=3, description="Max retries for rule engine")
    rule_engine_retry_delay: float = Field(default=1.0, description="Initial retry delay")

    # DLQ Configuration
    dlq_enabled: bool = Field(default=True, description="Enable dead letter queue")
    dlq_backend: str = Field(default="mysql", description="DLQ backend: mysql or file")
    dlq_directory: str = Field(default="./dlq", description="DLQ file directory")
    dlq_max_file_size: int = Field(default=10 * 1024 * 1024, description="Max DLQ file size in bytes")
    dlq_max_files: int = Field(default=10, description="Max number of DLQ files")
    dlq_retention_days: int = Field(default=14, description="DLQ retention days for durable backend")
    dlq_flush_batch_size: int = Field(default=100, description="Batch size for DLQ backend operations")
    dlq_retry_batch_limit: int = Field(default=100, description="Max DLQ rows retried per scheduler batch")
    dlq_retry_base_backoff_seconds: int = Field(default=60, description="Base retry backoff between DLQ batches")
    dlq_retry_max_backoff_seconds: int = Field(default=300, description="Max retry backoff between DLQ batches")
    dlq_retryable_error_types: List[str] = Field(
        default_factory=lambda: [
            "invalid_numeric_fields",
            "influxdb_write_error",
            "outbox_enqueue_error",
            "parse_error",
            "processing_error",
            "unexpected_error",
            "rule_engine_circuit_open",
            "rule_engine_server_error",
            "rule_engine_unexpected_error",
            "QUEUE_OVERFLOW",
        ],
        description="DLQ error types eligible for retry scheduling",
    )
    dlq_pending_warn_threshold: int = Field(default=500, description="Retryable DLQ pending rows that mark telemetry as degraded")
    dlq_pending_overload_threshold: int = Field(default=2000, description="Retryable DLQ pending rows that mark telemetry as overloaded")
    dlq_non_retryable_pending_warn_threshold: int = Field(
        default=100,
        description="Non-retryable pending DLQ rows threshold that marks telemetry as degraded",
    )

    # Telemetry Validation
    telemetry_schema_version: str = Field(default="v1", description="Supported schema version")
    telemetry_max_future_skew_seconds: int = Field(
        default=300,
        description="Max allowed future clock skew for telemetry timestamps before rejection",
    )
    telemetry_max_voltage: float = Field(default=250.0, description="Max voltage value")
    telemetry_min_voltage: float = Field(default=200.0, description="Min voltage value")
    telemetry_max_current: float = Field(default=2.0, description="Max current value")
    telemetry_min_current: float = Field(default=0.0, description="Min current value")
    telemetry_max_power: float = Field(default=500.0, description="Max power value")
    telemetry_min_power: float = Field(default=0.0, description="Min power value")
    telemetry_max_temperature: float = Field(default=80.0, description="Max temperature value")
    telemetry_min_temperature: float = Field(default=20.0, description="Min temperature value")
    telemetry_default_lookback_hours: int = Field(
        default=720,
        description="Default telemetry query window when start_time is not provided",
    )

    # WebSocket Configuration
    ws_heartbeat_interval: int = Field(default=30, description="WebSocket heartbeat interval")
    ws_max_connections: int = Field(default=100, description="Max WebSocket connections")
    ws_ticket_ttl_seconds: int = Field(default=30, description="Lifetime of single-use WebSocket tickets")

    # API Configuration
    # ✅ MUST MATCH UI
    api_prefix: str = Field(default="/api/v1/data", description="API route prefix")
    frontend_base_url: str = Field(
        default=os.getenv("FRONTEND_BASE_URL", "http://localhost:3000"),
        description="Primary frontend base URL",
    )
    data_allowed_origins: str = Field(
        default=os.getenv("DATA_ALLOWED_ORIGINS", ""),
        description="Additional comma-separated CORS origins for data-service",
    )

    @validator("mqtt_qos")
    def validate_mqtt_qos(cls, v: int) -> int:
        if v not in [0, 1, 2]:
            raise ValueError("MQTT QoS must be 0, 1, or 2")
        return v

    @validator("log_level")
    def validate_log_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of {valid_levels}")
        return v.upper()

    @validator("dlq_backend")
    def validate_dlq_backend(cls, v: str) -> str:
        normalized = v.lower().strip()
        if normalized not in {"mysql", "file"}:
            raise ValueError("dlq_backend must be either 'mysql' or 'file'")
        return normalized

    @validator("queue_overflow_log_level")
    def validate_queue_overflow_log_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        normalized = v.upper().strip()
        if normalized not in valid_levels:
            raise ValueError(f"queue_overflow_log_level must be one of {valid_levels}")
        return normalized

    class Config:
        env_file = str(ENV_PATH)
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"

    @property
    def mysql_async_url(self) -> str:
        password = quote_plus(self.mysql_password or "")
        return (
            f"mysql+aiomysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @property
    def mysql_sync_url(self) -> str:
        password = quote_plus(self.mysql_password or "")
        return (
            f"mysql+pymysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )


settings = Settings()

for _name in (
    "MQTT_PASSWORD",
    "INFLUXDB_URL",
    "INFLUXDB_TOKEN",
    "DEVICE_SERVICE_URL",
    "ENERGY_SERVICE_URL",
    "MYSQL_PASSWORD",
    "RULE_ENGINE_URL",
):
    if getattr(settings, _name.lower() if _name.isupper() else _name) is None:
        logger.warning("Missing environment variable for data-service setting: %s", _name)
