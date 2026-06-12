"""Recipient normalization helpers for notification channels."""

from __future__ import annotations

import re

_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")
_INDIA_COUNTRY_CODE = "+91"
_INDIA_COUNTRY_DIGITS = "91"
_INDIA_LOCAL_NUMBER_LENGTH = 10


def normalize_phone_recipient(value: str) -> str:
    """Normalize a phone recipient into E.164-like format."""
    cleaned = "".join(ch for ch in value.strip() if ch.isdigit() or ch == "+")
    if not cleaned:
        raise ValueError("phone recipient must be an E.164-compatible phone number")
    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"

    if cleaned.startswith("+"):
        digits = "".join(ch for ch in cleaned[1:] if ch.isdigit())
        cleaned = f"+{digits}" if digits else ""
    else:
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        if len(digits) == _INDIA_LOCAL_NUMBER_LENGTH:
            cleaned = f"{_INDIA_COUNTRY_CODE}{digits}"
        elif len(digits) == _INDIA_LOCAL_NUMBER_LENGTH + len(_INDIA_COUNTRY_DIGITS) and digits.startswith(_INDIA_COUNTRY_DIGITS):
            cleaned = f"+{digits}"
        else:
            cleaned = f"+{digits}"

    if not cleaned:
        raise ValueError("phone recipient must be an E.164-compatible phone number")

    digits = cleaned[1:]
    if not digits.isdigit() or len(digits) < 8 or len(digits) > 15:
        raise ValueError("phone recipient must be an E.164-compatible phone number")
    if not _PHONE_RE.match(cleaned):
        raise ValueError("phone recipient must be an E.164-compatible phone number")
    return cleaned
