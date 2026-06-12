import logging
import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress
from typing import Any

from fastapi import FastAPI, Request, status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.orgs import router as tenants_router
from app.api.v1.platform_maintenance import router as platform_maintenance_router
from app.config import settings
from app.cors import build_allowed_origins
from app.database import AsyncSessionFactory, engine
from app.rate_limit import configure_rate_limiting
from app.services.bootstrap_service import (
    ensure_bootstrap_super_admin,
    ensure_local_bootstrap_state,
    ensure_tenant_allocator_state,
)
from app.services.platform_maintenance_delivery import platform_maintenance_delivery_worker
from app.services.token_cleanup_service import refresh_token_cleanup_svc
from services.shared.debug_bootstrap import init_debug
from services.shared.http_compression import add_api_response_compression
from services.shared.startup_contract import validate_startup_contract

init_debug()

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger("auth-service")

_SENSITIVE_FIELD_NAMES = {
    "authorization",
    "password",
    "confirm_password",
    "refresh_token",
    "access_token",
    "token",
}


def _public_dependency_error(_exc: Exception) -> str:
    return "dependency_check_failed"


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
    return value


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for error in errors:
        clean = {key: value for key, value in error.items() if key != "input"}
        if "ctx" in clean:
            clean["ctx"] = _redact_sensitive_data(clean["ctx"])
        sanitized.append(clean)
    return sanitized


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in list(record.__dict__.items()):
            if key.lower() in _SENSITIVE_FIELD_NAMES:
                record.__dict__[key] = "[REDACTED]"
            elif isinstance(value, (dict, list)):
                record.__dict__[key] = _redact_sensitive_data(value)
        return True


for _logger_name in ("auth-service", "auth-service.auth", "auth-service.mailer"):
    logging.getLogger(_logger_name).addFilter(SensitiveDataFilter())


def validate_bootstrap_contract() -> None:
    if settings.is_production and settings.LOCAL_BOOTSTRAP_ENABLED:
        raise RuntimeError("STARTUP BLOCKED: LOCAL_BOOTSTRAP_ENABLED cannot be true in production.")

    if not settings.BOOTSTRAP_SUPER_ADMIN_ENABLED:
        return

    required = {
        "BOOTSTRAP_SUPER_ADMIN_EMAIL": settings.BOOTSTRAP_SUPER_ADMIN_EMAIL.strip(),
        "BOOTSTRAP_SUPER_ADMIN_PASSWORD": settings.BOOTSTRAP_SUPER_ADMIN_PASSWORD,
        "BOOTSTRAP_SUPER_ADMIN_FULL_NAME": settings.BOOTSTRAP_SUPER_ADMIN_FULL_NAME.strip(),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            f"STARTUP BLOCKED: Missing bootstrap super-admin settings while "
            f"BOOTSTRAP_SUPER_ADMIN_ENABLED=true: {missing}"
        )

    if len(settings.BOOTSTRAP_SUPER_ADMIN_PASSWORD) < 12:
        raise RuntimeError(
            "STARTUP BLOCKED: BOOTSTRAP_SUPER_ADMIN_PASSWORD must be at least 12 characters."
        )


def validate_auth_email_contract() -> None:
    if not settings.EMAIL_ENABLED:
        raise RuntimeError("STARTUP BLOCKED: Email-based invite/reset flows require EMAIL_ENABLED=true.")

    required = {
        "EMAIL_SMTP_HOST": settings.EMAIL_SMTP_HOST,
        "EMAIL_FROM_ADDRESS": settings.EMAIL_FROM_ADDRESS,
        "FRONTEND_BASE_URL": settings.FRONTEND_BASE_URL,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"STARTUP BLOCKED: Missing auth email settings: {missing}")

    if settings.EMAIL_SMTP_USERNAME and not settings.EMAIL_SMTP_PASSWORD:
        raise RuntimeError("STARTUP BLOCKED: EMAIL_SMTP_USERNAME is configured but EMAIL_SMTP_PASSWORD is missing.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_startup_contract()
    validate_bootstrap_contract()
    validate_auth_email_contract()
    logger.info(f"auth-service starting — environment={settings.ENVIRONMENT}")
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("DB connection verified")
    async with AsyncSessionFactory() as session:
        tenant_allocator_updated = await ensure_tenant_allocator_state(session)
        local_bootstrap = await ensure_local_bootstrap_state(session)
        created = False
        if settings.BOOTSTRAP_SUPER_ADMIN_ENABLED:
            created = await ensure_bootstrap_super_admin(session)
    if tenant_allocator_updated:
        logger.info("Tenant allocator state ensured", extra={"prefix": "SH"})
    if any(local_bootstrap.values()):
        logger.info("Local auth bootstrap ensured", extra=local_bootstrap)
    if created:
        logger.info("Bootstrap super-admin created", extra={"email": settings.BOOTSTRAP_SUPER_ADMIN_EMAIL})
    cleanup_task = asyncio.create_task(refresh_token_cleanup_svc.run_forever())
    platform_maintenance_task = asyncio.create_task(platform_maintenance_delivery_worker.start())
    yield
    await platform_maintenance_delivery_worker.stop()
    platform_maintenance_task.cancel()
    with suppress(asyncio.CancelledError):
        await platform_maintenance_task
    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task
    await engine.dispose()
    logger.info("auth-service shutdown complete")


app = FastAPI(
    title="FactoryOPS Auth Service",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None,
)

configure_rate_limiting(app)
add_api_response_compression(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=build_allowed_origins(settings.FRONTEND_BASE_URL),
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|32\.193\.53\.87)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(tenants_router)
app.include_router(platform_maintenance_router)


async def _db_health_status() -> tuple[str, str | None]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "connected", None
    except Exception as exc:  # pragma: no cover - exercised via endpoint tests
        logger.warning("auth_health_database_check_failed", extra={"error": str(exc)})
        return "unavailable", _public_dependency_error(exc)


async def _redis_health_status() -> tuple[str, str | None]:
    try:
        await asyncio.to_thread(refresh_token_cleanup_svc._get_redis_client().ping)
        return "connected", None
    except RedisError as exc:
        logger.warning("auth_health_redis_check_failed", extra={"error": str(exc)})
        return "unavailable", _public_dependency_error(exc)
    except Exception as exc:  # pragma: no cover - exercised via endpoint tests
        logger.warning("auth_health_redis_check_failed", extra={"error": str(exc)})
        return "unavailable", _public_dependency_error(exc)


@app.get("/health", tags=["ops"])
async def health():
    db_state, db_error = await _db_health_status()
    redis_state, redis_error = await _redis_health_status()
    reasons: list[str] = []
    if db_state != "connected":
        reasons.append("database_unavailable")
    if redis_state != "connected":
        reasons.append("redis_unavailable")
    return {
        "status": "ok" if not reasons else "degraded",
        "service": "auth-service",
        "dependencies": {
            "database": {"status": db_state, "error": db_error},
            "redis": {"status": redis_state, "error": redis_error},
        },
        "reasons": reasons,
    }


@app.get("/ready", tags=["ops"])
async def ready():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not ready"})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": jsonable_encoder(
                _sanitize_validation_errors(exc.errors()),
                custom_encoder={ValueError: str},
            ),
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}")
    return JSONResponse(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"},
    )
