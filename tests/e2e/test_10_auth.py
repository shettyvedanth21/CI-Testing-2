"""
Phase 9 - Auth system end-to-end tests.
Run: pytest tests/e2e/test_10_auth.py -v
Requires full Docker Compose stack running.
Requires super admin: admin@factoryops.local / Admin1234!
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
import pytest_asyncio
from jose import jwt

from tests.helpers.api_client import DeviceClient

AUTH_URL = "http://localhost:8090"
DEVICE_URL = "http://localhost:8000"
SUPER_ADMIN_EMAIL = "admin@factoryops.local"
SUPER_ADMIN_PASSWORD = "Admin1234!"
TIMEOUT = 20.0

SERVICES = {"device": DEVICE_URL}


def _unique(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000)}"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _detail_payload(response: httpx.Response) -> Any:
    payload = response.json()
    return payload.get("detail", payload) if isinstance(payload, dict) else payload


def _detail_message(response: httpx.Response) -> str:
    detail = _detail_payload(response)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)
    return str(detail)


async def _login(
    client: httpx.AsyncClient,
    email: str,
    password: str,
) -> httpx.Response:
    return await client.post(
        f"{AUTH_URL}/api/v1/auth/login",
        json={"email": email, "password": password},
    )


async def _login_token(
    client: httpx.AsyncClient,
    email: str,
    password: str,
) -> str:
    response = await _login(client, email, password)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def _create_org(client: httpx.AsyncClient, token: str, *, name: str, slug: str) -> dict[str, Any]:
    response = await client.post(
        f"{AUTH_URL}/api/admin/tenants",
        headers=_auth_headers(token),
        json={"name": name, "slug": slug},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_user_via_admin(
    client: httpx.AsyncClient,
    token: str,
    *,
    tenant_id: str,
    email: str,
    password: str,
    full_name: str,
) -> dict[str, Any]:
    response = await client.post(
        f"{AUTH_URL}/api/admin/users",
        headers=_auth_headers(token),
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
            "role": "org_admin",
            "tenant_id": tenant_id,
            "plant_ids": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_user_via_org(
    client: httpx.AsyncClient,
    token: str,
    *,
    tenant_id: str,
    email: str,
    password: str,
    full_name: str,
    role: str,
    plant_ids: list[str] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        f"{AUTH_URL}/api/v1/tenants/{tenant_id}/users",
        headers=_auth_headers(token),
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
            "role": role,
            "tenant_id": tenant_id,
            "plant_ids": plant_ids or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def device_helper() -> DeviceClient:
    return DeviceClient(DEVICE_URL)


@pytest_asyncio.fixture(scope="module")
async def super_admin_token() -> str:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await _login_token(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)


@pytest_asyncio.fixture(scope="module")
async def test_org(super_admin_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await _create_org(
            client,
            super_admin_token,
            name=f"Auth E2E Org {_unique('name')}",
            slug=_unique("auth-e2e-org"),
        )


@pytest_asyncio.fixture(scope="module")
async def test_plant(super_admin_token: str, test_org: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{AUTH_URL}/api/v1/tenants/{test_org['id']}/plants",
            headers=_auth_headers(super_admin_token),
            json={"name": "Auth E2E Plant", "location": "Pune", "timezone": "Asia/Kolkata"},
        )
        assert response.status_code == 201, response.text
        return response.json()


@pytest_asyncio.fixture(scope="module")
async def org_admin_token(super_admin_token: str, test_org: dict[str, Any]) -> str:
    email = f"{_unique('org-admin')}@factoryops.local"
    password = "OrgAdmin1234!"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        await _create_user_via_admin(
            client,
            super_admin_token,
            tenant_id=test_org["id"],
            email=email,
            password=password,
            full_name="Auth E2E Org Admin",
        )
        return await _login_token(client, email, password)


@pytest_asyncio.fixture(scope="module")
async def plant_manager_token(org_admin_token: str, test_org: dict[str, Any], test_plant: dict[str, Any]) -> str:
    email = f"{_unique('plant-manager')}@factoryops.local"
    password = "PlantManager1234!"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        await _create_user_via_org(
            client,
            org_admin_token,
            tenant_id=test_org["id"],
            email=email,
            password=password,
            full_name="Auth E2E Plant Manager",
            role="plant_manager",
            plant_ids=[test_plant["id"]],
        )
        return await _login_token(client, email, password)


@pytest_asyncio.fixture(scope="module")
async def viewer_token(org_admin_token: str, test_org: dict[str, Any], test_plant: dict[str, Any]) -> str:
    email = f"{_unique('viewer')}@factoryops.local"
    password = "Viewer1234!"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        await _create_user_via_org(
            client,
            org_admin_token,
            tenant_id=test_org["id"],
            email=email,
            password=password,
            full_name="Auth E2E Viewer",
            role="viewer",
            plant_ids=[test_plant["id"]],
        )
        return await _login_token(client, email, password)


pytestmark = pytest.mark.asyncio


class TestLogin:
    async def test_login_success_returns_tokens(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await _login(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["access_token"]
        assert payload["refresh_token"]
        assert payload["token_type"] == "bearer"
        assert payload["expires_in"] > 0

    async def test_login_wrong_password_returns_401(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await _login(client, SUPER_ADMIN_EMAIL, "wrongpassword")
        assert response.status_code == 401

    async def test_login_nonexistent_email_returns_401(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await _login(client, f"{_unique('missing')}@factoryops.local", "wrongpassword")
        assert response.status_code == 401

    async def test_login_wrong_password_message_is_generic(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await _login(client, SUPER_ADMIN_EMAIL, "wrongpassword")
        assert response.status_code == 401
        message = _detail_message(response).lower()
        assert "password" not in message
        assert "email" not in message
        assert "credential" in message

    async def test_login_sets_refresh_cookie(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await _login(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
        assert response.status_code == 200, response.text
        set_cookie = response.headers.get("set-cookie", "").lower()
        assert "refresh_token=" in set_cookie
        assert "httponly" in set_cookie

    async def test_access_token_claims_are_correct(self, super_admin_token: str) -> None:
        claims = jwt.get_unverified_claims(super_admin_token)
        assert claims["sub"]
        assert claims["role"] == "super_admin"
        assert claims["permissions_version"] == 0
        assert claims["type"] == "access"


class TestRefresh:
    async def test_refresh_with_valid_token_returns_new_tokens(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            login_response = await _login(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
            raw_refresh = login_response.json()["refresh_token"]
            response = await client.post(
                f"{AUTH_URL}/api/v1/auth/refresh",
                json={"refresh_token": raw_refresh},
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["access_token"]
        assert payload["refresh_token"]
        assert payload["refresh_token"] != raw_refresh

    async def test_refresh_with_invalid_token_returns_401(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/v1/auth/refresh",
                json={"refresh_token": "not-a-valid-refresh-token"},
            )
        assert response.status_code == 401

    async def test_refresh_is_rotated(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            login_response = await _login(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
            original_refresh = login_response.json()["refresh_token"]
            refresh_response = await client.post(
                f"{AUTH_URL}/api/v1/auth/refresh",
                json={"refresh_token": original_refresh},
            )
            assert refresh_response.status_code == 200, refresh_response.text
            reused_response = await client.post(
                f"{AUTH_URL}/api/v1/auth/refresh",
                json={"refresh_token": original_refresh},
            )
        assert reused_response.status_code == 401

    async def test_access_token_after_refresh_is_valid(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            login_response = await _login(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
            refresh_response = await client.post(
                f"{AUTH_URL}/api/v1/auth/refresh",
                json={"refresh_token": login_response.json()["refresh_token"]},
            )
            access_token = refresh_response.json()["access_token"]
            me_response = await client.get(
                f"{AUTH_URL}/api/v1/auth/me",
                headers=_auth_headers(access_token),
            )
        assert me_response.status_code == 200, me_response.text
        assert me_response.json()["user"]["email"] == SUPER_ADMIN_EMAIL


class TestLogout:
    async def test_logout_invalidates_refresh_token(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            login_response = await _login(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
            raw_refresh = login_response.json()["refresh_token"]
            logout_response = await client.post(
                f"{AUTH_URL}/api/v1/auth/logout",
                json={"refresh_token": raw_refresh},
            )
            refresh_response = await client.post(
                f"{AUTH_URL}/api/v1/auth/refresh",
                json={"refresh_token": raw_refresh},
            )
        assert logout_response.status_code == 200, logout_response.text
        assert refresh_response.status_code == 401

    async def test_logout_always_returns_200(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            login_response = await _login(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
            raw_refresh = login_response.json()["refresh_token"]
            first = await client.post(f"{AUTH_URL}/api/v1/auth/logout", json={"refresh_token": raw_refresh})
            second = await client.post(f"{AUTH_URL}/api/v1/auth/logout", json={"refresh_token": raw_refresh})
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text


class TestMe:
    async def test_me_with_valid_token(self, super_admin_token: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{AUTH_URL}/api/v1/auth/me", headers=_auth_headers(super_admin_token))
        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == SUPER_ADMIN_EMAIL

    async def test_me_with_no_token_returns_401(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{AUTH_URL}/api/v1/auth/me")
        assert response.status_code == 401

    async def test_me_with_invalid_token_returns_401(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{AUTH_URL}/api/v1/auth/me",
                headers=_auth_headers("not.a.real.token"),
            )
        assert response.status_code == 401

    async def test_me_returns_correct_role(self, super_admin_token: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{AUTH_URL}/api/v1/auth/me", headers=_auth_headers(super_admin_token))
        assert response.status_code == 200, response.text
        assert response.json()["user"]["role"] == "super_admin"


class TestAdminOrgs:
    async def test_super_admin_can_create_org(self, super_admin_token: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/admin/tenants",
                headers=_auth_headers(super_admin_token),
                json={"name": "Phase 9 Admin Org", "slug": _unique("phase9-admin-org")},
            )
        assert response.status_code == 201, response.text
        assert response.json()["id"]

    async def test_duplicate_slug_returns_409(self, super_admin_token: str, test_org: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/admin/tenants",
                headers=_auth_headers(super_admin_token),
                json={"name": "Duplicate Org", "slug": test_org["slug"]},
            )
        assert response.status_code == 409

    async def test_invalid_slug_format_returns_422(self, super_admin_token: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/admin/tenants",
                headers=_auth_headers(super_admin_token),
                json={"name": "Invalid Slug Org", "slug": "INVALID SLUG!"},
            )
        assert response.status_code == 422

    async def test_org_admin_cannot_access_admin_orgs(self, org_admin_token: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{AUTH_URL}/api/admin/tenants", headers=_auth_headers(org_admin_token))
        assert response.status_code == 403

    async def test_list_orgs_returns_created_org(self, super_admin_token: str, test_org: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{AUTH_URL}/api/admin/tenants", headers=_auth_headers(super_admin_token))
        assert response.status_code == 200, response.text
        slugs = [org["slug"] for org in response.json()]
        assert test_org["slug"] in slugs


class TestAdminUsers:
    async def test_super_admin_can_create_org_admin(self, super_admin_token: str, test_org: dict[str, Any]) -> None:
        email = f"{_unique('admin-create')}@factoryops.local"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/admin/users",
                headers=_auth_headers(super_admin_token),
                json={
                    "email": email,
                    "password": "CreateOrgAdmin1234!",
                    "full_name": "Created Org Admin",
                    "role": "org_admin",
                    "tenant_id": test_org["id"],
                    "plant_ids": [],
                },
            )
        assert response.status_code == 201, response.text
        assert response.json()["role"] == "org_admin"
        assert response.json()["tenant_id"] == test_org["id"]

    async def test_duplicate_email_returns_409(self, super_admin_token: str, test_org: dict[str, Any]) -> None:
        email = f"{_unique('dup-admin')}@factoryops.local"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            first = await client.post(
                f"{AUTH_URL}/api/admin/users",
                headers=_auth_headers(super_admin_token),
                json={
                    "email": email,
                    "password": "Duplicate1234!",
                    "full_name": "Duplicate Admin",
                    "role": "org_admin",
                    "tenant_id": test_org["id"],
                    "plant_ids": [],
                },
            )
            second = await client.post(
                f"{AUTH_URL}/api/admin/users",
                headers=_auth_headers(super_admin_token),
                json={
                    "email": email,
                    "password": "Duplicate1234!",
                    "full_name": "Duplicate Admin",
                    "role": "org_admin",
                    "tenant_id": test_org["id"],
                    "plant_ids": [],
                },
            )
        assert first.status_code == 201, first.text
        assert second.status_code == 409

    async def test_cannot_create_non_org_admin_via_admin_endpoint(
        self,
        super_admin_token: str,
        test_org: dict[str, Any],
    ) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/admin/users",
                headers=_auth_headers(super_admin_token),
                json={
                    "email": f"{_unique('bad-role')}@factoryops.local",
                    "password": "PlantManager1234!",
                    "full_name": "Not Allowed",
                    "role": "plant_manager",
                    "tenant_id": test_org["id"],
                    "plant_ids": [],
                },
            )
        assert response.status_code == 422


class TestOrgPlants:
    async def test_org_admin_can_create_plant(self, org_admin_token: str, test_org: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/v1/tenants/{test_org['id']}/plants",
                headers=_auth_headers(org_admin_token),
                json={"name": "Org Admin Plant", "location": "Mumbai", "timezone": "Asia/Kolkata"},
            )
        assert response.status_code == 201, response.text
        assert response.json()["tenant_id"] == test_org["id"]

    async def test_org_admin_cannot_access_other_org(self, org_admin_token: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            other_org = await _create_org(
                client,
                await _login_token(client, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD),
                name="Other Org",
                slug=_unique("other-org"),
            )
            response = await client.post(
                f"{AUTH_URL}/api/v1/tenants/{other_org['id']}/plants",
                headers=_auth_headers(org_admin_token),
                json={"name": "Forbidden Plant", "location": "Delhi", "timezone": "Asia/Kolkata"},
            )
        assert response.status_code == 403


class TestOrgUsers:
    async def test_org_admin_can_create_plant_manager(
        self,
        org_admin_token: str,
        test_org: dict[str, Any],
        test_plant: dict[str, Any],
    ) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/v1/tenants/{test_org['id']}/users",
                headers=_auth_headers(org_admin_token),
                json={
                    "email": f"{_unique('pm-create')}@factoryops.local",
                    "password": "PlantManager1234!",
                    "full_name": "Created Plant Manager",
                    "role": "plant_manager",
                    "tenant_id": test_org["id"],
                    "plant_ids": [test_plant["id"]],
                },
            )
        assert response.status_code == 201, response.text
        assert response.json()["role"] == "plant_manager"
        assert response.json()["tenant_id"] == test_org["id"]

    async def test_org_admin_cannot_create_org_admin(self, org_admin_token: str, test_org: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{AUTH_URL}/api/v1/tenants/{test_org['id']}/users",
                headers=_auth_headers(org_admin_token),
                json={
                    "email": f"{_unique('escalation')}@factoryops.local",
                    "password": "OrgAdmin1234!",
                    "full_name": "Role Escalation",
                    "role": "org_admin",
                    "tenant_id": test_org["id"],
                    "plant_ids": [],
                },
            )
        assert response.status_code == 403

    async def test_org_admin_can_deactivate_user(
        self,
        org_admin_token: str,
        test_org: dict[str, Any],
        test_plant: dict[str, Any],
    ) -> None:
        email = f"{_unique('deactivate')}@factoryops.local"
        password = "Deactivate1234!"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            user = await _create_user_via_org(
                client,
                org_admin_token,
                tenant_id=test_org["id"],
                email=email,
                password=password,
                full_name="Deactivate Me",
                role="viewer",
                plant_ids=[test_plant["id"]],
            )
            response = await client.patch(
                f"{AUTH_URL}/api/v1/tenants/{test_org['id']}/users/{user['id']}/deactivate",
                headers=_auth_headers(org_admin_token),
            )
        assert response.status_code == 200, response.text
        assert response.json()["message"] == "User deactivated"

    async def test_deactivated_user_token_is_revoked(
        self,
        org_admin_token: str,
        test_org: dict[str, Any],
        test_plant: dict[str, Any],
    ) -> None:
        email = f"{_unique('disabled-login')}@factoryops.local"
        password = "Disabled1234!"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            user = await _create_user_via_org(
                client,
                org_admin_token,
                tenant_id=test_org["id"],
                email=email,
                password=password,
                full_name="Disabled User",
                role="viewer",
                plant_ids=[test_plant["id"]],
            )
            deactivate = await client.patch(
                f"{AUTH_URL}/api/v1/tenants/{test_org['id']}/users/{user['id']}/deactivate",
                headers=_auth_headers(org_admin_token),
            )
            assert deactivate.status_code == 200, deactivate.text
            login_response = await _login(client, email, password)
        assert login_response.status_code == 403
        detail = _detail_payload(login_response)
        assert isinstance(detail, dict)
        assert detail.get("code") == "ACCOUNT_DISABLED"


class TestMiddlewareContract:
    async def test_device_service_requires_token(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{DEVICE_URL}/api/v1/devices")
        assert response.status_code == 401, response.text

    async def test_device_service_accessible_with_valid_token_and_tenant_scope(
        self,
        super_admin_token: str,
        test_org: dict[str, Any],
    ) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{DEVICE_URL}/api/v1/devices",
                headers={
                    **_auth_headers(super_admin_token),
                    "X-Target-Tenant-Id": test_org["id"],
                },
            )
        assert response.status_code == 200, response.text

    async def test_device_service_rejects_tenant_param_without_token(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{DEVICE_URL}/api/v1/devices", params={"tenant_id": "default"})
        assert response.status_code == 401, response.text


class TestRoleAccess:
    async def test_viewer_cannot_create_device(self, viewer_token: str, test_org: dict[str, Any], test_plant: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{DEVICE_URL}/api/v1/devices",
                headers=_auth_headers(viewer_token),
                json={
                    "device_id": _unique("viewer-denied"),
                    "tenant_id": test_org["id"],
                    "plant_id": test_plant["id"],
                    "device_name": "Viewer Forbidden Device",
                    "device_type": "compressor",
                    "device_id_class": "active",
                    "location": "Plant Floor",
                    "data_source_type": "metered",
                    "phase_type": "single",
                },
            )
        assert response.status_code == 403, response.text

    async def test_plant_manager_sees_only_their_plant_devices(
        self,
        plant_manager_token: str,
        org_admin_token: str,
        test_org: dict[str, Any],
        test_plant: dict[str, Any],
    ) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            other_plant_response = await client.post(
                f"{AUTH_URL}/api/v1/tenants/{test_org['id']}/plants",
                headers=_auth_headers(org_admin_token),
                json={"name": "Other Plant", "location": "Nashik", "timezone": "Asia/Kolkata"},
            )
            assert other_plant_response.status_code == 201, other_plant_response.text
            other_plant = other_plant_response.json()

        visible_device_id = _unique("pm-visible")
        hidden_device_id = _unique("pm-hidden")

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            visible_response = await client.post(
                f"{DEVICE_URL}/api/v1/devices",
                headers=_auth_headers(org_admin_token),
                json={
                    "device_id": visible_device_id,
                    "tenant_id": test_org["id"],
                    "plant_id": test_plant["id"],
                    "device_name": "Plant Manager Visible Device",
                    "device_type": "compressor",
                    "device_id_class": "active",
                    "location": "Main Plant",
                    "data_source_type": "metered",
                    "phase_type": "single",
                },
            )
            assert visible_response.status_code == 201, visible_response.text

            hidden_response = await client.post(
                f"{DEVICE_URL}/api/v1/devices",
                headers=_auth_headers(org_admin_token),
                json={
                    "device_id": hidden_device_id,
                    "tenant_id": test_org["id"],
                    "plant_id": other_plant["id"],
                    "device_name": "Plant Manager Hidden Device",
                    "device_type": "compressor",
                    "device_id_class": "active",
                    "location": "Other Plant",
                    "data_source_type": "metered",
                    "phase_type": "single",
                },
            )
            assert hidden_response.status_code == 201, hidden_response.text

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{DEVICE_URL}/api/v1/devices",
                headers=_auth_headers(plant_manager_token),
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        device_ids = [item["device_id"] for item in payload.get("data", [])]
        assert visible_device_id in device_ids
        assert hidden_device_id not in device_ids
