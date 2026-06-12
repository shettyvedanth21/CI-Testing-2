"""Async circuit breaker utility for inter-service HTTP calls."""

from __future__ import annotations

import asyncio
import inspect
import time
from datetime import datetime, timezone
from typing import Any

import logging

logger = logging.getLogger(__name__)

STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        open_timeout_sec: int = 30,
        half_open_max_calls: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.success_threshold = max(1, success_threshold)
        self.open_timeout_sec = max(1, open_timeout_sec)
        self.half_open_max_calls = max(1, half_open_max_calls)
        self._state = STATE_CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_at: datetime | None = None
        self._opened_at_monotonic = 0.0
        self._half_open_in_flight = 0
        self._lock = asyncio.Lock()

    async def call(self, coro) -> tuple[bool, Any]:
        acquired_half_open_slot = False
        async with self._lock:
            now = time.monotonic()
            if self._state == STATE_OPEN:
                if (now - self._opened_at_monotonic) >= self.open_timeout_sec:
                    self._transition_to(STATE_HALF_OPEN)
                else:
                    return False, None

            if self._state == STATE_HALF_OPEN:
                if self._half_open_in_flight >= self.half_open_max_calls:
                    return False, None
                self._half_open_in_flight += 1
                acquired_half_open_slot = True

        try:
            awaitable = coro() if callable(coro) else coro
            if not inspect.isawaitable(awaitable):
                raise TypeError("CircuitBreaker.call expects an awaitable or async callable")
            result = await awaitable
        except Exception:
            async with self._lock:
                self._last_failure_at = datetime.now(timezone.utc)
                self._failure_count += 1
                self._success_count = 0
                if acquired_half_open_slot:
                    self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
                    self._transition_to(STATE_OPEN)
                elif self._failure_count >= self.failure_threshold:
                    self._transition_to(STATE_OPEN)
            return False, None

        async with self._lock:
            if acquired_half_open_slot:
                self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
                self._success_count += 1
                self._failure_count = 0
                if self._success_count >= self.success_threshold:
                    self._transition_to(STATE_CLOSED)
            else:
                self._failure_count = 0
                self._success_count = 0
            return True, result

    def get_state(self) -> str:
        if self._state == STATE_OPEN and (time.monotonic() - self._opened_at_monotonic) >= self.open_timeout_sec:
            return STATE_HALF_OPEN
        return self._state

    def get_metrics(self) -> dict[str, Any]:
        return {
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "state": self.get_state(),
            "last_failure_at": self._last_failure_at.isoformat() if self._last_failure_at else None,
        }

    def _transition_to(self, state: str) -> None:
        if self._state == state:
            return
        self._state = state
        if state == STATE_OPEN:
            self._opened_at_monotonic = time.monotonic()
            self._success_count = 0
        elif state == STATE_HALF_OPEN:
            self._success_count = 0
            self._half_open_in_flight = 0
        elif state == STATE_CLOSED:
            self._failure_count = 0
            self._success_count = 0
            self._half_open_in_flight = 0
        logger.info("Circuit breaker state changed", extra={"circuit_breaker": self.name, "state": state})


_BREAKERS: dict[str, CircuitBreaker] = {}


def get_or_create_circuit_breaker(
    name: str,
    *,
    failure_threshold: int = 5,
    success_threshold: int = 2,
    open_timeout_sec: int = 30,
    half_open_max_calls: int = 1,
) -> CircuitBreaker:
    breaker = _BREAKERS.get(name)
    if breaker is None:
        breaker = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            success_threshold=success_threshold,
            open_timeout_sec=open_timeout_sec,
            half_open_max_calls=half_open_max_calls,
        )
        _BREAKERS[name] = breaker
    return breaker


def get_circuit_breaker_metrics() -> dict[str, dict[str, Any]]:
    return {name: breaker.get_metrics() for name, breaker in _BREAKERS.items()}
