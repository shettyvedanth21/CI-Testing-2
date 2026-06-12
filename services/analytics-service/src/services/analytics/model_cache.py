"""Filesystem-backed model artifact cache for warm reuse."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional


class ModelCache:
    """Lightweight local artifact cache (additive, non-breaking)."""

    def __init__(self, root: str = "/tmp/analytics-model-cache"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(name: str) -> str:
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]

    def _path(self, device_id: str, analysis_type: str, model_key: str, schema_hash: str) -> Path:
        safe_dev = self._safe(device_id)
        safe_schema = schema_hash[:24]
        return self.root / f"{safe_dev}_{analysis_type}_{model_key}_{safe_schema}.bin"

    def load(self, device_id: str, analysis_type: str, model_key: str, schema_hash: str) -> Optional[bytes]:
        p = self._path(device_id, analysis_type, model_key, schema_hash)
        if not p.exists():
            return None
        try:
            return p.read_bytes()
        except Exception:
            return None

    def save(self, device_id: str, analysis_type: str, model_key: str, schema_hash: str, payload: bytes) -> None:
        if not payload:
            return
        p = self._path(device_id, analysis_type, model_key, schema_hash)
        p.write_bytes(payload)
