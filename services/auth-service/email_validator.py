from __future__ import annotations

import re
from dataclasses import dataclass


class EmailNotValidError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedEmail:
    normalized: str
    local_part: str
    domain: str
    ascii_email: str


def validate_email(email: str, *_, **__) -> ValidatedEmail:
    value = email.strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        raise EmailNotValidError(f"invalid email address: {email!r}")
    local_part, domain = value.split("@", 1)
    normalized = value.lower()
    return ValidatedEmail(
        normalized=normalized,
        local_part=local_part,
        domain=domain,
        ascii_email=normalized,
    )
