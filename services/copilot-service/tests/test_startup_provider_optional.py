import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from src import main as main_module


class _FakeResult:
    pass


class _FakeConn:
    async def execute(self, _query):
        return _FakeResult()


class _FakeConnContext:
    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeEngine:
    def connect(self):
        return _FakeConnContext()

    async def dispose(self):
        return None


class _UnavailableModelClient:
    @staticmethod
    def is_provider_configured() -> bool:
        return False

    def is_available(self) -> bool:
        return False

    async def ping(self) -> bool:
        return False


def test_lifespan_starts_without_provider_configuration(monkeypatch):
    monkeypatch.setattr(main_module, "validate_startup_contract", lambda: None)
    monkeypatch.setattr(main_module, "readonly_engine", _FakeEngine())
    monkeypatch.setattr(main_module, "engine", _FakeEngine())

    async def _fake_load_schema():
        return None

    monkeypatch.setattr(main_module, "load_schema", _fake_load_schema)
    monkeypatch.setattr(main_module, "ModelClient", _UnavailableModelClient)

    main_module.startup_state.update(
        {
            "schema_loaded": False,
            "curated_mode_available": True,
            "provider_optional": True,
            "provider_configured": False,
            "provider_available": False,
            "provider_ping": False,
            "db_ready": False,
        }
    )

    async def _run():
        async with main_module.lifespan(main_module.app):
            assert main_module.startup_state["db_ready"] is True
            assert main_module.startup_state["schema_loaded"] is True
            assert main_module.startup_state["curated_mode_available"] is True
            assert main_module.startup_state["provider_optional"] is True
            assert main_module.startup_state["provider_configured"] is False
            assert main_module.startup_state["provider_available"] is False

    asyncio.run(_run())
