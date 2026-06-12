import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.ai.model_client import ModelClient
from src.api.chat import router as chat_router
from src.config import settings
from src.database import engine, readonly_engine
from src.db.schema_loader import load_schema
from src.rate_limit import configure_rate_limiting
from shared.auth_middleware import AuthMiddleware
from shared.feature_entitlements import require_feature
from services.shared.debug_bootstrap import init_debug
from services.shared.startup_contract import validate_startup_contract

init_debug()


logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)
_SENSITIVE_FIELD_NAMES = {
    "authorization",
    "password",
    "confirm_password",
    "refresh_token",
    "access_token",
    "token",
}

startup_state = {
    "schema_loaded": False,
    "curated_mode_available": True,
    "provider_optional": True,
    "provider_configured": False,
    "provider_available": False,
    "provider_ping": False,
    "db_ready": False,
}


def _redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in _SENSITIVE_FIELD_NAMES:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_sensitive_data(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_sensitive_data(item) for item in value]
    return value


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for error in errors:
        clean = {key: value for key, value in error.items() if key != "input"}
        if "ctx" in clean:
            clean["ctx"] = _redact_sensitive_data(clean["ctx"])
        sanitized.append(clean)
    return sanitized


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_startup_contract()
    try:
        async with readonly_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        startup_state["db_ready"] = True

        await load_schema()
        startup_state["schema_loaded"] = True

        model_client = ModelClient()
        startup_state["provider_configured"] = model_client.is_provider_configured()
        startup_state["provider_available"] = model_client.is_available()
        startup_state["provider_ping"] = await model_client.ping() if startup_state["provider_available"] else False

        logger.info("copilot_startup_complete", extra=startup_state)
    except Exception as exc:
        logger.exception("copilot_startup_failed", extra={"error": str(exc)})
    yield
    await engine.dispose()
    await readonly_engine.dispose()


app = FastAPI(title="Factory Copilot Service", version="1.0.0", lifespan=lifespan)
configure_rate_limiting(app)
app.add_middleware(AuthMiddleware)
app.include_router(chat_router, dependencies=[Depends(require_feature("copilot"))])


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "answer": "Invalid request payload.",
            "details": _sanitize_validation_errors(exc.errors()),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception in copilot-service")
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "answer": "Something went wrong.",
        },
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": settings.ai_provider,
        "curated_mode_available": startup_state["curated_mode_available"],
        "provider_optional": startup_state["provider_optional"],
        "provider_configured": startup_state["provider_configured"],
        "provider_available": startup_state["provider_available"],
    }


@app.get("/ready")
async def ready():
    ready_flag = all([startup_state["db_ready"], startup_state["schema_loaded"]])
    return {
        "status": "ready" if ready_flag else "not_ready",
        "checks": startup_state,
    }
