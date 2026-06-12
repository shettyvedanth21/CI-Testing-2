"""Cooldown normalization helpers for rule creation and evaluation."""

from __future__ import annotations

import math
from typing import Optional, Tuple

DEFAULT_COOLDOWN_MINUTES = 15
MAX_COOLDOWN_MINUTES = 7 * 24 * 60
MAX_COOLDOWN_SECONDS = 7 * 24 * 60 * 60


def normalize_cooldown_values(
    *,
    cooldown_mode: Optional[str],
    cooldown_unit: Optional[str],
    cooldown_minutes: Optional[int],
    cooldown_seconds: Optional[int],
    existing_mode: Optional[str] = None,
    existing_unit: Optional[str] = None,
    existing_minutes: Optional[int] = None,
    existing_seconds: Optional[int] = None,
) -> Tuple[str, str, int, int]:
    """
    Normalize cooldown inputs into a canonical representation.

    Returns:
        (cooldown_mode, cooldown_unit, cooldown_minutes, cooldown_seconds)

    The seconds field is the internal source of truth. Minutes remains for
    backward compatibility and display compatibility.
    """

    resolved_mode = (cooldown_mode or existing_mode or "interval").strip()
    if resolved_mode == "no_repeat":
        return resolved_mode, "minutes", 0, 0

    if cooldown_unit is not None:
        resolved_unit = cooldown_unit.strip().lower()
    elif cooldown_seconds is not None and cooldown_minutes is None:
        resolved_unit = "seconds"
    elif cooldown_minutes is not None and cooldown_seconds is None:
        resolved_unit = "minutes"
    elif existing_unit is not None:
        resolved_unit = existing_unit.strip().lower()
    else:
        resolved_unit = "seconds" if cooldown_seconds is not None else "minutes"

    if resolved_unit not in {"minutes", "seconds"}:
        resolved_unit = "seconds" if cooldown_seconds is not None and cooldown_minutes is None else "minutes"

    if resolved_unit == "seconds":
        resolved_seconds = cooldown_seconds
        if resolved_seconds is None:
            resolved_seconds = existing_seconds
        if resolved_seconds is None:
            resolved_seconds = DEFAULT_COOLDOWN_MINUTES * 60
        resolved_seconds = max(int(resolved_seconds), 0)

        if cooldown_minutes is not None:
            resolved_minutes = max(int(cooldown_minutes), 0)
        elif cooldown_seconds is not None:
            resolved_minutes = 0 if resolved_seconds == 0 else max(1, math.ceil(resolved_seconds / 60))
        elif existing_minutes is not None:
            resolved_minutes = max(int(existing_minutes), 0)
        else:
            resolved_minutes = 0 if resolved_seconds == 0 else max(1, math.ceil(resolved_seconds / 60))

        return resolved_mode, "seconds", resolved_minutes, resolved_seconds

    resolved_minutes = cooldown_minutes
    if resolved_minutes is None:
        resolved_minutes = existing_minutes
    if resolved_minutes is None:
        resolved_minutes = DEFAULT_COOLDOWN_MINUTES
    resolved_minutes = max(int(resolved_minutes), 0)

    resolved_seconds = cooldown_seconds
    if resolved_seconds is None or resolved_seconds != resolved_minutes * 60:
        resolved_seconds = resolved_minutes * 60
    resolved_seconds = max(int(resolved_seconds), 0)

    return resolved_mode, "minutes", resolved_minutes, resolved_seconds
