from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace


COPILOT_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "copilot-service"


def _load_service_clients_module():
    module_path = COPILOT_SERVICE_ROOT / "src" / "integrations" / "service_clients.py"
    spec = importlib.util.spec_from_file_location("copilot_service_clients", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load copilot service_clients module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service_clients = _load_service_clients_module()


def test_get_current_tariff_is_tenant_scoped(monkeypatch):
    captured = {}

    async def fake_fetch_tenant_tariff(client, base_url, tenant_id, *, service_name):  # noqa: ANN001
        captured["tenant_id"] = tenant_id
        captured["service_name"] = service_name
        return {"rate": 7.5, "currency": "USD", "configured": True, "source": "tenant_tariffs"}

    monkeypatch.setattr(service_clients, "fetch_tenant_tariff", fake_fetch_tenant_tariff)
    monkeypatch.setattr(
        service_clients,
        "settings",
        SimpleNamespace(reporting_service_url="http://reporting-service"),
    )

    rate, currency = asyncio.run(service_clients.get_current_tariff("ORG-COPILOT"))

    assert rate == 7.5
    assert currency == "USD"
    assert captured == {
        "tenant_id": "ORG-COPILOT",
        "service_name": "copilot-service",
    }
