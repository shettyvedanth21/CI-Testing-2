#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx


AUTH_URL = os.environ.get("CERTIFY_STACK_AUTH_URL", os.environ.get("AUTH_URL", "http://localhost:8090")).rstrip("/")
DEVICE_URL = os.environ.get("DEVICE_URL", "http://localhost:8000").rstrip("/")
SUPER_ADMIN_EMAIL = os.environ.get(
    "CERTIFY_STACK_EMAIL",
    os.environ.get("BOOTSTRAP_SUPER_ADMIN_EMAIL", "manash.ray@cittagent.com"),
)
SUPER_ADMIN_PASSWORD = os.environ.get(
    "CERTIFY_STACK_PASSWORD",
    os.environ.get("BOOTSTRAP_SUPER_ADMIN_PASSWORD", "Shivex@2706!"),
)
SEED_PASSWORD = os.environ.get("CERTIFY_SEED_PASSWORD", "Validate123!")
HTTP_TIMEOUT = float(os.environ.get("CERTIFY_HTTP_TIMEOUT", "30"))

PRIMARY_ORG_NAME = os.environ.get("CERTIFY_PRIMARY_ORG_NAME", "Test-01")
PRIMARY_ORG_SLUG = os.environ.get("CERTIFY_PRIMARY_ORG_SLUG", "test-01")
SECONDARY_ORG_NAME = os.environ.get("CERTIFY_SECONDARY_ORG_NAME", "Certification Validation Secondary")
SECONDARY_ORG_SLUG = os.environ.get("CERTIFY_SECONDARY_ORG_SLUG", "certification-validation-secondary")
REQUIRED_PREMIUM_FEATURES = ["analytics", "reports", "waste_analysis"]
PLANT_MANAGER_FEATURES = ["analytics", "reports", "waste_analysis"]


@dataclass
class SeededUser:
    email: str
    password: str
    role: str


@dataclass
class SeededDevice:
    device_id: str
    device_name: str
    plant_id: str
    plant_name: str
    metadata_key: str


@dataclass
class SeededOrg:
    id: str
    name: str
    slug: str
    plants: list[dict[str, str]]
    org_admin: SeededUser
    plant_manager: SeededUser
    devices: list[SeededDevice] = field(default_factory=list)
    operator: SeededUser | None = None
    viewer: SeededUser | None = None
    smoke_devices: list[SeededDevice] = field(default_factory=list)


class SeederError(RuntimeError):
    pass


