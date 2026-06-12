from __future__ import annotations

import os
import sys

from sqlalchemy.exc import OperationalError

sys.path.insert(0, "/Users/vedanthshetty/Desktop/GIT-Testing/FactoryOPS-Cittagent-Obeya-main/services/analytics-service")

os.environ["MYSQL_HOST"] = "localhost"
os.environ["MYSQL_PORT"] = "3306"
os.environ["MYSQL_DATABASE"] = "ai_factoryops"
os.environ["MYSQL_USER"] = "energy"
os.environ["MYSQL_PASSWORD"] = "energy"
os.environ["MYSQL_POOL_RECYCLE"] = "1234"

from src.config.settings import get_settings
from src.infrastructure.database import engine, is_transient_disconnect


def test_analytics_database_engine_enables_pre_ping_and_recycle():
    assert engine.sync_engine.pool._pre_ping is True
    assert engine.sync_engine.pool._recycle == 1234


def test_analytics_settings_default_device_service_url_matches_runtime_port():
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.device_service_url == "http://device-service:8000"


def test_is_transient_disconnect_treats_mysql_connect_failures_as_recoverable():
    class DummyOrig(Exception):
        def __init__(self):
            self.args = (2003, "Can't connect to MySQL server")

    error = OperationalError("SELECT 1", {}, DummyOrig())
    assert is_transient_disconnect(error) is True
