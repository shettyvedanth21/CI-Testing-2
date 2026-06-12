import httpx

from src.config import settings
from services.shared.tariff_client import fetch_tenant_tariff


async def get_current_tariff(tenant_id: str) -> tuple[float, str]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            payload = await fetch_tenant_tariff(
                client,
                settings.reporting_service_url,
                tenant_id,
                service_name="copilot-service",
            )
            rate = float(payload.get("rate") or 0.0)
            currency = payload.get("currency") or "INR"
            return rate, currency
    except Exception:
        return 0.0, "INR"
