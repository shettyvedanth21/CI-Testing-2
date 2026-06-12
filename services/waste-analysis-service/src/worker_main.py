import asyncio
import logging

from src.workers.waste_worker import WasteWorker
from services.shared.debug_bootstrap import init_debug
from services.shared.startup_contract import validate_startup_contract

init_debug()


logging.basicConfig(level=logging.INFO)


def main() -> None:
    validate_startup_contract()
    asyncio.run(WasteWorker().start())


if __name__ == "__main__":
    main()
