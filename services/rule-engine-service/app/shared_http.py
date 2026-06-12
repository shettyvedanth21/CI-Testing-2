from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_internal_client: Optional[httpx.AsyncClient] = None
_twilio_client: Optional[httpx.AsyncClient] = None


def get_internal_http_client() -> httpx.AsyncClient:
    global _internal_client
    if _internal_client is None or _internal_client.is_closed:
        _internal_client = httpx.AsyncClient(timeout=10.0)
    return _internal_client


def get_twilio_http_client() -> httpx.AsyncClient:
    global _twilio_client
    if _twilio_client is None or _twilio_client.is_closed:
        _twilio_client = httpx.AsyncClient(
            timeout=10.0,
            auth=(settings.TWILIO_ACCOUNT_SID or "", settings.TWILIO_AUTH_TOKEN or ""),
        )
    return _twilio_client


async def close_shared_http_clients() -> None:
    global _internal_client, _twilio_client
    for name, client in [("internal", _internal_client), ("twilio", _twilio_client)]:
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
                logger.info("shared_http_client_closed", extra={"client": name})
            except Exception:
                pass
    _internal_client = None
    _twilio_client = None
