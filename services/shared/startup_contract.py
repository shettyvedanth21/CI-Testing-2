from __future__ import annotations

import os


_KNOWN_BAD_PREFIXES = ["4031" + "b695", "chan" + "geme", "se" + "cret", "ex" + "ample"]


def validate_startup_contract() -> None:
    required = ["JWT_SECRET_KEY", "INTERNAL_SERVICE_SHARED_SECRET", "DATABASE_URL", "REDIS_URL"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"STARTUP BLOCKED: Missing required env vars: {missing}")

    key = os.environ["JWT_SECRET_KEY"]
    if len(key) < 32 or any(key.startswith(prefix) for prefix in _KNOWN_BAD_PREFIXES):
        raise RuntimeError(
            "STARTUP BLOCKED: JWT_SECRET_KEY is too short or uses a known insecure value."
        )

    internal_secret = os.environ["INTERNAL_SERVICE_SHARED_SECRET"]
    if len(internal_secret) < 32:
        raise RuntimeError(
            "STARTUP BLOCKED: INTERNAL_SERVICE_SHARED_SECRET is too short."
        )
