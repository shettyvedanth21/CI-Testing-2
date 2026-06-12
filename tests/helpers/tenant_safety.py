from __future__ import annotations

from fastapi import Request

from services.shared.tenant_context import TenantContext


def make_tenant_context(
    tenant_id: str | None,
    *,
    user_id: str = "svc:test",
    role: str = "internal_service",
    is_super_admin: bool = False,
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        plant_ids=[],
        is_super_admin=is_super_admin,
    )


def make_system_context(service_name: str = "svc:test") -> TenantContext:
    return TenantContext.system(service_name)


def make_request(
    *,
    query_string: str = "",
    headers: dict[str, str] | None = None,
    state: dict[str, object] | None = None,
) -> Request:
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "query_string": query_string.encode("latin-1"),
    }
    request = Request(scope)
    for key, value in (state or {}).items():
        setattr(request.state, key, value)
    return request
