"""
Polling helpers for live async jobs.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import pytest


def wait_until(
    fn: Callable[[], Any],
    timeout_sec: int = 60,
    poll_sec: float = 2.0,
    description: str = "condition",
) -> Any:
    deadline = time.time() + timeout_sec
    last_err = None
    last_value = None

    while time.time() < deadline:
        try:
            result = fn()
            if result:
                return result
            last_value = result
        except Exception as exc:
            last_err = exc
        time.sleep(poll_sec)

    raise TimeoutError(
        f"Timed out after {timeout_sec}s waiting for: {description}\n"
        f"Last value: {last_value}\n"
        f"Last error: {last_err}"
    )


def wait_for_job(
    status_fn: Callable[[], dict],
    timeout_sec: int = 180,
    description: str = "job",
) -> dict:
    def _poll():
        try:
            status = status_fn()
        except Exception:
            return None
        value = (status.get("status") or status.get("state") or "").lower()
        if value in ("failed", "error"):
            pytest.fail(f"{description} entered failed state.\nStatus response: {status}")
        if value == "completed":
            return status
        return None

    return wait_until(
        _poll,
        timeout_sec=timeout_sec,
        poll_sec=3.0,
        description=f"{description} to complete",
    )


def wait_for_download_ready(
    status_fn: Callable[[], dict],
    timeout_sec: int = 120,
    description: str = "download",
) -> dict:
    def _poll():
        try:
            status = status_fn()
        except Exception:
            return None
        if status.get("download_ready"):
            return status
        if status.get("artifact_ready"):
            return status
        return None

    return wait_until(
        _poll,
        timeout_sec=timeout_sec,
        poll_sec=3.0,
        description=f"{description} artifact to be ready",
    )
