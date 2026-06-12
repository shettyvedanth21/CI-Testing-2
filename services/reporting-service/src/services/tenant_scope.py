from __future__ import annotations

from services.shared.tenant_context import TenantContext, normalize_tenant_id

SERVICE_USER_ID = "svc:reporting-service"
SERVICE_ROLE = "system"

def build_service_tenant_context(tenant_id: str | None) -> TenantContext:
    resolved = normalize_tenant_id(tenant_id)
    if resolved is None:
        raise ValueError("Tenant scope is required")

    return TenantContext(
        tenant_id=resolved,
        user_id=SERVICE_USER_ID,
        role=SERVICE_ROLE,
        plant_ids=[],
        is_super_admin=False,
    )
