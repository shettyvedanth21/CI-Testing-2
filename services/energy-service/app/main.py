from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.services.broadcaster import energy_broadcaster
from app.services.device_meta import meta_cache
from app.services.tariff_cache import tariff_cache
from shared.auth_middleware import AuthMiddleware
from services.shared.debug_bootstrap import init_debug
from services.shared.http_compression import add_api_response_compression
from services.shared.startup_contract import validate_startup_contract

init_debug()


async def _close_shared_http_clients() -> None:
    for cache in (meta_cache, tariff_cache):
        client = getattr(cache, "_client", None)
        if client is not None and not client.is_closed:
            await client.aclose()


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_startup_contract()
    await energy_broadcaster.start(settings.REDIS_URL, settings.ENERGY_STREAM_REDIS_CHANNEL)
    yield
    await _close_shared_http_clients()
    await energy_broadcaster.stop()


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)
add_api_response_compression(app)
app.add_middleware(AuthMiddleware)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "energy-service"}


app.include_router(router)
