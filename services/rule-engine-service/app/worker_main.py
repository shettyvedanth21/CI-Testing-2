import asyncio

from services.shared.debug_bootstrap import init_debug
from app.alert_rate_limiter import close_alert_rate_limiter
from app.database import worker_engine
from app.shared_http import close_shared_http_clients
from app.workers.notification_worker import NotificationWorker, recover_stale_attempted_on_startup

init_debug()


async def _startup_and_run() -> None:
    await recover_stale_attempted_on_startup()
    worker = NotificationWorker()
    try:
        await worker.start()
    finally:
        await worker.stop()
        await close_alert_rate_limiter()
        await close_shared_http_clients()
        await worker_engine.dispose()


def main() -> None:
    asyncio.run(_startup_and_run())


if __name__ == "__main__":
    main()
