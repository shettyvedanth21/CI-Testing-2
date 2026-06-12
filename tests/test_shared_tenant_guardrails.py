from __future__ import annotations

from dataclasses import dataclass
import os

import pytest
from fastapi import HTTPException

from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import (
    TenantContext,
    build_tenant_scoped_internal_headers,
    INTERNAL_SERVICE_SIGNATURE_HEADER,
    INTERNAL_SERVICE_TIMESTAMP_HEADER,
    resolve_request_tenant_id,
)
from tests.helpers.tenant_safety import make_request, make_system_context, make_tenant_context


@dataclass
class _TenantOwnedModel:
    tenant_id: str | None = None
    id: str | None = None


class _TenantOwnedRepository(TenantScopedRepository[_TenantOwnedModel]):
    model = _TenantOwnedModel


def test_system_context_is_explicit_and_unscoped() -> None:
    ctx = TenantContext.system("svc:reporting-service")

    assert ctx.tenant_id is None
    assert ctx.user_id == "svc:reporting-service"
    assert ctx.role == "super_admin"
    assert ctx.is_super_admin is True


def test_build_tenant_scoped_internal_headers_requires_tenant_scope() -> None:
    os.environ.setdefault("JWT_SECRET_KEY", "tenant-guardrail-test-secret")
    headers = build_tenant_scoped_internal_headers("reporting-service", "ORG-A")

    assert headers["X-Internal-Service"] == "reporting-service"
    assert headers["X-Tenant-Id"] == "ORG-A"
    assert INTERNAL_SERVICE_SIGNATURE_HEADER in headers
    assert INTERNAL_SERVICE_TIMESTAMP_HEADER in headers

    with pytest.raises(ValueError, match="Tenant scope is required"):
        build_tenant_scoped_internal_headers("reporting-service", "")


def test_tenant_owned_repository_rejects_cross_tenant_opt_out_without_system_context() -> None:
    ctx = make_tenant_context("ORG-A", role="plant_manager")

    with pytest.raises(ValueError, match="explicit system context"):
        _TenantOwnedRepository(session=object(), ctx=ctx, allow_cross_tenant=True)


def test_tenant_owned_repository_allows_explicit_system_context_for_global_jobs() -> None:
    repo = _TenantOwnedRepository(
        session=object(),
        ctx=make_system_context("svc:global-job"),
        allow_cross_tenant=True,
    )

    assert repo._tenant_id is None


def test_internal_tenant_scope_mismatch_fails_closed_in_shared_resolution() -> None:
    request = make_request(
        headers={"X-Tenant-Id": "tenant-b"},
        state={
            "tenant_context": make_tenant_context("tenant-a"),
            "role": "internal_service",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_request_tenant_id(request)

    assert exc_info.value.detail["code"] == "TENANT_SCOPE_MISMATCH"
