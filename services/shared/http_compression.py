from __future__ import annotations

import gzip
import os
from collections.abc import Awaitable, Callable

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_DEFAULT_MINIMUM_SIZE = 500
_DEFAULT_COMPRESS_LEVEL = 6
_SSE_CONTENT_TYPE = "text/event-stream"
_COMPRESSIBLE_CONTENT_TYPE_PREFIXES = (
    "application/json",
    "application/javascript",
    "application/xml",
    "text/",
)


def _load_minimum_size() -> int:
    raw_value = os.environ.get("API_GZIP_MINIMUM_SIZE")
    if raw_value is None:
        return _DEFAULT_MINIMUM_SIZE
    try:
        return max(0, int(raw_value))
    except ValueError:
        return _DEFAULT_MINIMUM_SIZE


def _load_compress_level() -> int:
    raw_value = os.environ.get("API_GZIP_COMPRESS_LEVEL")
    if raw_value is None:
        return _DEFAULT_COMPRESS_LEVEL
    try:
        return min(9, max(1, int(raw_value)))
    except ValueError:
        return _DEFAULT_COMPRESS_LEVEL


class NonStreamingGZipMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        minimum_size: int | None = None,
        compresslevel: int | None = None,
    ) -> None:
        self.app = app
        self.minimum_size = _load_minimum_size() if minimum_size is None else max(0, int(minimum_size))
        self.compresslevel = _load_compress_level() if compresslevel is None else min(9, max(1, int(compresslevel)))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        if "gzip" not in request_headers.get("Accept-Encoding", ""):
            await self.app(scope, receive, send)
            return

        responder = _NonStreamingGZipResponder(
            self.app,
            minimum_size=self.minimum_size,
            compresslevel=self.compresslevel,
            send=send,
        )
        await responder(scope, receive)


class _NonStreamingGZipResponder:
    def __init__(
        self,
        app: ASGIApp,
        *,
        minimum_size: int,
        compresslevel: int,
        send: Send,
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
        self.send = send
        self.initial_message: Message | None = None
        self.body_chunks: list[bytes] = []
        self.streaming_passthrough = False
        self.response_started = False
        self.content_encoding_set = False
        self.skip_for_content_type = False
        self.compressible_content_type = False

    async def __call__(self, scope: Scope, receive: Receive) -> None:
        await self.app(scope, receive, self.send_with_optional_gzip)

    async def send_with_optional_gzip(self, message: Message) -> None:
        message_type = message["type"]

        if message_type == "http.response.start":
            self.initial_message = message
            headers = Headers(raw=message["headers"])
            self.content_encoding_set = "content-encoding" in headers
            content_type = headers.get("content-type", "")
            self.skip_for_content_type = _SSE_CONTENT_TYPE in content_type.lower()
            self.compressible_content_type = content_type.lower().startswith(_COMPRESSIBLE_CONTENT_TYPE_PREFIXES)
            return

        if message_type != "http.response.body":
            await self.send(message)
            return

        if self.initial_message is None:
            await self.send(message)
            return

        if self.streaming_passthrough:
            await self.send(message)
            return

        body = message.get("body", b"")
        more_body = bool(message.get("more_body", False))

        if more_body:
            if self.skip_for_content_type or not self.compressible_content_type or self.content_encoding_set:
                self.streaming_passthrough = True
                await self._flush_passthrough()
                await self.send(message)
                return

            self.body_chunks.append(body)
            return

        self.body_chunks.append(body)
        full_body = b"".join(self.body_chunks)

        if self.content_encoding_set or self.skip_for_content_type or len(full_body) < self.minimum_size:
            await self._send_uncompressed(full_body)
            return

        compressed_body = gzip.compress(full_body, compresslevel=self.compresslevel)
        headers = MutableHeaders(raw=self.initial_message["headers"])
        headers["Content-Encoding"] = "gzip"
        headers["Content-Length"] = str(len(compressed_body))
        headers.add_vary_header("Accept-Encoding")

        await self.send(self.initial_message)
        await self.send({"type": "http.response.body", "body": compressed_body, "more_body": False})
        self.response_started = True

    async def _flush_passthrough(self) -> None:
        if self.response_started:
            return
        await self.send(self.initial_message)  # type: ignore[arg-type]
        self.response_started = True
        if self.body_chunks:
            await self.send({"type": "http.response.body", "body": b"".join(self.body_chunks), "more_body": True})
            self.body_chunks = []

    async def _send_uncompressed(self, body: bytes) -> None:
        if not self.response_started:
            await self.send(self.initial_message)  # type: ignore[arg-type]
            self.response_started = True
        await self.send({"type": "http.response.body", "body": body, "more_body": False})


def add_api_response_compression(app: ASGIApp) -> None:
    app.add_middleware(NonStreamingGZipMiddleware)
