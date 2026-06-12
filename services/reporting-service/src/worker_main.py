import asyncio
import logging

from src.workers.report_worker import ReportWorker
from services.shared.debug_bootstrap import init_debug
from services.shared.startup_contract import validate_startup_contract

init_debug()


logging.basicConfig(level=logging.INFO)


def main() -> None:
    validate_startup_contract()
    asyncio.run(ReportWorker().start())


if __name__ == "__main__":
    main()
