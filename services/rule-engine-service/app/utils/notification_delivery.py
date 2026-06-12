"""Helpers for notification delivery audit storage."""

from __future__ import annotations

import hashlib


def hash_recipient(recipient: str) -> str:
    """Produce a deterministic reconciliation hash for a recipient value."""
    return hashlib.sha256(recipient.strip().lower().encode("utf-8")).hexdigest()


def mask_recipient(channel: str, recipient: str) -> str:
    """Mask recipient values for admin-safe display and export."""
    normalized = recipient.strip()
    if channel == "email":
        local_part, sep, domain = normalized.partition("@")
        if not sep:
            return _mask_phone_like(normalized)
        if len(local_part) <= 2:
            masked_local = f"{local_part[:1]}***"
        else:
            masked_local = f"{local_part[:1]}***{local_part[-1:]}"
        return f"{masked_local}@{domain}"
    if channel in {"sms", "whatsapp"}:
        if normalized.startswith("whatsapp:"):
            phone = normalized.split(":", 1)[1]
            return f"whatsapp:{_mask_phone_like(phone)}"
        return _mask_phone_like(normalized)
    return "***"


def _mask_phone_like(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits or value)
    return f"{'*' * max(len(digits) - 4, 2)}{digits[-4:]}"
