"""
Typed HTTP clients for live E2E tests.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx

from tests.helpers.db_client import db_query, db_query_one


TIMEOUT_SHORT = 30
TIMEOUT_LONG = 120
DEFAULT_AUTH_URL = "http://localhost:8090"
DEFAULT_SUPER_ADMIN_EMAIL = os.environ.get("BOOTSTRAP_SUPER_ADMIN_EMAIL", "manash.ray@cittagent.com")
DEFAULT_SUPER_ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_SUPER_ADMIN_PASSWORD", "Shivex@2706!")
_LOGIN_CACHE: dict[tuple[str, str, str], str] = {}


def get_auth_settings() -> tuple[str, str, str]:
    auth_url = (
        os.environ.get("CERTIFY_STACK_AUTH_URL")
        or os.environ.get("AUTH_URL")
        or DEFAULT_AUTH_URL
    )
    email = os.environ.get("CERTIFY_STACK_EMAIL") or DEFAULT_SUPER_ADMIN_EMAIL
    password = os.environ.get("CERTIFY_STACK_PASSWORD") or DEFAULT_SUPER_ADMIN_PASSWORD
    return auth_url.rstrip("/"), email, password


def _discover_default_tenant(required_features: set[str] | None = None) -> str:
    required_features = required_features or set()
    rows = db_query(
        """
        SELECT
            o.id AS tenant_id,
            o.premium_feature_grants_json AS premium_feature_grants_json,
            EXISTS (
                SELECT 1
                FROM devices d
                WHERE d.deleted_at IS NULL
                  AND d.tenant_id = o.id
            ) AS has_device
        FROM organizations o
        WHERE o.is_active = 1
        ORDER BY has_device DESC, o.created_at ASC
        """
    )
    for row in rows:
        grants = row.get("premium_feature_grants_json") or []
        if isinstance(grants, str):
            grants = json.loads(grants or "[]")
        grant_set = {str(item) for item in grants}
        if required_features.issubset(grant_set):
            return str(row["tenant_id"])

    if rows:
        return str(rows[0]["tenant_id"])
    raise RuntimeError("Unable to discover a default tenant for E2E API client")


def _login_super_admin(*, use_cache: bool = True) -> str:
    auth_url, email, password = get_auth_settings()
    cache_key = (auth_url, email, password)
    if use_cache and cache_key in _LOGIN_CACHE:
        return _LOGIN_CACHE[cache_key]
    response = httpx.post(
        f"{auth_url}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=TIMEOUT_SHORT,
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    if use_cache:
        _LOGIN_CACHE[cache_key] = token
    return token


def _ensure_org_premium_features(token: str, tenant_id: str, required_features: set[str]) -> None:
    auth_url, _, _ = get_auth_settings()
    if not required_features:
        return

    response = httpx.get(
        f"{auth_url}/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_SHORT,
    )
    response.raise_for_status()
    payload = response.json()
    grants = {str(item) for item in payload.get("premium_feature_grants") or []}
    if required_features.issubset(grants):
        return

    merged_grants = sorted(grants | required_features)
    update = httpx.put(
        f"{auth_url}/api/v1/tenants/{tenant_id}/entitlements",
        headers={"Authorization": f"Bearer {token}"},
        json={"premium_feature_grants": merged_grants},
        timeout=TIMEOUT_SHORT,
    )
    update.raise_for_status()


def _ensure_default_plant(token: str, tenant_id: str) -> str:
    auth_url, _, _ = get_auth_settings()
    response = httpx.get(
        f"{auth_url}/api/v1/tenants/{tenant_id}/plants",
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_SHORT,
    )
    response.raise_for_status()
    plants = response.json()
    if isinstance(plants, list) and plants:
        return str(plants[0]["id"])

    created = httpx.post(
        f"{auth_url}/api/v1/tenants/{tenant_id}/plants",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "E2E Validation Plant",
            "location": "Validation",
            "timezone": "Asia/Kolkata",
        },
        timeout=TIMEOUT_SHORT,
    )
    created.raise_for_status()
    payload = created.json()
    return str(payload["id"])


class DeviceClient:
    def __init__(self, base: str, headers: dict[str, str], default_plant_id: str):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_SHORT, headers=headers)
        self.default_plant_id = default_plant_id

    def create_device(self, payload: dict) -> dict:
        body = dict(payload)
        body.setdefault("plant_id", self.default_plant_id)
        body.setdefault("device_id_class", "active")
        body.pop("device_id", None)
        resp = self.c.post("/api/v1/devices", json=body)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_device(self, device_id: str) -> dict:
        resp = self.c.get(f"/api/v1/devices/{device_id}")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def delete_device(self, device_id: str, *, soft: bool = True) -> None:
        self.c.delete(f"/api/v1/devices/{device_id}", params={"soft": str(soft).lower()})

    def set_shift(self, device_id: str, payload: dict) -> dict:
        resp = self.c.post(f"/api/v1/devices/{device_id}/shifts", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_shifts(self, device_id: str) -> list:
        resp = self.c.get(f"/api/v1/devices/{device_id}/shifts")
        resp.raise_for_status()
        return self._unwrap_list(resp.json())

    def set_idle_config(self, device_id: str, idle_current_threshold: float = None, full_load_current_a: float = None) -> dict:
        payload = {}
        if idle_current_threshold is not None:
            payload["idle_current_threshold"] = idle_current_threshold
        if full_load_current_a is not None:
            payload["full_load_current_a"] = full_load_current_a
        resp = self.c.post(
            f"/api/v1/devices/{device_id}/idle-config",
            json=payload,
        )
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_idle_config(self, device_id: str) -> dict:
        resp = self.c.get(f"/api/v1/devices/{device_id}/idle-config")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def set_waste_config(self, device_id: str, overconsumption_current_threshold_a: float) -> dict:
        resp = self.c.put(
            f"/api/v1/devices/{device_id}/waste-config",
            json={"overconsumption_current_threshold_a": overconsumption_current_threshold_a},
        )
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_current_state(self, device_id: str) -> dict:
        resp = self.c.get(f"/api/v1/devices/{device_id}/current-state")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def set_parameter_health(self, device_id: str, payload: dict) -> list:
        parameters = payload.get("parameters", [])
        created = []
        for item in parameters:
            converted = {
                "parameter_name": item["field"],
                "normal_min": item.get("normal_min"),
                "normal_max": item.get("normal_max"),
                "weight": item.get("weight"),
                "ignore_zero_value": False,
                "is_active": True,
            }
            resp = self.c.post(f"/api/v1/devices/{device_id}/health-config", json=converted)
            resp.raise_for_status()
            created.append(self._unwrap(resp.json()))
        return created

    def calculate_health_score(self, device_id: str, telemetry_values: dict, machine_state: str = "RUNNING") -> dict:
        resp = self.c.post(
            f"/api/v1/devices/{device_id}/health-score",
            json={"values": telemetry_values, "machine_state": machine_state},
        )
        resp.raise_for_status()
        return resp.json()

    def set_dashboard_widgets(self, device_id: str, payload: dict) -> dict:
        selected_fields = [w["field"] for w in payload.get("widgets", [])]
        resp = self.c.put(
            f"/api/v1/devices/{device_id}/dashboard-widgets",
            json={"selected_fields": selected_fields},
        )
        resp.raise_for_status()
        return resp.json()

    def register_mqtt_credential(self, device_id: str) -> dict:
        resp = self.c.post(f"/api/v1/devices/{device_id}/mqtt-credential/register")
        if resp.status_code == 409:
            resp = self.c.post(f"/api/v1/devices/{device_id}/mqtt-credential/rotate")
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", body)
            cred = data.get("credential", data)
            return {
                "mqtt_username": cred.get("mqtt_username"),
                "mqtt_password": data.get("mqtt_password"),
                "already_exists": True,
            }
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body)
        cred = data.get("credential", data)
        return {
            "mqtt_username": cred.get("mqtt_username"),
            "mqtt_password": data.get("mqtt_password"),
            "already_exists": False,
        }

    def _unwrap(self, body: dict) -> dict:
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def _unwrap_list(self, body) -> list:
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            for key in ("data", "items", "results", "shifts"):
                if key in body and isinstance(body[key], list):
                    return body[key]
        return []


class DataClient:
    def __init__(self, base: str, headers: dict[str, str]):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_SHORT, headers=headers)

    def health(self) -> dict:
        resp = self.c.get("/api/v1/data/health")
        resp.raise_for_status()
        return resp.json()

    def get_telemetry(self, device_id: str, hours_back: int = 2) -> list:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=hours_back)
        resp = self.c.get(
            f"/api/v1/data/telemetry/{device_id}",
            params={
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "limit": 2000,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict):
                return data.get("items", [])
            if isinstance(data, list):
                return data
            return body.get("items", [])
        if isinstance(body, list):
            return body
        return []

    def get_latest(self, device_id: str) -> Optional[dict]:
        items = self.get_telemetry(device_id, hours_back=1)
        return items[-1] if items else None


class RulesClient:
    def __init__(self, base: str, headers: dict[str, str]):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_SHORT, headers=headers)

    def create_rule(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/rules", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_rules(self) -> list:
        resp = self.c.get("/api/v1/rules")
        resp.raise_for_status()
        return self._unwrap_list(resp.json())

    def delete_rule(self, rule_id) -> None:
        self.c.delete(f"/api/v1/rules/{rule_id}")

    def get_alerts(self, device_id: Optional[str] = None) -> list:
        params = {"device_id": device_id} if device_id else {}
        resp = self.c.get("/api/v1/alerts", params=params)
        resp.raise_for_status()
        return self._unwrap_list(resp.json())

    def _unwrap(self, body):
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def _unwrap_list(self, body) -> list:
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            for key in ("data", "items", "rules", "alerts"):
                if key in body and isinstance(body[key], list):
                    return body[key]
        return []


class AnalyticsClient:
    def __init__(self, base: str, headers: dict[str, str]):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_LONG, headers=headers)

    def run_job(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/analytics/run", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_status(self, job_id: str) -> dict:
        resp = self.c.get(f"/api/v1/analytics/status/{job_id}")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_results(self, job_id: str) -> dict:
        resp = self.c.get(f"/api/v1/analytics/formatted-results/{job_id}")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_models(self) -> dict:
        resp = self.c.get("/api/v1/analytics/models")
        resp.raise_for_status()
        return resp.json()

    def _unwrap(self, body):
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


class ReportingClient:
    def __init__(self, base: str, headers: dict[str, str], default_tenant: str):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_LONG, headers=headers)
        self.default_tenant = default_tenant

    def set_tariff(self, payload: dict) -> dict:
        body = {
            "rate": payload["rate_per_kwh"],
            "currency": payload.get("currency", "INR"),
        }
        resp = self.c.post("/api/v1/settings/tariff", json=body)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_tariff(self) -> dict:
        resp = self.c.get("/api/v1/settings/tariff", params={"tenant_id": self.default_tenant})
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def create_notification_channel(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/settings/notifications/email", json={"email": payload["email"]})
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_notification_channels(self) -> list:
        resp = self.c.get("/api/v1/settings/notifications")
        resp.raise_for_status()
        body = resp.json()
        return body.get("email", []) if isinstance(body, dict) else []

    def delete_notification_channel(self, channel_id) -> None:
        self.c.delete(f"/api/v1/settings/notifications/email/{channel_id}")

    def run_energy_report(self, payload: dict) -> dict:
        body = {
            "device_id": payload["device_id"],
            "start_date": payload["start_date"],
            "end_date": payload["end_date"],
            "tenant_id": self.default_tenant,
            "report_name": payload.get("report_name"),
        }
        resp = self.c.post("/api/reports/energy/consumption", json=body)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_report_status(self, report_id: str) -> dict:
        resp = self.c.get(
            f"/api/reports/{report_id}/status",
            params={"tenant_id": self.default_tenant},
        )
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def download_report(self, report_id: str) -> bytes:
        resp = self.c.get(
            f"/api/reports/{report_id}/download",
            params={"tenant_id": self.default_tenant},
        )
        resp.raise_for_status()
        return resp.content

    def _unwrap(self, body):
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


class WasteClient:
    def __init__(self, base: str, headers: dict[str, str]):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_LONG, headers=headers)

    def run_analysis(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/waste/analysis/run", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_status(self, job_id: str) -> dict:
        resp = self.c.get(f"/api/v1/waste/analysis/{job_id}/status")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_result(self, job_id: str) -> dict:
        resp = self.c.get(f"/api/v1/waste/analysis/{job_id}/result")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def download_pdf(self, job_id: str) -> bytes:
        resp = self.c.get(f"/api/v1/waste/analysis/{job_id}/download")
        resp.raise_for_status()
        body = resp.json()
        url = str(body["download_url"])
        if url.startswith("/api/waste/analysis/"):
            url = url.replace("/api/waste/analysis/", "/api/v1/waste/analysis/", 1)
        if url.startswith("/"):
            url = urljoin(str(self.c.base_url), url)
        file_resp = self.c.get(url)
        file_resp.raise_for_status()
        return file_resp.content

    def _unwrap(self, body):
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


class CopilotClient:
    def __init__(self, base: str, headers: dict[str, str]):
        self.c = httpx.Client(base_url=base, timeout=60, headers=headers)

    def chat(self, message: str, history: Optional[list] = None) -> dict:
        resp = self.c.post(
            "/api/v1/copilot/chat",
            json={"message": message, "conversation_history": history or []},
        )
        if resp.status_code not in (200, 503):
            resp.raise_for_status()
        return resp.json()


class APIClient:
    def __init__(self, services: dict):
        token = _login_super_admin()
        required_premium_features = {"analytics", "reports", "waste_analysis", "copilot"}
        default_tenant = _discover_default_tenant(required_premium_features)
        _ensure_org_premium_features(token, default_tenant, required_premium_features)
        default_plant_id = _ensure_default_plant(token, default_tenant)
        self.default_tenant = default_tenant
        self.default_plant_id = default_plant_id
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Target-Tenant-Id": default_tenant,
        }
        self.device = DeviceClient(services["device"], headers, default_plant_id)
        self.data = DataClient(services["data"], headers)
        self.rules = RulesClient(services["rules"], headers)
        self.analytics = AnalyticsClient(services["analytics"], headers)
        self.reporting = ReportingClient(services["reporting"], headers, default_tenant)
        self.waste = WasteClient(services["waste"], headers)
        self.copilot = CopilotClient(services["copilot"], headers)
