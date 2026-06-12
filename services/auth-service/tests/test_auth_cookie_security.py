import os
import sys
import logging
from pathlib import Path
from unittest.mock import ANY, AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, SERVICES_ROOT, AUTH_SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(AUTH_SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from fastapi.exceptions import RequestValidationError

from app.api.v1 import auth as auth_api
from app.api.v1.auth import router as auth_router
from app.database import get_db
from app.main import SensitiveDataFilter, validation_exception_handler
from app.rate_limit import configure_rate_limiting
from app.schemas.auth import TokenResponse


class FakeDBSession:
    async def execute(self, *args, **kwargs):
        return None


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    configure_rate_limiting(app)
    app.include_router(auth_router)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    async def _override_get_db():
        yield FakeDBSession()

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.11", 123)),
        base_url="http://testserver",
    ) as async_client:
        yield async_client

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_login_sets_refresh_cookie_and_omits_refresh_token_from_body(client, monkeypatch):
    token_response = TokenResponse(
        access_token="access-token",
        refresh_token="refresh-secret",
        expires_in=900,
    )
    monkeypatch.setattr(auth_api.auth_svc, "login", AsyncMock(return_value=(None, token_response)))

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "secret123"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "access-token"
    assert response.json()["refresh_token"] is None
    set_cookie = response.headers.get("set-cookie", "")
    assert "refresh_token=refresh-secret" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/backend/auth/api/v1/auth" in set_cookie


@pytest.mark.asyncio
async def test_refresh_uses_cookie_transport_without_body(client, monkeypatch):
    token_response = TokenResponse(
        access_token="fresh-access-token",
        refresh_token="fresh-refresh-token",
        expires_in=900,
    )
    refresh = AsyncMock(return_value=token_response)
    monkeypatch.setattr(auth_api.auth_svc, "refresh", refresh)

    response = await client.post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": "cookie-refresh-token"},
        headers={"origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert response.json()["refresh_token"] is None
    refresh.assert_awaited_once_with(ANY, "cookie-refresh-token")
    set_cookie = response.headers.get("set-cookie", "")
    assert "refresh_token=fresh-refresh-token" in set_cookie
    assert "HttpOnly" in set_cookie


@pytest.mark.asyncio
async def test_refresh_blocks_cross_site_origin_when_cookie_present(client, monkeypatch):
    monkeypatch.setattr(
        auth_api.auth_svc,
        "refresh",
        AsyncMock(
            return_value=TokenResponse(
                access_token="fresh-access-token",
                refresh_token="fresh-refresh-token",
                expires_in=900,
            )
        ),
    )

    response = await client.post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": "cookie-refresh-token"},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INVALID_ORIGIN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "message"),
    [
        ("INVALID_REFRESH_TOKEN", "Invalid refresh token"),
        ("REFRESH_TOKEN_REVOKED", "Refresh token revoked"),
        ("REFRESH_TOKEN_EXPIRED", "Refresh token expired"),
    ],
)
async def test_refresh_terminal_token_failures_clear_cookie(client, monkeypatch, error_code, message):
    monkeypatch.setattr(
        auth_api.auth_svc,
        "refresh",
        AsyncMock(
            side_effect=HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": error_code, "message": message},
            )
        ),
    )

    response = await client.post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": "dead-refresh-token"},
        headers={"origin": "http://localhost:3000"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == error_code
    set_cookie = response.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


@pytest.mark.asyncio
async def test_logout_clears_cookie(client, monkeypatch):
    monkeypatch.setattr(auth_api.auth_svc, "logout", AsyncMock(return_value=None))

    response = await client.post(
        "/api/v1/auth/logout",
        cookies={"refresh_token": "cookie-refresh-token"},
        headers={"origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


@pytest.mark.asyncio
async def test_validation_errors_do_not_echo_password_inputs(client):
    invalid_token = "sensitive-test-token"
    response = await client.post(
        "/api/v1/auth/password/reset",
        json={
            "token": invalid_token,
            "password": "SuperSecret123!",
            "confirm_password": "SuperSecret123!",
        },
    )

    assert response.status_code == 422
    body = response.json()
    serialized = str(body)
    assert "SuperSecret123!" not in serialized
    assert invalid_token not in serialized
    assert body["message"] == "Request validation failed"


def test_sensitive_data_filter_redacts_token_and_password_fields():
    record = logging.makeLogRecord(
        {
            "msg": "auth event",
            "password": "SuperSecret123!",
            "refresh_token": "refresh-secret",
            "details": {"authorization": "Bearer raw-token", "nested": {"access_token": "access-secret"}},
        }
    )
    filter_ = SensitiveDataFilter()

    assert filter_.filter(record) is True
    assert record.__dict__["password"] == "[REDACTED]"
    assert record.__dict__["refresh_token"] == "[REDACTED]"
    assert record.__dict__["details"]["authorization"] == "[REDACTED]"
    assert record.__dict__["details"]["nested"]["access_token"] == "[REDACTED]"
