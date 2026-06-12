from __future__ import annotations

import sys
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))
SERVICE_ROOT = REPO_ROOT / "services" / "copilot-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MYSQL_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("MYSQL_READONLY_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")

from src.main import validation_exception_handler
from src.response.schema import ChatRequest


def test_copilot_validation_handler_does_not_echo_request_input() -> None:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.post("/chat")
    async def chat(body: ChatRequest) -> dict[str, str]:
        return {"ok": "true"}

    client = TestClient(app)
    response = client.post(
        "/chat",
        json={
            "message": "",
            "conversation_history": [{"role": "user", "content": "Bearer secret-token-value"}],
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "VALIDATION_ERROR"
    assert all("input" not in item for item in payload["details"])
    assert "secret-token-value" not in str(payload)
