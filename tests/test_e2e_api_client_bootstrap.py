from tests.helpers.api_client import APIClient


def test_api_client_enables_all_required_premium_features(monkeypatch):
    ensured: list[tuple[str, str, set[str]]] = []

    monkeypatch.setattr(
        "tests.helpers.api_client._login_super_admin",
        lambda *args, **kwargs: "token-123",
    )
    monkeypatch.setattr(
        "tests.helpers.api_client._discover_default_tenant",
        lambda required_features=None: "tenant-abc",
    )
    monkeypatch.setattr(
        "tests.helpers.api_client._ensure_org_premium_features",
        lambda token, tenant_id, required_features: ensured.append((token, tenant_id, set(required_features))),
    )

    client = APIClient(
        {
            "device": "http://localhost:8000",
            "data": "http://localhost:8081",
            "rules": "http://localhost:8002",
            "analytics": "http://localhost:8003",
            "reporting": "http://localhost:8085",
            "waste": "http://localhost:8087",
            "copilot": "http://localhost:8007",
        }
    )

    assert client.analytics.c.headers["X-Target-Tenant-Id"] == "tenant-abc"
    assert ensured == [
        (
            "token-123",
            "tenant-abc",
            {"analytics", "reports", "waste_analysis", "copilot"},
        )
    ]
