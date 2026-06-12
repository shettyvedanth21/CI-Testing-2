"""Compatibility package for repo-root test imports.

This maps ``import app`` to the device-service application package so local
pytest runs can resolve the same module layout the container uses.
"""

from __future__ import annotations

from pathlib import Path

_DEVICE_APP_DIR = Path(__file__).resolve().parent.parent / "services" / "device-service" / "app"

__path__ = [str(_DEVICE_APP_DIR)]
