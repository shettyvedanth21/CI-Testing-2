from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "ensure_certification_orgs.py"
SPEC = importlib.util.spec_from_file_location("ensure_certification_orgs", MODULE_PATH)
assert SPEC and SPEC.loader
ensure_certification_orgs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ensure_certification_orgs
SPEC.loader.exec_module(ensure_certification_orgs)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self):
        self.gets = []
        self.posts = []
        self.puts = []

    def get(self, url, headers=None, params=None):
        self.gets.append((url, headers, params))
        return FakeResponse([])

    def post(self, url, headers=None, json=None):
        self.posts.append((url, headers, json))
        payload = {"id": "created-org"}
        if json:
            payload.update(json)
        return FakeResponse(payload)

    def put(self, url, headers=None, json=None):
        self.puts.append((url, headers, json))
        return FakeResponse({"success": True})

    def close(self):
        return None


def test_ensure_org_reuses_existing_slug():
    seeder = ensure_certification_orgs.CertificationSeeder()
    seeder.client.close()
    seeder.client = FakeClient()
    seeder.list_orgs = lambda token: [{"id": "SH00000001", "name": "Test-01", "slug": "test-01"}]

    org = seeder.ensure_org("token", name="Test-01", slug="test-01")

    assert org["id"] == "SH00000001"
    assert seeder.client.posts == []


def test_ensure_org_creates_missing_slug():
    seeder = ensure_certification_orgs.CertificationSeeder()
    seeder.client.close()
    seeder.client = FakeClient()
    seeder.list_orgs = lambda token: []

    org = seeder.ensure_org("token", name="Cert Org", slug="cert-org")

    assert org["slug"] == "cert-org"
    assert len(seeder.client.posts) == 1


def test_ensure_plant_manager_feature_matrix_merges_required_features():
    seeder = ensure_certification_orgs.CertificationSeeder()
    seeder.client.close()
    seeder.client = FakeClient()
    seeder.get_entitlements = lambda token, tenant_id: {
        "role_feature_matrix": {
            "plant_manager": ["analytics"],
            "operator": ["machines"],
            "viewer": [],
        }
    }

    seeder.ensure_plant_manager_feature_matrix("org-admin-token", "SH00000001")

    assert seeder.client.puts
    _, _, payload = seeder.client.puts[0]
    assert payload["role_feature_matrix"]["plant_manager"] == ["analytics", "reports", "waste_analysis"]
    assert payload["role_feature_matrix"]["operator"] == ["machines"]
    assert payload["role_feature_matrix"]["viewer"] == []


def test_list_admin_users_uses_canonical_tenant_id_query():
    seeder = ensure_certification_orgs.CertificationSeeder()
    seeder.client.close()
    seeder.client = FakeClient()

    seeder.list_admin_users("super-admin-token", "SH00000001")

    assert seeder.client.gets
    _, _, params = seeder.client.gets[0]
    assert params == {"tenant_id": "SH00000001"}


def test_ensure_org_admin_posts_canonical_tenant_id():
    seeder = ensure_certification_orgs.CertificationSeeder()
    seeder.client.close()
    seeder.client = FakeClient()
    seeder.list_admin_users = lambda token, tenant_id: []
    seeder.login = lambda email, password: "access-token"

    seeder.ensure_org_admin("super-admin-token", {"id": "SH00000001", "name": "Tenant One", "slug": "tenant-one"})

    assert seeder.client.posts
    _, _, payload = seeder.client.posts[0]
    assert payload["tenant_id"] == "SH00000001"
    assert "role" in payload


def test_scope_matrix_limits_plant_manager_to_first_two_plants():
    seeder = ensure_certification_orgs.CertificationSeeder()
    seeder.client.close()
    seeder.client = FakeClient()
    seeder.ensure_org = lambda *args, **kwargs: {"id": "SH00000001", "name": "Test-01", "slug": "test-01"}
    seeder.ensure_premium_features = lambda *args, **kwargs: None
    seeder.ensure_org_admin = lambda *args, **kwargs: ensure_certification_orgs.SeededUser(
        email="admin@test.local",
        password="pw",
        role="org_admin",
    )
    seeder.login = lambda *args, **kwargs: "org-admin-token"
    seeded_plants = iter(
        [
            {"id": "PLANT-A", "name": "A"},
            {"id": "PLANT-B", "name": "B"},
            {"id": "PLANT-C", "name": "C"},
        ]
    )
    seeder.ensure_named_plant = lambda *args, **kwargs: next(seeded_plants)
    seeder.ensure_plant_manager_feature_matrix = lambda *args, **kwargs: None
    captured: dict[str, list[str]] = {}

    def fake_ensure_plant_manager(_token, _org, plant_ids):
        captured["plant_ids"] = list(plant_ids)
        return ensure_certification_orgs.SeededUser(
            email="pm@test.local",
            password="pw",
            role="plant_manager",
        )

    seeder.ensure_plant_manager = fake_ensure_plant_manager
    seeder.ensure_scoped_user = lambda *args, **kwargs: ensure_certification_orgs.SeededUser(
        email=f"{kwargs['role']}@test.local",
        password="pw",
        role=kwargs["role"],
    )
    seeder.ensure_seeded_device = lambda _token, _tenant_id, plant, **kwargs: ensure_certification_orgs.SeededDevice(
        device_id=kwargs.get("device_id") or "DEVICE",
        device_name=kwargs["device_name"],
        plant_id=str(plant["id"]),
        plant_name=str(plant["name"]),
        metadata_key=str(kwargs["metadata_key"]),
    )

    org = seeder.ensure_org_bundle("super-admin-token", name="Test-01", slug="test-01", include_scope_matrix=True)

    assert captured["plant_ids"] == ["PLANT-A", "PLANT-B"]
    assert [device.plant_id for device in org.smoke_devices] == ["PLANT-A", "PLANT-B", "PLANT-C"]
