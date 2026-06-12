import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, SERVICES_ROOT, AUTH_SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(AUTH_SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.api.v1.auth import router as auth_router
from app.api.v1 import auth as auth_api
from app.database import get_db
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

    async def _override_get_db():
        yield FakeDBSession()

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 123)),
        base_url="http://testserver",
    ) as async_client:
        yield async_client

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_login_is_rate_limited_after_ten_requests(client, monkeypatch):
    token_response = TokenResponse(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_in=900,
    )
    monkeypatch.setattr(auth_api.auth_svc, "login", AsyncMock(return_value=(None, token_response)))

    for _ in range(10):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "secret123"},
        )
        assert response.status_code == 200

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "secret123"},
    )

    assert response.status_code == 429


@pytest.mark.asyncio
async def test_password_forgot_is_rate_limited_after_five_requests(client, monkeypatch):
    monkeypatch.setattr(auth_api.auth_svc, "request_password_reset", AsyncMock(return_value=None))

    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": "user@example.com"},
        )
        assert response.status_code == 200

    response = await client.post(
        "/api/v1/auth/password/forgot",
        json={"email": "user@example.com"},
    )

    assert response.status_code == 429


@pytest.mark.asyncio
async def test_invitation_accept_is_rate_limited_after_five_requests(client, monkeypatch):
    monkeypatch.setattr(auth_api.auth_svc, "accept_invitation", AsyncMock(return_value=None))

    payload = {
        "token": "a" * 32,
        "password": "secret123",
        "confirm_password": "secret123",
    }

    for _ in range(5):
        response = await client.post("/api/v1/auth/invitations/accept", json=payload)
        assert response.status_code == 200

    response = await client.post("/api/v1/auth/invitations/accept", json=payload)

    assert response.status_code == 429
