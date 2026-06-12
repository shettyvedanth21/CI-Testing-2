"""Dedicated worker entrypoint for durable telemetry pipeline stages."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVICES_DIR = BASE_DIR.parent
PROJECT_ROOT = SERVICES_DIR.parent
for path in (PROJECT_ROOT, SERVICES_DIR, BASE_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from src.config import settings
from src.main import app_state
from src.utils import configure_logging, get_logger
from services.shared.debug_bootstrap import init_debug
from services.shared.startup_contract import validate_startup_contract

init_debug()

configure_logging(settings.log_level)
logger = get_logger(__name__)


async def _run() -> None:
    validate_startup_contract()
    await app_state.startup()
    logger.info("Data telemetry worker running", role=settings.app_role)
    stop_event = asyncio.Event()

    def _request_stop(*_args) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    await stop_event.wait()
    await app_state.shutdown()


if __name__ == "__main__":
    asyncio.run(_run())
