from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.params import Param

from .feature_entitlements import FeatureEntitlementState

INTERNAL_SERVICE_HEADER = "X-Internal-Service"
INTERNAL_SERVICE_SIGNATURE_HEADER = "X-Internal-Service-Signature"
INTERNAL_SERVICE_TIMESTAMP_HEADER = "X-Internal-Service-Timestamp"
TENANT_HEADER = "X-Tenant-Id"
TARGET_TENANT_HEADER = "X-Target-Tenant-Id"
_DEFAULT_INTERNAL_SERVICE_MAX_SKEW_SECONDS = 300


@dataclass(frozen=True)
class TenantContext:
    tenant_id: Optional[str]
    user_id: str
    role: str
    plant_ids: list[str]
    is_super_admin: bool
    entitlements: FeatureEntitlementState | None = None

    def require_tenant(self) -> str:
        if self.tenant_id is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "TENANT_SCOPE_REQUIRED",
                    "message": "Tenant scope is required for this action.",
                },
            )
        return self.tenant_id

    def has_feature(self, feature_key: str) -> bool:
        if self.entitlements is None:
            return False
        return feature_key in self.entitlements.available_features

    @classmethod
    def system(cls, service_name: str) -> TenantContext:
        return cls(
            tenant_id=None,
            user_id=service_name,
            role="super_admin",
            plant_ids=[],
            is_super_admin=True,
        )

    @classmethod
    def from_request(cls, request: Request) -> TenantContext:
        ctx = getattr(request.state, "tenant_context", None)
        if ctx is None:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "MISSING_AUTH_CONTEXT",
                    "message": "Authentication context is missing.",
                },
            )
        return ctx


def normalize_tenant_id(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Param):
        return normalize_tenant_id(value.default)
    resolved = str(value).strip()
    return resolved or None


def _coalesce_tenant_candidates(
    *candidates: tuple[str, object | None],
    error_code: str = "TENANT_SCOPE_MISMATCH",
    error_message: str = "Conflicting tenant scope provided.",
) -> str | None:
    resolved_values: list[tuple[str, str]] = []
    for label, value in candidates:
        normalized = normalize_tenant_id(value)
        if normalized is not None:
            resolved_values.append((label, normalized))

    if not resolved_values:
        return None

    distinct_values = {value for _, value in resolved_values}
    if len(distinct_values) > 1:
        raise HTTPException(
            status_code=403,
            detail={
                "code": error_code,
                "message": error_message,
                "sources": [label for label, _ in resolved_values],
            },
        )

    return resolved_values[0][1]


def resolve_request_tenant_id(
    request: Request,
    *,
    explicit_tenant_id: object | None = None,
    required: bool = False,
    allow_superadmin_query_fallback: bool = True,
    allow_query_fallback: bool = True,
) -> str | None:
    headers = getattr(request, "headers", {}) or {}
    query_params = getattr(request, "query_params", {}) or {}

    ctx = getattr(request.state, "tenant_context", None)
    state_tenant_id = None if ctx is None else ctx.tenant_id

    role = getattr(request.state, "role", "anonymous")
    query_fallback_enabled = allow_query_fallback and role != "internal_service"
    if role == "super_admin" and not allow_superadmin_query_fallback:
        query_fallback_enabled = False

    requested_candidates: list[tuple[str, object | None]] = [
        ("explicit_tenant_id", explicit_tenant_id),
        (f"header:{TENANT_HEADER}", headers.get(TENANT_HEADER)),
        (f"header:{TARGET_TENANT_HEADER}", headers.get(TARGET_TENANT_HEADER)),
    ]
    if query_fallback_enabled:
        requested_candidates.extend(
            [
                ("query:tenant_id", query_params.get("tenant_id")),
            ]
        )

    requested_tenant_id = _coalesce_tenant_candidates(
        *requested_candidates,
        error_message="Conflicting tenant scope provided.",
    )

    if state_tenant_id is not None:
        if requested_tenant_id is not None and requested_tenant_id != state_tenant_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "TENANT_SCOPE_MISMATCH",
                    "message": "Requested tenant scope does not match the authenticated tenant.",
                },
            )
        return state_tenant_id

    if requested_tenant_id is None and required:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "TENANT_SCOPE_REQUIRED",
                "message": "Tenant scope is required for this action.",
            },
        )

    return requested_tenant_id


def require_tenant(request: Request) -> str:
    return resolve_request_tenant_id(request, required=True)  # type: ignore[return-value]


def _load_internal_service_secret() -> str:
    secret = os.environ.get("INTERNAL_SERVICE_SHARED_SECRET")
    if not secret:
        raise RuntimeError("Internal service authentication secret is not configured")
    return secret


def _internal_service_signature_payload(service_name: str, tenant_id: str | None, timestamp: int) -> bytes:
    normalized_service_name = str(service_name or "").strip()
    normalized_tenant_id = normalize_tenant_id(tenant_id) or ""
    return f"{normalized_service_name}:{normalized_tenant_id}:{int(timestamp)}".encode("utf-8")


def _sign_internal_service_request(service_name: str, tenant_id: str | None, timestamp: int) -> str:
    secret = _load_internal_service_secret().encode("utf-8")
    payload = _internal_service_signature_payload(service_name, tenant_id, timestamp)
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_internal_service_headers(request: Request, service_name: str) -> str | None:
    timestamp_raw = str(request.headers.get(INTERNAL_SERVICE_TIMESTAMP_HEADER) or "").strip()
    signature = str(request.headers.get(INTERNAL_SERVICE_SIGNATURE_HEADER) or "").strip()
    if not timestamp_raw or not signature:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_INTERNAL_SERVICE_AUTH",
                "message": "Internal service proof is required.",
            },
        )

    try:
        timestamp = int(timestamp_raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_INTERNAL_SERVICE_AUTH",
                "message": "Internal service proof timestamp is invalid.",
            },
        ) from exc

    max_skew_seconds = int(
        os.environ.get(
            "INTERNAL_SERVICE_AUTH_MAX_SKEW_SECONDS",
            _DEFAULT_INTERNAL_SERVICE_MAX_SKEW_SECONDS,
        )
    )
    if abs(int(time.time()) - timestamp) > max(1, max_skew_seconds):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_INTERNAL_SERVICE_AUTH",
                "message": "Internal service proof has expired.",
            },
        )

    tenant_id = resolve_request_tenant_id(
        request,
        allow_superadmin_query_fallback=False,
        allow_query_fallback=False,
    )
    expected_signature = _sign_internal_service_request(service_name, tenant_id, timestamp)
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_INTERNAL_SERVICE_AUTH",
                "message": "Internal service proof is invalid.",
            },
        )

    return tenant_id


def build_internal_headers(service_name: str, tenant_id: str | None = None) -> dict[str, str]:
    timestamp = int(time.time())
    headers = {
        INTERNAL_SERVICE_HEADER: service_name,
        INTERNAL_SERVICE_TIMESTAMP_HEADER: str(timestamp),
        INTERNAL_SERVICE_SIGNATURE_HEADER: _sign_internal_service_request(service_name, tenant_id, timestamp),
    }
    if tenant_id:
        headers[TENANT_HEADER] = tenant_id
    return headers


def build_tenant_scoped_internal_headers(service_name: str, tenant_id: str) -> dict[str, str]:
    normalized_tenant_id = normalize_tenant_id(tenant_id)
    if normalized_tenant_id is None:
        raise ValueError("Tenant scope is required for tenant-owned internal requests.")
    return build_internal_headers(service_name, normalized_tenant_id)
