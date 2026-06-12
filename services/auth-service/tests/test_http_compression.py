import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, SERVICES_ROOT, AUTH_SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(AUTH_SERVICE_ROOT)

from services.shared.http_compression import add_api_response_compression


@pytest.mark.asyncio
async def test_non_streaming_gzip_compresses_large_json_payload():
    app = FastAPI()
    add_api_response_compression(app)

    @app.get("/large")
    async def _large():
        return {"payload": "x" * 4000}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/large", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["payload"] == "x" * 4000


@pytest.mark.asyncio
async def test_non_streaming_gzip_skips_small_payloads():
    app = FastAPI()
    add_api_response_compression(app)

    @app.get("/small")
    async def _small():
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/small", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_non_streaming_gzip_skips_sse_streams():
    app = FastAPI()
    add_api_response_compression(app)

    async def _event_stream():
        yield b"data: hello\n\n"

    @app.get("/stream")
    async def _stream():
        return StreamingResponse(_event_stream(), media_type="text/event-stream")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/stream", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in response.headers
    assert response.text == "data: hello\n\n"


@pytest.mark.asyncio
async def test_non_streaming_gzip_compresses_chunked_json_responses():
    app = FastAPI()
    add_api_response_compression(app)

    async def _json_stream():
        yield b'{"payload":"'
        yield b'x' * 4000
        yield b'"}'

    @app.get("/chunked-json")
    async def _chunked_json():
        return StreamingResponse(_json_stream(), media_type="application/json")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/chunked-json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["payload"] == "x" * 4000
