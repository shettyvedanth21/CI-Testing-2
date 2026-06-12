#!/usr/bin/env python
from __future__ import annotations

import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = PROJECT_ROOT / "services"
for path in (PROJECT_ROOT, SERVICES_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _load_local_env() -> None:
    env_local = PROJECT_ROOT / ".env.local"
    if not env_local.is_file():
        return
    override_keys = {
        "INTERNAL_SERVICE_SHARED_SECRET",
    }
    for line in env_local.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and (key not in os.environ or key in override_keys):
            os.environ[key] = value


_load_local_env()

from services.shared.tenant_context import build_internal_headers


AUTH_URL = os.environ.get("AUTH_URL", "http://localhost:8090")
DEVICE_URL = os.environ.get("DEVICE_URL", "http://localhost:8000")
RULE_URL = os.environ.get("RULE_URL", "http://localhost:8002")
WASTE_URL = os.environ.get("WASTE_URL", "http://localhost:8087")
ANALYTICS_URL = os.environ.get("ANALYTICS_URL", "http://localhost:8003")

SUPER_ADMIN_EMAIL = os.environ.get(
    "VALIDATE_SUPER_ADMIN_EMAIL",
    os.environ.get("BOOTSTRAP_SUPER_ADMIN_EMAIL", "manash.ray@cittagent.com"),
)
SUPER_ADMIN_PASSWORD = os.environ.get(
    "VALIDATE_SUPER_ADMIN_PASSWORD",
    os.environ.get("BOOTSTRAP_SUPER_ADMIN_PASSWORD", "Shivex@2706!"),
)
VALIDATION_PASSWORD = os.environ.get("VALIDATE_TEMP_PASSWORD", "Validate123!")
HTTP_TIMEOUT = float(os.environ.get("VALIDATE_HTTP_TIMEOUT", "30"))

os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", os.environ.get("INTERNAL_SERVICE_SHARED_SECRET", "test-internal-service-secret-at-least-32-chars"))


@dataclass
class CheckResult:
    status: str
    label: str
    detail: str


@dataclass
class OrgProbe:
    tenant_id: str
    org_name: str
    org_slug: str
    devices: list[dict[str, Any]]

    @property
    def sample_device_id(self) -> str:
        if not self.devices:
            raise RuntimeError(f"No devices discovered for {self.org_name}")
        return str(
            self.devices[0].get("device_id")
            or self.devices[0].get("id")
            or self.devices[0].get("device_name")
        )


class ValidationFailure(RuntimeError):
    pass


class Validator:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=HTTP_TIMEOUT)
        self.results: list[CheckResult] = []
        self.reporting_url = self._resolve_reporting_url()

    def close(self) -> None:
        self.client.close()

    def pass_check(self, label: str, detail: str) -> None:
        self.results.append(CheckResult("PASS", label, detail))
        print(f"  PASS: {label}")

    def fail_check(self, label: str, detail: str) -> None:
        self.results.append(CheckResult("FAIL", label, detail))
        print(f"  FAIL: {label}")
        print(f"        {detail}")

    def blocked_check(self, label: str, detail: str) -> None:
        self.results.append(CheckResult("BLOCKED", label, detail))
        print(f"  BLOCKED: {label}")
        print(f"           {detail}")

    def expect(self, label: str, condition: bool, detail: str) -> None:
        if condition:
            self.pass_check(label, detail)
        else:
            self.fail_check(label, detail)

    def _json_or_text(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return response.text

    def _extract_error_code(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        detail = payload.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            return str(detail["code"])
        code = payload.get("code")
        if isinstance(code, str):
            return code
        error = payload.get("error")
        if isinstance(error, str):
            return error
        return None

    def _serialize(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        return str(payload)

    def login(self, email: str, password: str) -> str:
        response = self.client.post(
            f"{AUTH_URL}/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValidationFailure(f"Login returned no access token for {email}")
        return str(token)

    def get(self, url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> httpx.Response:
        return self.client.get(url, headers=headers, params=params, follow_redirects=True)

    def post(self, url: str, *, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None) -> httpx.Response:
        return self.client.post(url, headers=headers, json=json)

    def _build_internal_headers(self, tenant_id: str | None = None) -> dict[str, str]:
        return build_internal_headers("validate-isolation", tenant_id)

    def _resolve_reporting_url(self) -> str:
        configured = os.environ.get("REPORTING_URL")
        candidates = [configured] if configured else [
            "http://localhost:8085",
            "http://localhost:8008",
        ]

        for candidate in [item for item in candidates if item]:
            try:
                response = self.client.get(f"{candidate}/health")
                if response.status_code == 200:
                    return candidate
            except httpx.HTTPError:
                continue

        return candidates[0]

    def list_orgs(self, super_admin_token: str) -> list[dict[str, Any]]:
        response = self.get(
            f"{AUTH_URL}/api/admin/tenants",
            headers={"Authorization": f"Bearer {super_admin_token}"},
        )
        response.raise_for_status()
        orgs = response.json()
        if not isinstance(orgs, list):
            raise ValidationFailure("Expected /api/admin/tenants to return a list")
        return orgs

    def _preferred_org_ids_from_seed(self) -> list[str]:
        direct_primary = os.environ.get("VALIDATE_PRIMARY_TENANT_ID")
        direct_secondary = os.environ.get("VALIDATE_SECONDARY_TENANT_ID")
        if direct_primary and direct_secondary:
            return [direct_primary, direct_secondary]

        seed_file = os.environ.get("VALIDATE_CERTIFICATION_SEED_FILE")
        seed_json = os.environ.get("VALIDATE_CERTIFICATION_SEED_JSON")
        payload: dict[str, Any] | None = None
        if seed_file:
            try:
                with open(seed_file, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception as exc:
                raise ValidationFailure(f"Unable to read certification seed file {seed_file}: {exc}") from exc
        elif seed_json:
            try:
                loaded = json.loads(seed_json)
                payload = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError as exc:
                raise ValidationFailure(f"Unable to parse VALIDATE_CERTIFICATION_SEED_JSON: {exc}") from exc

        if not payload:
            return [item for item in [direct_primary, direct_secondary] if item]

        strict_env = payload.get("strict_env") if isinstance(payload.get("strict_env"), dict) else {}
        preferred_ids = [
            direct_primary or strict_env.get("VALIDATE_PRIMARY_TENANT_ID"),
            direct_secondary or strict_env.get("VALIDATE_SECONDARY_TENANT_ID"),
        ]
        return [str(item) for item in preferred_ids if item]

    def fetch_fleet_snapshot(self, token: str, tenant_id: str | None = None) -> tuple[httpx.Response, Any]:
        params = {"page": 1, "page_size": 10}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id
        response = self.get(
            f"{DEVICE_URL}/api/v1/devices/dashboard/fleet-snapshot",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        return response, self._json_or_text(response)

    def discover_orgs_with_devices(self, super_admin_token: str) -> list[OrgProbe]:
        preferred_ids = self._preferred_org_ids_from_seed()
        orgs = self.list_orgs(super_admin_token)

        discovered: list[OrgProbe] = []
        for org in orgs:
            tenant_id = str(org["id"])
            response, payload = self.fetch_fleet_snapshot(super_admin_token, tenant_id)
            if response.status_code != 200:
                continue
            devices = payload.get("devices") or []
            if devices:
                discovered.append(
                    OrgProbe(
                        tenant_id=tenant_id,
                        org_name=str(org["name"]),
                        org_slug=str(org["slug"]),
                        devices=devices,
                    )
                )

        if preferred_ids:
            by_id = {org.tenant_id: org for org in discovered}
            selected = [by_id[tenant_id] for tenant_id in preferred_ids if tenant_id in by_id]
            if len(selected) == len(preferred_ids):
                return selected
            missing = [tenant_id for tenant_id in preferred_ids if tenant_id not in by_id]
            raise ValidationFailure(
                f"Preferred validation orgs were not usable for fleet discovery: {', '.join(missing)}"
            )

        preferred_slugs = {"cittagent-pvt-ltd", "tata"}
        preferred_orgs = [org for org in discovered if org.org_slug in preferred_slugs]
        if len(preferred_orgs) >= 2:
            preferred_orgs.sort(key=lambda item: (item.org_slug not in preferred_slugs, item.org_name))
            return preferred_orgs[:2]

        if len(discovered) < 2:
            raise ValidationFailure("Need at least two organizations with devices for isolation validation.")
        return discovered[:2]

    def ensure_plant(self, super_admin_token: str, tenant_id: str, org_slug: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {super_admin_token}"}
        response = self.get(f"{AUTH_URL}/api/v1/tenants/{tenant_id}/plants", headers=headers)
        response.raise_for_status()
        plants = response.json()
        if plants:
            return plants[0]

        suffix = int(time.time() * 1000)
        create = self.post(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/plants",
            headers=headers,
            json={
                "name": f"Validation Plant {org_slug[:20]} {suffix}",
                "location": "Validation",
                "timezone": "Asia/Kolkata",
            },
        )
        create.raise_for_status()
        return create.json()

    def create_org_admin(self, super_admin_token: str, org: OrgProbe) -> tuple[str, str]:
        suffix = f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"
        email = f"validate+{org.org_slug}-{suffix}@factoryops.local"
        response = self.post(
            f"{AUTH_URL}/api/admin/users",
            headers={"Authorization": f"Bearer {super_admin_token}"},
            json={
                "email": email,
                "full_name": f"{org.org_name} Validation Admin",
                "role": "org_admin",
                "tenant_id": org.tenant_id,
                "password": VALIDATION_PASSWORD,
                "plant_ids": [],
            },
        )
        response.raise_for_status()
        return email, VALIDATION_PASSWORD

    def create_plant_manager(self, org_admin_token: str, tenant_id: str, plant_id: str) -> tuple[str, str]:
        suffix = f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"
        email = f"validate+pm-{suffix}@factoryops.local"
        response = self.post(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/users",
            headers={"Authorization": f"Bearer {org_admin_token}"},
            json={
                "email": email,
                "full_name": "Validation Plant Manager",
                "role": "plant_manager",
                "tenant_id": tenant_id,
                "plant_ids": [plant_id],
                "password": VALIDATION_PASSWORD,
            },
        )
        response.raise_for_status()
        return email, VALIDATION_PASSWORD

    def get_org_entitlements(self, token: str, tenant_id: str) -> dict[str, Any]:
        response = self.get(
            f"{AUTH_URL}/api/v1/tenants/{tenant_id}/entitlements",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValidationFailure("Expected entitlements response to be an object")
        return payload

    def run(self) -> int:
        print("")
        print("======================================")
        print(" FactoryOPS Org Isolation Validator")
        print("======================================")
        print("")

        try:
            super_admin_token = self.login(SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
            self.pass_check("Super admin login works", f"Authenticated {SUPER_ADMIN_EMAIL}")
        except Exception as exc:
            self.fail_check("Super admin login works", str(exc))
            return 1

        try:
            orgs = self.discover_orgs_with_devices(super_admin_token)
        except Exception as exc:
            self.blocked_check("Discover two organizations with devices", str(exc))
            return 1

        primary_org, secondary_org = orgs[0], orgs[1]
        print(f"Using tenant pair: {primary_org.org_name} ({primary_org.sample_device_id}) vs {secondary_org.org_name} ({secondary_org.sample_device_id})")

        primary_email, primary_password = self.create_org_admin(super_admin_token, primary_org)
        secondary_email, secondary_password = self.create_org_admin(super_admin_token, secondary_org)
        primary_admin_token = self.login(primary_email, primary_password)
        secondary_admin_token = self.login(secondary_email, secondary_password)
        self.pass_check("Primary org admin login works", primary_email)
        self.pass_check("Secondary org admin login works", secondary_email)

        primary_plant = self.ensure_plant(super_admin_token, primary_org.tenant_id, primary_org.org_slug)
        plant_manager_email, plant_manager_password = self.create_plant_manager(
            primary_admin_token,
            primary_org.tenant_id,
            str(primary_plant["id"]),
        )
        plant_manager_token = self.login(plant_manager_email, plant_manager_password)
        self.pass_check("Plant manager login works", plant_manager_email)

        print("")
        print("-- Backend API checks --")
        print("")

        unscoped_response, unscoped_payload = self.fetch_fleet_snapshot(super_admin_token, None)
        unscoped_code = self._extract_error_code(unscoped_payload)
        self.expect(
            "Super admin fleet-snapshot without tenant scope fails closed",
            unscoped_response.status_code == 403 and unscoped_code == "TENANT_SCOPE_REQUIRED",
            f"status={unscoped_response.status_code} code={unscoped_code}",
        )

        primary_snapshot_response, primary_snapshot_payload = self.fetch_fleet_snapshot(
            super_admin_token,
            primary_org.tenant_id,
        )
        secondary_snapshot_response, secondary_snapshot_payload = self.fetch_fleet_snapshot(
            super_admin_token,
            secondary_org.tenant_id,
        )
        primary_snapshot_text = self._serialize(primary_snapshot_payload)
        secondary_snapshot_text = self._serialize(secondary_snapshot_payload)
        self.expect(
            "Primary tenant fleet snapshot is tenant-scoped",
            primary_snapshot_response.status_code == 200
            and primary_org.sample_device_id in primary_snapshot_text
            and secondary_org.sample_device_id not in primary_snapshot_text,
            f"status={primary_snapshot_response.status_code} sample={primary_org.sample_device_id}/{secondary_org.sample_device_id}",
        )
        self.expect(
            "Secondary tenant fleet snapshot is tenant-scoped",
            secondary_snapshot_response.status_code == 200
            and secondary_org.sample_device_id in secondary_snapshot_text
            and primary_org.sample_device_id not in secondary_snapshot_text,
            f"status={secondary_snapshot_response.status_code} sample={secondary_org.sample_device_id}/{primary_org.sample_device_id}",
        )

        primary_summary_response = self.get(
            f"{DEVICE_URL}/api/v1/devices/dashboard/summary",
            headers={"Authorization": f"Bearer {primary_admin_token}"},
        )
        primary_summary_payload = self._json_or_text(primary_summary_response)
        primary_summary_text = self._serialize(primary_summary_payload)
        self.expect(
            "Primary org admin summary excludes secondary tenant data",
            primary_summary_response.status_code == 200 and secondary_org.sample_device_id not in primary_summary_text,
            f"status={primary_summary_response.status_code}",
        )

        primary_direct_snapshot_response, primary_direct_snapshot_payload = self.fetch_fleet_snapshot(primary_admin_token, None)
        primary_direct_snapshot_text = self._serialize(primary_direct_snapshot_payload)
        self.expect(
            "Primary org admin fleet snapshot works without tenant param",
            primary_direct_snapshot_response.status_code == 200
            and primary_org.sample_device_id in primary_direct_snapshot_text
            and secondary_org.sample_device_id not in primary_direct_snapshot_text,
            f"status={primary_direct_snapshot_response.status_code}",
        )

        foreign_snapshot_response = self.get(
            f"{DEVICE_URL}/api/v1/devices/dashboard/fleet-snapshot",
            headers={"Authorization": f"Bearer {primary_admin_token}"},
            params={"page": 1, "page_size": 10, "tenant_id": secondary_org.tenant_id},
        )
        foreign_snapshot_payload = self._json_or_text(foreign_snapshot_response)
        foreign_code = self._extract_error_code(foreign_snapshot_payload)
        self.expect(
            "Foreign tenant override is rejected for org admin",
            foreign_snapshot_response.status_code == 403 and foreign_code == "TENANT_SCOPE_MISMATCH",
            f"status={foreign_snapshot_response.status_code} code={foreign_code}",
        )

        print("")
        print("-- Auth checks --")
        print("")

        primary_me = self.get(
            f"{AUTH_URL}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {primary_admin_token}"},
        )
        primary_me_payload = self._json_or_text(primary_me)
        self.expect(
            "Primary org admin role is org_admin",
            primary_me.status_code == 200 and primary_me_payload.get("user", {}).get("role") == "org_admin",
            f"status={primary_me.status_code} role={primary_me_payload.get('user', {}).get('role')}",
        )
        self.expect(
            "Primary org admin belongs to primary org",
            primary_me.status_code == 200 and primary_me_payload.get("tenant", {}).get("id") == primary_org.tenant_id,
            f"tenant_id={primary_me_payload.get('tenant', {}).get('id')}",
        )

        secondary_me = self.get(
            f"{AUTH_URL}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {secondary_admin_token}"},
        )
        secondary_me_payload = self._json_or_text(secondary_me)
        self.expect(
            "Secondary org admin belongs to secondary org only",
            secondary_me.status_code == 200 and secondary_me_payload.get("tenant", {}).get("id") == secondary_org.tenant_id,
            f"tenant_id={secondary_me_payload.get('tenant', {}).get('id')}",
        )

        plant_me = self.get(
            f"{AUTH_URL}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {plant_manager_token}"},
        )
        plant_me_payload = self._json_or_text(plant_me)
        plant_ids = plant_me_payload.get("plant_ids", []) if isinstance(plant_me_payload, dict) else []
        self.expect(
            "Plant manager role and plant scope are correct",
            plant_me.status_code == 200
            and plant_me_payload.get("user", {}).get("role") == "plant_manager"
            and str(primary_plant["id"]) in {str(item) for item in plant_ids},
            f"status={plant_me.status_code} plants={plant_ids}",
        )

        primary_entitlements = self.get_org_entitlements(primary_admin_token, primary_org.tenant_id)
        primary_features = {str(item) for item in primary_entitlements.get("available_features", [])}
        secondary_entitlements = self.get_org_entitlements(secondary_admin_token, secondary_org.tenant_id)
        secondary_features = {str(item) for item in secondary_entitlements.get("available_features", [])}

        print("")
        print("-- Internal API security checks --")
        print("")

        try:
            tenantless_notifications = self.get(
                f"{self.reporting_url}/api/v1/settings/notifications",
                headers=self._build_internal_headers(),
            )
            tenantless_payload = self._json_or_text(tenantless_notifications)
            tenantless_code = self._extract_error_code(tenantless_payload)
            self.expect(
                "Reporting notification settings reject tenantless internal requests",
                tenantless_notifications.status_code == 403 and tenantless_code == "TENANT_SCOPE_REQUIRED",
                f"status={tenantless_notifications.status_code} code={tenantless_code}",
            )

            scoped_notifications = self.get(
                f"{self.reporting_url}/api/v1/settings/notifications",
                headers=self._build_internal_headers(primary_org.tenant_id),
            )
            scoped_notifications_payload = self._json_or_text(scoped_notifications)
            self.expect(
                "Reporting notification settings accept tenant-scoped internal requests",
                scoped_notifications.status_code == 200 and isinstance(scoped_notifications_payload, dict) and "email" in scoped_notifications_payload,
                f"status={scoped_notifications.status_code}",
            )
        except httpx.ConnectError as exc:
            self.blocked_check(
                "Reporting notification settings checks",
                f"Reporting service unavailable at {self.reporting_url}: {exc}",
            )

        print("")
        print("-- Cross-service absence checks --")
        print("")

        rules_response = self.get(
            f"{RULE_URL}/api/v1/rules",
            headers={"Authorization": f"Bearer {primary_admin_token}"},
        )
        rules_payload = self._json_or_text(rules_response)
        rules_text = self._serialize(rules_payload)
        self.expect(
            "Rules list for primary org excludes secondary tenant identifier",
            rules_response.status_code == 200 and secondary_org.tenant_id not in rules_text,
            f"status={rules_response.status_code}",
        )

        if "waste_analysis" not in primary_features or "waste_analysis" not in secondary_features:
            self.blocked_check(
                "Waste history cross-tenant check",
                "Selected tenant pair does not have waste_analysis enabled for both tenants.",
            )
        else:
            primary_waste_response = self.get(
                f"{WASTE_URL}/api/v1/waste/analysis/history",
                headers={"Authorization": f"Bearer {primary_admin_token}"},
            )
            secondary_waste_response = self.get(
                f"{WASTE_URL}/api/v1/waste/analysis/history",
                headers={"Authorization": f"Bearer {secondary_admin_token}"},
            )
            primary_waste_payload = self._json_or_text(primary_waste_response)
            secondary_waste_payload = self._json_or_text(secondary_waste_response)
            primary_items = primary_waste_payload.get("items", []) if isinstance(primary_waste_payload, dict) else []
            secondary_items = secondary_waste_payload.get("items", []) if isinstance(secondary_waste_payload, dict) else []
            primary_job_ids = {
                str(item.get("job_id"))
                for item in primary_items
                if isinstance(item, dict) and item.get("job_id") is not None
            }
            secondary_job_ids = {
                str(item.get("job_id"))
                for item in secondary_items
                if isinstance(item, dict) and item.get("job_id") is not None
            }
            overlap = sorted(primary_job_ids & secondary_job_ids)
            self.expect(
                "Waste history remains tenant-scoped between org admins",
                primary_waste_response.status_code == 200
                and secondary_waste_response.status_code == 200
                and not overlap,
                f"primary_status={primary_waste_response.status_code} secondary_status={secondary_waste_response.status_code} overlap={overlap[:5]}",
            )

        if "analytics" not in primary_features:
            self.blocked_check(
                "Analytics jobs cross-tenant check",
                f"Analytics feature is not enabled for {primary_org.org_name}.",
            )
        else:
            analytics_response = self.get(
                f"{ANALYTICS_URL}/api/v1/analytics/jobs",
                headers={"Authorization": f"Bearer {primary_admin_token}"},
            )
            analytics_payload = self._json_or_text(analytics_response)
            analytics_text = self._serialize(analytics_payload)
            self.expect(
                "Analytics jobs for primary org exclude secondary sample device",
                analytics_response.status_code == 200 and secondary_org.sample_device_id not in analytics_text,
                f"status={analytics_response.status_code}",
            )

        print("")
        print("-- TypeScript check --")
        print("")
        from subprocess import run

        typecheck = run(
            ["npx", "tsc", "--noEmit"],
            cwd=os.path.join(os.getcwd(), "ui-web"),
            capture_output=True,
            text=True,
        )
        if typecheck.returncode == 0:
            self.pass_check("TypeScript zero errors", "ui-web typecheck passed")
        else:
            output = (typecheck.stdout + typecheck.stderr).strip()
            self.fail_check("TypeScript zero errors", output or "TypeScript exited with a non-zero status")

        passed = sum(1 for result in self.results if result.status == "PASS")
        failed = sum(1 for result in self.results if result.status == "FAIL")
        blocked = sum(1 for result in self.results if result.status == "BLOCKED")

        print("")
        print("======================================")
        print(f" Results: {passed} passed, {failed} failed, {blocked} blocked")
        print("======================================")
        print("")

        if failed == 0 and blocked == 0:
            print(" ALL PASSED. Org isolation smoke checks are current.")
            return 0

        if failed:
            print(f" {failed} checks failed. Investigate before browser validation.")
        if blocked:
            print(f" {blocked} checks were blocked by environment or missing validation prerequisites.")
        return 1


def main() -> int:
    validator = Validator()
    try:
        return validator.run()
    finally:
        validator.close()


if __name__ == "__main__":
    sys.exit(main())
