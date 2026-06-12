"""Configure InfluxDB bucket retention on service startup."""

import os

from src.utils import get_logger

logger = get_logger(__name__)

RETENTION_DAYS = int(os.getenv("INFLUXDB_RETENTION_DAYS", "365"))


async def ensure_bucket_retention(influxdb_client) -> None:
    """
    Set retention policy on the telemetry bucket to INFLUXDB_RETENTION_DAYS days.
    Idempotent and safe to call on every startup.
    """
    try:
        buckets_api = influxdb_client.buckets_api()
        bucket_name = os.getenv("INFLUXDB_BUCKET", "telemetry")

        buckets = buckets_api.find_buckets().buckets
        target = next((bucket for bucket in buckets if bucket.name == bucket_name), None)
        if target is None:
            logger.warning("influxdb_bucket_not_found", bucket=bucket_name)
            return

        retention_seconds = RETENTION_DAYS * 24 * 3600
        current_retention = (
            target.retention_rules[0].every_seconds
            if getattr(target, "retention_rules", None)
            else 0
        )

        if current_retention == retention_seconds:
            logger.info("influxdb_retention_already_set", days=RETENTION_DAYS)
            return

        from influxdb_client.domain.bucket_retention_rules import BucketRetentionRules

        target.retention_rules = [
            BucketRetentionRules(
                every_seconds=retention_seconds,
                type="expire",
            )
        ]
        buckets_api.update_bucket(bucket=target)
        logger.info(
            "influxdb_retention_configured",
            days=RETENTION_DAYS,
            seconds=retention_seconds,
        )
    except Exception as exc:
        logger.warning("influxdb_retention_setup_failed", error=str(exc))
