"""CORS origin helpers for the data-service."""

from __future__ import annotations

import os


LOCAL_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:3002",
    "http://127.0.0.1:3002",
    "http://32.193.53.87:3000",
    "http://32.193.53.87:3001",
    "http://32.193.53.87:3002",
]


def build_allowed_origins(frontend_base_url: str, explicit_origins: str = "") -> list[str]:
    origins = set(LOCAL_DEV_ORIGINS)
    for candidate in (
        frontend_base_url,
        os.getenv("EXTERNAL_URL", ""),
        os.getenv("NEXT_PUBLIC_API_URL", ""),
    ):
        normalized = (candidate or "").strip().rstrip("/")
        if normalized:
            origins.add(normalized)
    for value in explicit_origins.split(","):
        normalized = value.strip().rstrip("/")
        if normalized:
            origins.add(normalized)
    return sorted(origins)
