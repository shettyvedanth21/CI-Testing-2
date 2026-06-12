from __future__ import annotations

from fastapi import HTTPException
from starlette.requests import Request
import pytest

from services.shared.tenant_context import resolve_request_tenant_id
from tests.helpers.tenant_safety import make_tenant_context


def _request(
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


def test_resolve_request_tenant_id_accepts_tenant_id_query():
    request = _request(query_string="tenant_id=tenant-a")

    assert resolve_request_tenant_id(request) == "tenant-a"


def test_resolve_request_tenant_id_rejects_conflicting_duplicate_query_values():
    request = _request(
        query_string="tenant_id=tenant-b",
        headers={"X-Tenant-Id": "tenant-a"},
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_request_tenant_id(request)

    detail = exc_info.value.detail
    assert detail["code"] == "TENANT_SCOPE_MISMATCH"


def test_resolve_request_tenant_id_rejects_conflict_with_authenticated_tenant():
    request = _request(
        query_string="tenant_id=tenant-b",
        state={
            "tenant_id": "tenant-a",
            "role": "org_admin",
            "tenant_context": make_tenant_context("tenant-a", role="org_admin"),
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_request_tenant_id(request)

    detail = exc_info.value.detail
    assert detail["code"] == "TENANT_SCOPE_MISMATCH"


def test_resolve_request_tenant_id_accepts_matching_internal_header_scope():
    request = _request(
        headers={"X-Tenant-Id": "tenant-a"},
        state={
            "role": "internal_service",
            "tenant_context": make_tenant_context("tenant-a"),
        },
    )

    assert resolve_request_tenant_id(request) == "tenant-a"
