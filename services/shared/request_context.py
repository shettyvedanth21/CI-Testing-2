from __future__ import annotations

from contextvars import ContextVar
from typing import Optional


current_http_path: ContextVar[Optional[str]] = ContextVar("current_http_path", default=None)

