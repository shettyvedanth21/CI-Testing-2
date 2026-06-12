from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

REPORTING_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "reporting-service"
if str(REPORTING_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(REPORTING_SERVICE_ROOT))

from src.repositories.tariff_repository import TariffRepository
from src.services.tariff_resolver import resolve_tariff


def test_resolve_tariff_uses_tenant_scoped_tariff(monkeypatch):
    captured = {}

    async def fake_get_effective_version(self, *args, **kwargs):  # noqa: ANN001
        captured["effective_version_tenant_id"] = getattr(self, "_tenant_id", None)
        return None

    async def fake_get_tariff(self, *args, **kwargs):  # noqa: ANN001
        captured["tenant_id"] = getattr(self, "_tenant_id", None)
        return SimpleNamespace(
            energy_rate_per_kwh=7.25,
            currency="inr",
            updated_at=datetime(2026, 3, 31, 12, 0, 0),
        )

    monkeypatch.setattr(TariffRepository, "get_effective_version", fake_get_effective_version)
    monkeypatch.setattr(TariffRepository, "get_tariff", fake_get_tariff)

    tariff = asyncio.run(resolve_tariff(object(), "TENANT-123"))

    assert captured["tenant_id"] == "TENANT-123"
    assert tariff.rate == 7.25
    assert tariff.currency == "INR"
    assert tariff.source == "tenant_tariffs"


def test_resolve_tariff_returns_unconfigured_when_tenant_tariff_missing(monkeypatch):
    async def fake_get_effective_version(self, *args, **kwargs):  # noqa: ANN001
        return None

    async def fake_get_tariff(self, *args, **kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(TariffRepository, "get_effective_version", fake_get_effective_version)
    monkeypatch.setattr(TariffRepository, "get_tariff", fake_get_tariff)

    tariff = asyncio.run(resolve_tariff(object(), "TENANT-404"))

    assert tariff.rate is None
    assert tariff.currency == "INR"
    assert tariff.source == "default_unconfigured"
