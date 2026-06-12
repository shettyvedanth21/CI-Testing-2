from __future__ import annotations

from typing import Any

import httpx

from services.shared.tenant_context import build_internal_headers


async def internal_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    service_name: str,
    tenant_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    response = await client.get(
        url,
        params=params,
        headers=build_internal_headers(service_name, tenant_id),
    )
    if response.status_code >= 500:
        response.raise_for_status()
    return response
