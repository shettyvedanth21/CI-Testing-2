from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_auth_service_uses_dedicated_alembic_version_table() -> None:
    env_source = (PROJECT_ROOT / "services" / "auth-service" / "alembic" / "env.py").read_text()

    assert 'VERSION_TABLE_NAME = "alembic_version_auth"' in env_source
    assert 'LEGACY_VERSION_TABLE_NAME = "alembic_version"' in env_source
    assert "_head_schema_is_present" in env_source
    assert "platform_maintenance_announcements" in env_source


def test_data_service_uses_dedicated_alembic_version_table() -> None:
    env_source = (PROJECT_ROOT / "services" / "data-service" / "alembic" / "env.py").read_text()

    assert 'VERSION_TABLE_NAME = "alembic_version_data"' in env_source
    assert 'LEGACY_VERSION_TABLE_NAME = "alembic_version"' in env_source
    assert "_head_schema_is_present" in env_source
    assert "telemetry_outbox" in env_source
