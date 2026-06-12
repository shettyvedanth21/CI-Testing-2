"""Internal-service auth helpers mirrored for the standalone simulator."""

from __future__ import annotations

import hashlib
import hmac
import os
import time


def _load_internal_service_secret() -> str:
    secret = os.getenv("INTERNAL_SERVICE_SHARED_SECRET", "").strip()
    if not secret:
        raise RuntimeError("INTERNAL_SERVICE_SHARED_SECRET is required for simulator internal-service requests")
    return secret


def sign_internal_service_request(
    service_name: str,
    tenant_id: str | None,
    *,
    timestamp: int | None = None,
    secret: str | None = None,
) -> tuple[int, str]:
    resolved_timestamp = int(time.time()) if timestamp is None else int(timestamp)
    resolved_secret = (secret if secret is not None else _load_internal_service_secret()).encode("utf-8")
    payload = f"{service_name.strip()}:{(tenant_id or '').strip()}:{resolved_timestamp}".encode("utf-8")
    signature = hmac.new(resolved_secret, payload, hashlib.sha256).hexdigest()
    return resolved_timestamp, signature


def build_internal_service_headers(service_name: str, tenant_id: str | None) -> dict[str, str]:
    timestamp, signature = sign_internal_service_request(service_name, tenant_id)
    headers = {
        "X-Internal-Service": service_name,
        "X-Internal-Service-Timestamp": str(timestamp),
        "X-Internal-Service-Signature": signature,
    }
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    return headers
