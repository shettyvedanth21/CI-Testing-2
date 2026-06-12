from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from services.shared.tenant_context import build_internal_headers


_TARIFF_RESOLVE_PATHS = (
    "/api/reports/tariffs/internal/resolve",
    "/api/v1/tariffs/internal/resolve",
)


async def fetch_tenant_tariff(
    client: httpx.AsyncClient,
    reporting_base_url: str,
    tenant_id: Optional[str],
    *,
    service_name: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    base = (reporting_base_url or "").rstrip("/")
    if not base:
        return {"rate": 0.0, "currency": "INR", "configured": False, "source": "missing_base_url", "version_id": None}

    if not tenant_id:
        return {
            "rate": 0.0,
            "currency": "INR",
            "configured": False,
            "source": "tenant_scope_required",
            "version_id": None,
        }

    return await resolve_tenant_tariff(
        client,
        reporting_base_url,
        tenant_id,
        service_name=service_name,
        effective_at=None,
        timeout=timeout,
    )


async def resolve_tenant_tariff(
    client: httpx.AsyncClient,
    reporting_base_url: str,
    tenant_id: Optional[str],
    *,
    service_name: str,
    effective_at: datetime | None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    base = (reporting_base_url or "").rstrip("/")
    if not base:
        return {"rate": 0.0, "currency": "INR", "configured": False, "source": "missing_base_url", "version_id": None}

    if not tenant_id:
        return {
            "rate": 0.0,
            "currency": "INR",
            "configured": False,
            "source": "tenant_scope_required",
            "version_id": None,
        }

    params = {"effective_at": effective_at.isoformat()} if effective_at is not None else None
    headers = build_internal_headers(service_name, tenant_id)

    response: httpx.Response | None = None
    for index, path in enumerate(_TARIFF_RESOLVE_PATHS):
        response = await client.get(
            f"{base}{path}",
            params=params,
            headers=headers,
            timeout=timeout,
        )
        if response.status_code != 404 or index == len(_TARIFF_RESOLVE_PATHS) - 1:
            break

    assert response is not None
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    rate = data.get("rate")
    configured = rate is not None
    return {
        "rate": float(rate) if configured else 0.0,
        "currency": str(data.get("currency") or "INR"),
        "configured": configured,
        "source": str(data.get("source") or ("tenant_tariffs" if configured else "default_unconfigured")),
        "version_id": data.get("version_id"),
        "effective_start_at": data.get("effective_start_at"),
        "effective_end_at": data.get("effective_end_at"),
    }
