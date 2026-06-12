from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, "/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/services/analytics-service")

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_DATABASE", "ai_factoryops")
os.environ.setdefault("MYSQL_USER", "energy")
os.environ.setdefault("MYSQL_PASSWORD", "energy")

from src.infrastructure import database


class _FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_get_db_session_returns_live_session_instance(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(database, "async_session_maker", lambda: fake_session)

    session = await database.get_db_session()

    assert session is fake_session
    assert fake_session.closed is False