def _parse_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class CertificationSeeder:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=HTTP_TIMEOUT)

    def close(self) -> None:
        self.client.close()

    def login(self, email: str, password: str) -> str:
        response = self.client.post(
            f"{AUTH_URL}/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise SeederError(f"Login returned no access token for {email}")
        return str(token)

    def _auth_headers(self, token: str, tenant_id: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}"}
        if tenant_id:
            headers["X-Target-Tenant-Id"] = tenant_id
        return headers

    def list_orgs(self, super_admin_token: str) -> list[dict[str, Any]]:
        response = self.client.get(
            f"{AUTH_URL}/api/admin/tenants",
            headers=self._auth_headers(super_admin_token),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise SeederError("Expected org list response to be an array")
        return payload

    def ensure_org(self, super_admin_token: str, *, name: str, slug: str) -> dict[str, Any]:
        for org in self.list_orgs(super_admin_token):
            if org.get("slug") == slug:
                return org

        created = self.client.post(
            f"{AUTH_URL}/api/admin/tenants",
            headers=self._auth_headers(super_admin_token),
            json={"name": name, "slug": slug},
        )
        created.raise_for_status()
        return created.json()

    def list_plants(self, token: str, tenant_id: str) -> list[dict[str, Any]]:
        response = self.client.get(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/plants",
            headers=self._auth_headers(token),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise SeederError("Expected plant list response to be an array")
        return payload

    def ensure_named_plant(self, token: str, tenant_id: str, *, name: str) -> dict[str, Any]:
        for plant in self.list_plants(token, tenant_id):
            if plant.get("name") == name:
                return plant

        created = self.client.post(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/plants",
            headers=self._auth_headers(token),
            json={
                "name": name,
                "location": "Certification Validation",
                "timezone": "Asia/Kolkata",
            },
        )
        created.raise_for_status()
        return created.json()

    def get_entitlements(self, token: str, tenant_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/entitlements",
            headers=self._auth_headers(token),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise SeederError("Expected entitlements response to be an object")
        return payload

    def ensure_premium_features(self, super_admin_token: str, tenant_id: str) -> None:
        current = self.get_entitlements(super_admin_token, tenant_id)
        grants = {str(item) for item in current.get("premium_feature_grants") or []}
        merged = sorted(grants | set(REQUIRED_PREMIUM_FEATURES))
        if merged == sorted(grants):
            return

        response = self.client.put(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/entitlements",
            headers=self._auth_headers(super_admin_token),
            json={"premium_feature_grants": merged},
        )
        response.raise_for_status()

    def ensure_plant_manager_feature_matrix(self, org_admin_token: str, tenant_id: str) -> None:
        current = self.get_entitlements(org_admin_token, tenant_id)
        matrix = current.get("role_feature_matrix") or {}
        normalized = {
            "plant_manager": sorted(set(matrix.get("plant_manager") or []) | set(PLANT_MANAGER_FEATURES)),
            "operator": list(matrix.get("operator") or []),
            "viewer": list(matrix.get("viewer") or []),
        }
        if normalized == {
            "plant_manager": list(matrix.get("plant_manager") or []),
            "operator": list(matrix.get("operator") or []),
            "viewer": list(matrix.get("viewer") or []),
        }:
            return

        response = self.client.put(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/entitlements",
            headers=self._auth_headers(org_admin_token),
            json={"role_feature_matrix": normalized},
        )
        response.raise_for_status()

    def list_admin_users(self, super_admin_token: str, tenant_id: str) -> list[dict[str, Any]]:
        response = self.client.get(
            f"{AUTH_URL}/api/admin/users",
            headers=self._auth_headers(super_admin_token),
            params={"tenant_id": tenant_id},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise SeederError("Expected admin user list response to be an array")
        return payload

    def list_org_users(self, token: str, tenant_id: str) -> list[dict[str, Any]]:
        response = self.client.get(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/users",
            headers=self._auth_headers(token),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise SeederError("Expected org user list response to be an array")
        return payload

    def ensure_org_admin(self, super_admin_token: str, org: dict[str, Any]) -> SeededUser:
        email = f"certify+{org['slug']}-admin@factoryops.local"
        users = self.list_admin_users(super_admin_token, str(org["id"]))
        if not any(user.get("email") == email for user in users):
            created = self.client.post(
                f"{AUTH_URL}/api/admin/users",
                headers=self._auth_headers(super_admin_token),
                json={
                    "email": email,
                    "full_name": f"{org['name']} Certification Org Admin",
                    "role": "org_admin",
                    "tenant_id": str(org["id"]),
                    "password": SEED_PASSWORD,
                    "plant_ids": [],
                },
            )
            created.raise_for_status()

        self.login(email, SEED_PASSWORD)
        return SeededUser(email=email, password=SEED_PASSWORD, role="org_admin")

    def ensure_plant_manager(self, org_admin_token: str, org: dict[str, Any], plant_ids: list[str]) -> SeededUser:
        if not plant_ids:
            raise SeederError(f"Cannot create plant manager for org {org['slug']} without plants")

        email = f"certify+{org['slug']}-pm@factoryops.local"
        users = self.list_org_users(org_admin_token, str(org["id"]))
        if not any(user.get("email") == email for user in users):
            created = self.client.post(
                f"{AUTH_URL}/api/v1/tenants/{org['id']}/users",
                headers=self._auth_headers(org_admin_token),
                json={
                    "email": email,
                    "full_name": f"{org['name']} Certification Plant Manager",
                    "role": "plant_manager",
                    "tenant_id": str(org["id"]),
                    "plant_ids": plant_ids,
                    "password": SEED_PASSWORD,
                },
            )
            created.raise_for_status()

        self.login(email, SEED_PASSWORD)
        return SeededUser(email=email, password=SEED_PASSWORD, role="plant_manager")

    def ensure_scoped_user(
        self,
        org_admin_token: str,
        org: dict[str, Any],
        *,
        role: str,
        email_suffix: str,
        full_name: str,
        plant_ids: list[str],
    ) -> SeededUser:
        email = f"certify+{org['slug']}-{email_suffix}@factoryops.local"
        users = self.list_org_users(org_admin_token, str(org["id"]))
        if not any(user.get("email") == email for user in users):
            created = self.client.post(
                f"{AUTH_URL}/api/v1/tenants/{org['id']}/users",
                headers=self._auth_headers(org_admin_token),
                json={
                    "email": email,
                    "full_name": full_name,
                    "role": role,
                    "tenant_id": str(org["id"]),
                    "plant_ids": plant_ids,
                    "password": SEED_PASSWORD,
                },
            )
            created.raise_for_status()

        self.login(email, SEED_PASSWORD)
        return SeededUser(email=email, password=SEED_PASSWORD, role=role)

    def list_devices(self, token: str, tenant_id: str) -> list[dict[str, Any]]:
        page = 1
        page_size = 100
        devices: list[dict[str, Any]] = []

        while True:
            response = self.client.get(
                f"{DEVICE_URL}/api/v1/devices",
                headers=self._auth_headers(token, tenant_id),
                params={"page": page, "page_size": page_size},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise SeederError("Expected device list response to contain a data array")

            batch = payload["data"]
            devices.extend(batch)
            total_pages = int(payload.get("total_pages") or 1)
            if page >= total_pages or not batch:
                return devices
            page += 1

    def ensure_seeded_device(
        self,
        token: str,
        tenant_id: str,
        plant: dict[str, Any],
        *,
        device_name: str,
        metadata_key: str,
    ) -> SeededDevice:
        for device in self.list_devices(token, tenant_id):
            metadata = _parse_metadata(device.get("metadata_json"))
            if metadata.get("certification_seed_key") == metadata_key:
                return SeededDevice(
                    device_id=str(device["device_id"]),
                    device_name=str(device.get("device_name") or device_name),
                    plant_id=str(device.get("plant_id") or plant["id"]),
                    plant_name=str(plant["name"]),
                    metadata_key=metadata_key,
                )

        created = self.client.post(
            f"{DEVICE_URL}/api/v1/devices",
            headers=self._auth_headers(token, tenant_id),
            json={
                "device_name": device_name,
                "device_type": "compressor",
                "device_id_class": "active",
                "manufacturer": "Certification",
                "model": "Validation Rig",
                "location": f"{plant['name']} Validation Zone",
                "phase_type": "single",
                "data_source_type": "metered",
                "plant_id": str(plant["id"]),
                "metadata_json": json.dumps(
                    {
                        "certification_seed": True,
                        "certification_seed_key": metadata_key,
                        "org_slug": tenant_id,
                    }
                ),
            },
        )
        created.raise_for_status()
        payload = created.json()
        data = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
        return SeededDevice(
            device_id=str(data["device_id"]),
            device_name=str(data["device_name"]),
            plant_id=str(data["plant_id"]),
            plant_name=str(plant["name"]),
            metadata_key=metadata_key,
        )

    def ensure_org_bundle(
        self,
        super_admin_token: str,
        *,
        name: str,
        slug: str,
        include_scope_matrix: bool = False,
    ) -> SeededOrg:
        org = self.ensure_org(super_admin_token, name=name, slug=slug)
        tenant_id = str(org["id"])
        self.ensure_premium_features(super_admin_token, tenant_id)

        org_admin = self.ensure_org_admin(super_admin_token, org)
        org_admin_token = self.login(org_admin.email, org_admin.password)

        plant_a = self.ensure_named_plant(org_admin_token, tenant_id, name=f"{name} Certification Plant A")
        plant_b = self.ensure_named_plant(org_admin_token, tenant_id, name=f"{name} Certification Plant B")
        plants = [plant_a, plant_b]
        if include_scope_matrix:
            plant_c = self.ensure_named_plant(org_admin_token, tenant_id, name=f"{name} Certification Plant C")
            plants.append(plant_c)
        plant_ids = [str(plant["id"]) for plant in plants]

        self.ensure_plant_manager_feature_matrix(org_admin_token, tenant_id)
        org_admin_token = self.login(org_admin.email, org_admin.password)
        plant_manager_plant_ids = plant_ids[:2] if include_scope_matrix else plant_ids
        plant_manager = self.ensure_plant_manager(org_admin_token, org, plant_manager_plant_ids)
        operator = None
        viewer = None
        smoke_devices: list[SeededDevice] = []

        if include_scope_matrix:
            operator = self.ensure_scoped_user(
                org_admin_token,
                org,
                role="operator",
                email_suffix="operator",
                full_name=f"{name} Validation Operator",
                plant_ids=[str(plant_a["id"])],
            )
            viewer = self.ensure_scoped_user(
                org_admin_token,
                org,
                role="viewer",
                email_suffix="viewer",
                full_name=f"{name} Validation Viewer",
                plant_ids=[str(plant_a["id"])],
            )

        devices = [
            self.ensure_seeded_device(
                org_admin_token,
                tenant_id,
                plant_a,
                device_name="Certification Duplicate Machine",
                metadata_key=f"{slug}:duplicate:a",
            ),
            self.ensure_seeded_device(
                org_admin_token,
                tenant_id,
                plant_b,
                device_name="Certification Duplicate Machine",
                metadata_key=f"{slug}:duplicate:b",
            ),
        ]
        if include_scope_matrix:
            smoke_devices = [
                self.ensure_seeded_device(
                    org_admin_token,
                    tenant_id,
                    plant_a,
                    device_name="Smoke Device A",
                    metadata_key=f"{slug}:smoke:a",
                ),
                self.ensure_seeded_device(
                    org_admin_token,
                    tenant_id,
                    plant_b,
                    device_name="Smoke Device B",
                    metadata_key=f"{slug}:smoke:b",
                ),
                self.ensure_seeded_device(
                    org_admin_token,
                    tenant_id,
                    plants[2],
                    device_name="Smoke Device C",
                    metadata_key=f"{slug}:smoke:c",
                ),
            ]

        return SeededOrg(
            id=tenant_id,
            name=str(org["name"]),
            slug=str(org["slug"]),
            plants=[
                {"id": str(plant["id"]), "name": str(plant["name"])}
                for plant in plants
            ],
            org_admin=org_admin,
            plant_manager=plant_manager,
            devices=devices,
            operator=operator,
            viewer=viewer,
            smoke_devices=smoke_devices,
        )

    def run(self) -> dict[str, Any]:
        super_admin_token = self.login(SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
        primary = self.ensure_org_bundle(
            super_admin_token,
            name=PRIMARY_ORG_NAME,
            slug=PRIMARY_ORG_SLUG,
            include_scope_matrix=True,
        )
        secondary = self.ensure_org_bundle(
            super_admin_token,
            name=SECONDARY_ORG_NAME,
            slug=SECONDARY_ORG_SLUG,
        )
        return {
            "primary_org": asdict(primary),
            "secondary_org": asdict(secondary),
            "strict_env": {
                "VALIDATE_PRIMARY_TENANT_ID": primary.id,
                "VALIDATE_SECONDARY_TENANT_ID": secondary.id,
                "CERTIFY_TENANT_ID": primary.id,
                "CERTIFY_ORG_LABEL": primary.name,
                "CERTIFY_PM_EMAIL": primary.plant_manager.email,
                "CERTIFY_PM_PASSWORD": primary.plant_manager.password,
                "CERTIFY_EXPECTED_GENERATED_DEVICE_ID": primary.devices[0].device_id,
                "CERTIFY_OPERATOR_EMAIL": primary.operator.email if primary.operator else "",
                "CERTIFY_OPERATOR_PASSWORD": primary.operator.password if primary.operator else "",
                "CERTIFY_VIEWER_EMAIL": primary.viewer.email if primary.viewer else "",
                "CERTIFY_VIEWER_PASSWORD": primary.viewer.password if primary.viewer else "",
            },
            "smoke_context": {
                "super_admin_email": SUPER_ADMIN_EMAIL,
                "org_admin_email": primary.org_admin.email,
                "plant_manager_email": primary.plant_manager.email,
                "operator_email": primary.operator.email if primary.operator else "",
                "viewer_email": primary.viewer.email if primary.viewer else "",
                "passwords": {
                    "super_admin": SUPER_ADMIN_PASSWORD,
                    "org_admin": primary.org_admin.password,
                    "plant_manager": primary.plant_manager.password,
                    "operator": primary.operator.password if primary.operator else "",
                    "viewer": primary.viewer.password if primary.viewer else "",
                },
                "plants": {
                    "A": primary.plants[0]["name"],
                    "B": primary.plants[1]["name"],
                    "C": primary.plants[2]["name"],
                },
                "devices": {
                    "A": primary.smoke_devices[0].device_name,
                    "B": primary.smoke_devices[1].device_name,
                    "C": primary.smoke_devices[2].device_name,
                },
                "device_ids": {
                    "A": primary.smoke_devices[0].device_id,
                    "B": primary.smoke_devices[1].device_id,
                    "C": primary.smoke_devices[2].device_id,
                },
                "tenant_id": primary.id,
                "secondary_tenant_id": secondary.id,
            },
        }


def main() -> int:
    seeder = CertificationSeeder()
    try:
        payload = seeder.run()
        print(json.dumps(payload, indent=2))
        return 0
    finally:
        seeder.close()


if __name__ == "__main__":
    raise SystemExit(main())
