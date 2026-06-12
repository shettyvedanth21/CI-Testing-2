from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings

_TRUSTED_PREFIXES = (
    "10.",
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "192.168.",
    "127.",
    "::1",
    "fc",
    "fd",
)


def _is_trusted_proxy(host: str) -> bool:
    return any(host.startswith(p) for p in _TRUSTED_PREFIXES)


def _proxy_aware_key(request: Request) -> str:
    direct_host = request.client.host if request.client else None
    if direct_host and _is_trusted_proxy(direct_host):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
            if client_ip:
                return client_ip
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
    return direct_host or "unknown"


limiter = Limiter(key_func=_proxy_aware_key, storage_uri=settings.REDIS_URL)


def configure_rate_limiting(app: FastAPI) -> None:
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
