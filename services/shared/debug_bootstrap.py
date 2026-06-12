from __future__ import annotations

import logging
import os

logger = logging.getLogger("debug_bootstrap")


def init_debug() -> None:
    enable = os.environ.get("DEBUGPY_ENABLE", "").strip().lower() == "true"
    if not enable:
        return

    port_str = os.environ.get("DEBUGPY_PORT", "0").strip()
    try:
        port = int(port_str)
    except ValueError:
        logger.warning("DEBUGPY_PORT is not an integer (%s), skipping debugpy", port_str)
        return

    if port <= 0:
        logger.warning("DEBUGPY_ENABLE=true but DEBUGPY_PORT is invalid (%s), skipping debugpy", port_str)
        return

    host = os.environ.get("DEBUGPY_HOST", "0.0.0.0").strip()

    try:
        import debugpy

        debugpy.listen((host, port))
        logger.info("debugpy listening on %s:%d", host, port)
    except Exception as exc:
        logger.warning("debugpy.listen() failed: %s", exc)
        return

    wait = os.environ.get("DEBUGPY_WAIT_FOR_CLIENT", "").strip().lower() == "true"
    if wait:
        logger.info("debugpy waiting for client on %s:%d ...", host, port)
        debugpy.wait_for_client()
        logger.info("debugpy client attached")
