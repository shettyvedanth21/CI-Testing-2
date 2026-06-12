"""Dedicated worker entrypoint for analytics jobs."""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import text

from src.config.logging_config import configure_logging
from src.config.settings import get_settings
from src.infrastructure.database import async_session_maker
from src.workers.job_queue import InMemoryJobQueue, RedisJobQueue
from src.workers.job_worker import JobWorker
from services.shared.debug_bootstrap import init_debug
from services.shared.startup_contract import validate_startup_contract

init_debug()

logger = structlog.get_logger()


async def _cleanup_interrupted_jobs() -> int:
    async with async_session_maker() as session:
        result = await session.execute(
            text(
                """
                UPDATE analytics_jobs
                SET status = 'failed',
                    error_code = 'SERVICE_RESTART',
                    error_message = 'Job was interrupted by a service restart. Please resubmit.',
                    message = 'Job was interrupted by a service restart. Please resubmit.',
                    completed_at = UTC_TIMESTAMP(),
                    updated_at = UTC_TIMESTAMP()
                WHERE status = 'running'
                """
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


async def _run_worker() -> None:
    validate_startup_contract()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("analytics_worker_starting")
    cleaned_jobs = await _cleanup_interrupted_jobs()
    if cleaned_jobs:
        logger.warning(
            "analytics_worker_interrupted_jobs_cleaned",
            count=cleaned_jobs,
            restarted_at=datetime.now(timezone.utc).isoformat(),
        )

    if settings.queue_backend == "redis":
        queue = RedisJobQueue(
            redis_url=settings.redis_url,
            stream_name=settings.redis_stream_name,
            dead_letter_stream=settings.redis_dead_letter_stream,
            consumer_group=settings.redis_consumer_group,
            consumer_name=settings.redis_consumer_name,
            maxsize=settings.queue_max_length,
        )
    else:
        queue = InMemoryJobQueue(maxsize=settings.queue_max_length)

    worker = JobWorker(queue, max_concurrent=settings.max_concurrent_jobs)
    await worker.start()


def main() -> None:
    asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
