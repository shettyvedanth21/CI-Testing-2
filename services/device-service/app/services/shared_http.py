"""Shared async HTTP client pool for device-service inter-service calls.

Provides long-lived, connection-pooled httpx.AsyncClient instances per target
service base URL, eliminating per-call ephemeral client creation overhead.

Clients are created lazily on first access and must be closed during shutdown
via ``close_all()``.

Timeout strategy: the client itself has a generous default timeout. Callers
should pass an explicit ``timeout`` to individual ``client.get()`` /
``client.post()`` calls to control per-request deadlines. The ``get_client``
function does NOT set per-client timeout — timeout is a per-request concern.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import httpx
from app.config import settings

logger = logging.getLogger(__name__)

_CLIENT_TIMEOUT = 30.0
_MAX_CONNECTIONS = 20
_MAX_KEEPALIVE_CONNECTIONS = 10
_KEEPALIVE_EXPIRY = 30.0

_lock: asyncio.Lock | None = None
_clients: dict[str, httpx.AsyncClient] = {}
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _build_client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=httpx.Timeout(_CLIENT_TIMEOUT),
        limits=httpx.Limits(
            max_connections=_MAX_CONNECTIONS,
            max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=_KEEPALIVE_EXPIRY,
        ),
    )


async def get_client(base_url: str) -> httpx.AsyncClient:
    if not base_url:
        raise ValueError("base_url must not be empty")
    key = base_url.rstrip("/")
    if key in _clients:
        return _clients[key]
    lock = _get_lock()
    async with lock:
        if key in _clients:
            return _clients[key]
        client = _build_client(base_url)
        _clients[key] = client
        logger.info("shared_http_client_created", extra={"base_url": key})
        return client


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    operation: str,
    retries: int | None = None,
    backoff_ms: int | None = None,
    retryable_status_codes: Iterable[int] | None = None,
    **kwargs,
) -> httpx.Response:
    configured_retries = settings.INTERNAL_SERVICE_RETRIES if retries is None else retries
    total_attempts = max(1, configured_retries + 1)
    delay_ms = max(0, settings.INTERNAL_SERVICE_RETRY_BACKOFF_MS if backoff_ms is None else backoff_ms)
    retryable_codes = set(retryable_status_codes or _RETRYABLE_STATUS_CODES)

    for attempt in range(1, total_attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except _RETRYABLE_EXCEPTIONS as exc:
            if attempt >= total_attempts:
                raise
            logger.warning(
                "shared_http_retry_transport_error",
                extra={
                    "operation": operation,
                    "method": method,
                    "url": url,
                    "attempt": attempt,
                    "total_attempts": total_attempts,
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            if delay_ms:
                await asyncio.sleep((delay_ms * attempt) / 1000.0)
            continue

        if response.status_code in retryable_codes and attempt < total_attempts:
            logger.warning(
                "shared_http_retry_status_code",
                extra={
                    "operation": operation,
                    "method": method,
                    "url": url,
                    "attempt": attempt,
                    "total_attempts": total_attempts,
                    "status_code": response.status_code,
                },
            )
            await response.aclose()
            if delay_ms:
                await asyncio.sleep((delay_ms * attempt) / 1000.0)
            continue

        return response

    raise RuntimeError(f"unreachable retry loop for {operation}")


async def close_all() -> None:
    lock = _get_lock()
    async with lock:
        for key, client in _clients.items():
            try:
                await client.aclose()
            except Exception:
                pass
        count = len(_clients)
        _clients.clear()
        if count:
            logger.info("shared_http_clients_closed", extra={"count": count})
