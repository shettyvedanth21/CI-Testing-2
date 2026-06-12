from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.shared.tenant_context import TenantContext


@dataclass(frozen=True)
class BoundJobPayload:
    job_type: str
    tenant_id: Optional[str]
    device_id: Optional[str]
    initiated_by_user_id: str
    initiated_by_role: str
    payload: dict

    def validate(self) -> None:
        if not self.job_type:
            raise ValueError("job_type is required")
        if not self.initiated_by_user_id:
            raise ValueError("initiated_by_user_id is required")
        if self.initiated_by_role != "super_admin" and self.tenant_id is None:
            raise ValueError("tenant_id is required for non-super_admin jobs")

    def to_tenant_context(self) -> TenantContext:
        return TenantContext(
            tenant_id=self.tenant_id,
            user_id=self.initiated_by_user_id,
            role=self.initiated_by_role,
            plant_ids=[],
            is_super_admin=self.initiated_by_role == "super_admin",
        )
