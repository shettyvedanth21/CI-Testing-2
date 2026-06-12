from __future__ import annotations

import os

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# Keep the reporting test suite hermetic even when a developer shell has loaded
# production-like Redis settings from .env/.env.local. The queue tests inject
# fake Redis clients directly; the FastAPI rate limiter should never dial Redis
# during local unit tests.
os.environ["REDIS_URL"] = "memory://"
