#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.helpers.simulator import TelemetrySimulator


ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "preprod-validation"
UI_ROOT = REPO_ROOT / "ui-web"
DEFAULT_PASSWORD = os.environ.get("CERTIFY_SEED_PASSWORD", "Validate123!")
DEFAULT_TIMEOUT = float(os.environ.get("PREPROD_HTTP_TIMEOUT", "30"))
VALIDATION_JWT_SECRET = "validation-jwt-secret-key-at-least-32-characters"

SERVICE_ENDPOINTS = {
    "auth-service": os.environ.get("AUTH_URL", "http://localhost:8090").rstrip("/") + "/health",
    "device-service": os.environ.get("DEVICE_URL", "http://localhost:8000").rstrip("/") + "/health",
    "data-service": os.environ.get("DATA_URL", "http://localhost:8081").rstrip("/") + "/api/v1/data/health",
    "rule-engine-service": os.environ.get("RULE_URL", "http://localhost:8002").rstrip("/") + "/health",
    "reporting-service": os.environ.get("REPORTING_URL", "http://localhost:8085").rstrip("/") + "/health",
    "analytics-service-live": os.environ.get("ANALYTICS_URL", "http://localhost:8003").rstrip("/") + "/health/live",
    "analytics-service-ready": os.environ.get("ANALYTICS_URL", "http://localhost:8003").rstrip("/") + "/health/ready",
    "waste-analysis-service": os.environ.get("WASTE_URL", "http://localhost:8087").rstrip("/") + "/health",
    "energy-service": os.environ.get("ENERGY_URL", "http://localhost:8010").rstrip("/") + "/health",
    "ui-web": os.environ.get("UI_WEB_BASE_URL", "http://localhost:3000").rstrip("/") + "/login",
}

CONTAINER_NAMES = (
    "reporting-service",
    "analytics-service",
    "analytics-worker",
    "rule-engine-service",
    "device-service",
    "auth-service",
    "data-service",
    "ui-web",
)

MONEY_SENSITIVE_ITEMS = {
    "multi_org_isolation",
    "real_telemetry_ingestion",
    "telemetry_influx_contract",
    "role_scoping",
    "rules",
    "real_rule_trigger_execution",
    "notification_delivery_intent",
    "reports",
    "scheduled_reports",
    "analytics",
    "financial_consistency",
    "error_handling",
    "logs_runtime_stability",
    "final_go_no_go",
}

CHECKLIST_TITLES = {
    "fresh_reset_sanity": "Fresh reset sanity",
    "multi_org_isolation": "Multi-org isolation",
    "org_plant_setup": "Org / plant setup",
    "device_onboarding": "Device onboarding",
    "real_telemetry_ingestion": "Real telemetry ingestion",
    "telemetry_influx_contract": "Telemetry / Influx contract",
    "role_scoping": "Role scoping",
    "machines_page": "Machines page",
    "machine_detail_page": "Machine detail page",
    "rules": "Rules",
    "real_rule_trigger_execution": "Real rule trigger execution",
    "per_rule_notification_recipients": "Per-rule notification recipients",
    "notification_delivery_intent": "Notification delivery intent",
    "settings": "Settings",
    "legacy_notification_migration_behavior": "Legacy notification migration behavior",
    "reports": "Reports",
    "scheduled_reports": "Scheduled reports",
    "analytics": "Analytics",
    "financial_consistency": "Financial consistency",
    "error_handling": "Error handling",
    "hardware_lifecycle": "Hardware lifecycle",
    "hardware_integrity": "Hardware integrity",
    "logs_runtime_stability": "Logs / runtime stability",
    "final_go_no_go": "Final GO / NO-GO",
}


def _load_script_module(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


certify_release_contracts = _load_script_module(
    REPO_ROOT / "scripts" / "certify_release_contracts.py",
    "preprod_certify_release_contracts",
)


@dataclass
class Evidence:
    label: str
    path: str | None = None
    detail: str | None = None


@dataclass
class Finding:
    title: str
    severity: str
    affected_module: str
    affected_role: str
    root_cause: str
    classification: str
    production_blocking: bool
    command: str
    error_output: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class ChecklistResult:
    item_id: str
    title: str
    status: str = "NOT_EXECUTED"
    evidence_summary: str = "Not executed by this run."
    evidence: list[Evidence] = field(default_factory=list)
    failure: dict[str, Any] | None = None


@dataclass
class CommandResult:
    name: str
    command: str
    status: str
    returncode: int
    duration_seconds: float
    stdout_path: str
    stderr_path: str
    classification: str | None = None
    production_blocking: bool | None = None


@dataclass
class RunnerConfig:
    mode: str
    stop_on_first_defect: bool
    artifacts_dir: Path
    cert_python: str
    auth_url: str
    device_url: str
    data_url: str
    rule_url: str
    reporting_url: str
    analytics_url: str
    waste_url: str
    energy_url: str
    ui_url: str
    super_admin_email: str
    super_admin_password: str
    super_admin_full_name: str
    live_org_admin_email: str | None
    live_org_admin_password: str | None
    seed_password: str
    http_timeout: float
    reset_stack: bool = False


class ValidationError(RuntimeError):
    pass


TargetedCommand = tuple[str, list[str], tuple[str, ...], dict[str, str], Path | None]


class AuthClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def login(self, email: str, password: str) -> str:
        response = self.client.post("/api/v1/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValidationError(f"Login for {email} returned no access token.")
        return str(token)

    def me(self, token: str) -> dict[str, Any]:
        response = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        return response.json()


class DeviceClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _headers(token: str, tenant_id: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}"}
        if tenant_id:
            headers["X-Target-Tenant-Id"] = tenant_id
        return headers

    def fleet_snapshot(self, token: str, tenant_id: str | None = None) -> tuple[int, Any]:
        response = self.client.get(
            "/api/v1/devices/dashboard/fleet-snapshot",
            headers=self._headers(token, tenant_id),
            params={"page": 1, "page_size": 50, **({"tenant_id": tenant_id} if tenant_id else {})},
        )
        return response.status_code, _json_or_text(response)

    def list_devices(self, token: str, tenant_id: str) -> list[dict[str, Any]]:
        response = self.client.get(
            "/api/v1/devices",
            headers=self._headers(token, tenant_id),
            params={"page": 1, "limit": 200},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return payload["items"]
        return []

    def create_device(self, token: str, tenant_id: str, payload: dict[str, Any]) -> httpx.Response:
        return self.client.post(
            "/api/v1/devices",
            headers=self._headers(token, tenant_id),
            json=payload,
        )

    def register_mqtt_credential(self, token: str, tenant_id: str, device_id: str) -> dict[str, Any]:
        response = self.client.post(
            f"/api/v1/devices/{device_id}/mqtt-credential/register",
            headers=self._headers(token, tenant_id),
        )
        if response.status_code == 409:
            response = self.client.post(
                f"/api/v1/devices/{device_id}/mqtt-credential/rotate",
                headers=self._headers(token, tenant_id),
            )
            response.raise_for_status()
            body = response.json().get("data", response.json())
            cred = body.get("credential", body)
            return {"mqtt_username": cred.get("mqtt_username"), "mqtt_password": body.get("mqtt_password"), "already_exists": True}
        response.raise_for_status()
        body = response.json().get("data", response.json())
        cred = body.get("credential", body)
        return {"mqtt_username": cred.get("mqtt_username"), "mqtt_password": body.get("mqtt_password"), "already_exists": False}


class DataClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _headers(token: str, tenant_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Target-Tenant-Id": tenant_id,
        }

    def history(self, token: str, tenant_id: str, device_id: str, *, minutes_back: int = 120) -> list[dict[str, Any]]:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=minutes_back)
        response = self.client.get(
            f"/api/v1/data/telemetry/{device_id}",
            headers=self._headers(token, tenant_id),
            params={
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "limit": 500,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data["items"]
            if isinstance(data, list):
                return data
            if isinstance(payload.get("items"), list):
                return payload["items"]
        if isinstance(payload, list):
            return payload
        return []

    def latest(self, token: str, tenant_id: str, device_id: str) -> dict[str, Any] | None:
        response = self.client.get(
            f"/api/v1/data/telemetry/{device_id}/latest",
            headers=self._headers(token, tenant_id),
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and isinstance(data.get("item"), dict):
            return data["item"]
        if isinstance(data, dict):
            return data
        return payload if isinstance(payload, dict) else None

    def latest_batch(self, token: str, tenant_id: str, device_ids: list[str]) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/data/telemetry/latest-batch",
            headers=self._headers(token, tenant_id),
            json={"device_ids": device_ids},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and isinstance(data.get("items"), dict):
            return data["items"]
        return {}


class RuleClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _headers(token: str, tenant_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Target-Tenant-Id": tenant_id,
        }

    def create_rule(self, token: str, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post("/api/v1/rules", headers=self._headers(token, tenant_id), json=payload)
        response.raise_for_status()
        body = response.json()
        return body.get("data", body)

    def list_rules(self, token: str, tenant_id: str) -> list[dict[str, Any]]:
        response = self.client.get("/api/v1/rules", headers=self._headers(token, tenant_id))
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            return body["data"]
        return []

    def alerts(self, token: str, tenant_id: str, device_id: str | None = None) -> list[dict[str, Any]]:
        params = {"device_id": device_id} if device_id else None
        response = self.client.get("/api/v1/alerts", headers=self._headers(token, tenant_id), params=params)
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            return body["data"]
        return []


class ReportingClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _headers(token: str, tenant_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Target-Tenant-Id": tenant_id,
        }

    def create_energy_report(
        self,
        token: str,
        tenant_id: str,
        *,
        device_id: str,
        start_date: date,
        end_date: date,
        report_name: str,
    ) -> httpx.Response:
        return self.client.post(
            "/api/reports/energy/consumption",
            headers=self._headers(token, tenant_id),
            json={
                "device_id": device_id,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "tenant_id": tenant_id,
                "report_name": report_name,
            },
        )

    def report_status(self, token: str, tenant_id: str, report_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"/api/reports/{report_id}/status",
            headers=self._headers(token, tenant_id),
            params={"tenant_id": tenant_id},
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("data", payload)

    def download_report(self, token: str, tenant_id: str, report_id: str) -> bytes:
        response = self.client.get(
            f"/api/reports/{report_id}/download",
            headers=self._headers(token, tenant_id),
            params={"tenant_id": tenant_id},
        )
        response.raise_for_status()
        return response.content


class AnalyticsClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _headers(token: str, tenant_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Target-Tenant-Id": tenant_id,
        }

    def models(self, token: str, tenant_id: str) -> dict[str, Any]:
        response = self.client.get("/api/v1/analytics/models", headers=self._headers(token, tenant_id))
        response.raise_for_status()
        return response.json()

    def run_job(self, token: str, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post("/api/v1/analytics/run", headers=self._headers(token, tenant_id), json=payload)
        response.raise_for_status()
        body = response.json()
        return body.get("data", body)

    def status(self, token: str, tenant_id: str, job_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"/api/v1/analytics/status/{job_id}",
            headers=self._headers(token, tenant_id),
        )
        response.raise_for_status()
        body = response.json()
        return body.get("data", body)

    def results(self, token: str, tenant_id: str, job_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"/api/v1/analytics/formatted-results/{job_id}",
            headers=self._headers(token, tenant_id),
        )
        response.raise_for_status()
        body = response.json()
        return body.get("data", body)


def shell_join(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def with_pythonpath(env: dict[str, str], *paths: Path) -> dict[str, str]:
    updated = env.copy()
    existing = updated.get("PYTHONPATH", "")
    ordered = [str(path) for path in paths if path]
    if existing:
        ordered.append(existing)
    updated["PYTHONPATH"] = os.pathsep.join(ordered)
    return updated


def slugify(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-") or "artifact"


def compact_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def build_targeted_validation_commands(
    config: RunnerConfig,
    env: dict[str, str],
    *,
    include_full_validation_extras: bool,
) -> list[TargetedCommand]:
    targeted_commands: list[TargetedCommand] = [
        (
            "Entitlement contract regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "tests/test_feature_entitlement_gate_contract.py",
                "-q",
            ],
            ("role_scoping",),
            with_pythonpath(
                env,
                REPO_ROOT,
                REPO_ROOT / "services",
            ),
            None,
        ),
        (
            "Auth entitlement revocation regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "tests/test_token_version_revocation.py::test_shared_middleware_rejects_stale_tenant_entitlements_version",
                "-q",
            ],
            ("role_scoping",),
            with_pythonpath(
                env,
                REPO_ROOT / "services" / "auth-service",
                REPO_ROOT / "services",
                REPO_ROOT,
            ),
            REPO_ROOT / "services" / "auth-service",
        ),
        (
            "Device entitlement config regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "services/device-service/tests/test_device_config_write_access.py::test_org_admin_cannot_read_waste_config_without_waste_analysis_entitlement",
                "services/device-service/tests/test_device_config_write_access.py::test_org_admin_cannot_write_waste_config_without_waste_analysis_entitlement",
                "-q",
            ],
            ("role_scoping",),
            with_pythonpath(
                env,
                REPO_ROOT,
                REPO_ROOT / "services",
                REPO_ROOT / "services" / "device-service",
            ),
            None,
        ),
        (
            "Device onboarding ID allocation regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "tests/test_device_id_generation.py::test_generated_device_id_repairs_stale_sequence_after_existing_conflicts",
                "tests/test_device_id_generation.py::test_device_id_sequences_increment_per_prefix",
                "-q",
            ],
            ("device_onboarding", "error_handling"),
            with_pythonpath(
                env,
                REPO_ROOT / "services" / "device-service",
                REPO_ROOT / "services",
                REPO_ROOT,
            ),
            REPO_ROOT / "services" / "device-service",
        ),
        (
            "Platform maintenance status regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "tests/test_platform_maintenance_status.py",
                "-q",
            ],
            ("logs_runtime_stability",),
            with_pythonpath(
                env,
                REPO_ROOT / "services" / "auth-service",
                REPO_ROOT / "services",
                REPO_ROOT,
            ),
            REPO_ROOT / "services" / "auth-service",
        ),
        (
            "Platform maintenance admin regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "tests/test_platform_maintenance.py::test_platform_maintenance_list_returns_effective_statuses",
                "tests/test_platform_maintenance.py::test_platform_maintenance_update_can_switch_to_broadcast_all",
                "tests/test_platform_maintenance.py::test_current_platform_maintenance_allows_super_admin_tenant_scope_header",
                "-q",
            ],
            ("logs_runtime_stability",),
            with_pythonpath(
                env,
                REPO_ROOT / "services" / "auth-service",
                REPO_ROOT / "services",
                REPO_ROOT,
            ),
            REPO_ROOT / "services" / "auth-service",
        ),
        (
            "Platform maintenance delivery regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "tests/test_platform_maintenance_delivery.py::test_sync_broadcast_announcement_reaches_all_active_orgs_but_excludes_suspended_orgs",
                "tests/test_platform_maintenance_delivery.py::test_current_banner_query_honors_selected_vs_broadcast_targeting",
                "-q",
            ],
            ("logs_runtime_stability",),
            with_pythonpath(
                env,
                REPO_ROOT / "services" / "auth-service",
                REPO_ROOT / "services",
                REPO_ROOT,
            ),
            REPO_ROOT / "services" / "auth-service",
        ),
        (
            "Simulatorctl startup regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "scripts/tests/test_simulatorctl.py",
                "scripts/tests/test_mqtt_non_tls_contract.py",
                "tools/device-simulator/tests/test_credential_bootstrap.py",
                "tools/device-simulator/tests/test_internal_service_auth.py",
                "tools/device-simulator/tests/test_main.py",
                "tools/device-simulator/tests/test_provisioning_bundle.py",
                "tools/device-simulator/tests/test_simulator.py::FallbackHeartbeatStateTests::test_send_device_heartbeat_uses_signed_internal_headers",
                "-q",
            ],
            ("device_onboarding", "error_handling", "logs_runtime_stability"),
            env,
            None,
        ),
        (
            "Deploy recovery regression tests",
            [
                "npm",
                "exec",
                "--",
                "tsx",
                "--test",
                "tests/unit/deployRecovery.test.ts",
                "tests/unit/authBootstrap.test.ts",
                "tests/unit/apiFetch.auth-recovery.test.ts",
            ],
            ("error_handling", "logs_runtime_stability"),
            env,
            REPO_ROOT / "ui-web",
        ),
        (
            "Machine activity-history resilience regression tests",
            [
                "npm",
                "exec",
                "--",
                "tsx",
                "--test",
                "tests/unit/activityHistoryResilience.test.ts",
            ],
            ("machine_detail_page", "error_handling"),
            env,
            REPO_ROOT / "ui-web",
        ),
        (
            "Machine dashboard bootstrap latency guard regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "services/device-service/tests/test_dashboard_bootstrap_latency_guard.py",
                "services/device-service/tests/test_dashboard_tariff_cache.py",
                "-q",
            ],
            ("machine_detail_page",),
            with_pythonpath(
                env,
                REPO_ROOT,
                REPO_ROOT / "services",
                REPO_ROOT / "services" / "device-service",
            ),
            None,
        ),
        (
            "Machine detail bootstrap frontend contract tests",
            [
                "npm",
                "exec",
                "--",
                "tsx",
                "--test",
                "tests/unit/machineDetailLoadContract.test.ts",
            ],
            ("machine_detail_page", "error_handling"),
            env,
            REPO_ROOT / "ui-web",
        ),
        (
            "Analytics long-running truthfulness backend regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "services/analytics-service/tests/unit/test_job_status_estimator.py",
                "services/analytics-service/tests/unit/test_job_status_route_payload.py",
                "-q",
            ],
            ("analytics", "error_handling"),
            with_pythonpath(
                env,
                REPO_ROOT,
                REPO_ROOT / "services" / "analytics-service",
            ),
            None,
        ),
        (
            "Analytics long-running truthfulness frontend regression tests",
            [
                "npm",
                "exec",
                "--",
                "tsx",
                "--test",
                "tests/unit/analyticsAsyncProgressTruthfulness.test.ts",
            ],
            ("analytics",),
            env,
            REPO_ROOT / "ui-web",
        ),
        (
            "Targeted financial consistency tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "tests/test_energy_service_cost_alignment.py",
                "tests/test_energy_accounting.py",
                "services/device-service/tests/test_loss_accounting_consistency.py",
                "-q",
            ],
            ("financial_consistency",),
            with_pythonpath(
                env,
                REPO_ROOT,
                REPO_ROOT / "services" / "device-service",
                REPO_ROOT / "services" / "energy-service",
            ),
            None,
        ),
        (
            "Truth parity gate invariants",
            ["bash", str(REPO_ROOT / "scripts" / "run-truth-parity-gate.sh")],
            ("financial_consistency",),
            env,
            None,
        ),
        (
            "Notification and settings regression tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "services/rule-engine-service/tests/test_notification_adapter.py",
                "services/reporting-service/tests/test_settings_notifications_migration.py",
                "tests/test_reporting_settings_tenant_isolation.py",
                "-q",
            ],
            ("per_rule_notification_recipients", "settings", "legacy_notification_migration_behavior"),
            with_pythonpath(
                env,
                REPO_ROOT,
                REPO_ROOT / "services" / "rule-engine-service",
                REPO_ROOT / "services" / "reporting-service",
            ),
            None,
        ),
        (
            "Scheduled report reliability tests",
            [
                config.cert_python,
                "-m",
                "pytest",
                "services/reporting-service/tests/test_scheduler_reliability.py",
                "services/reporting-service/tests/test_report_history_scope.py",
                "-q",
            ],
            ("scheduled_reports",),
            with_pythonpath(
                env,
                REPO_ROOT,
                REPO_ROOT / "services" / "reporting-service",
            ),
            None,
        ),
    ]

    if include_full_validation_extras:
        targeted_commands.extend(
            [
                (
                    "Hardware lifecycle regression tests",
                    [
                        config.cert_python,
                        "-m",
                        "pytest",
                        "services/device-service/tests/test_hardware_inventory.py",
                        "services/device-service/tests/test_hardware_decommission_precision.py",
                        "-q",
                    ],
                    ("hardware_lifecycle",),
                    with_pythonpath(
                        env,
                        REPO_ROOT,
                        REPO_ROOT / "services" / "device-service",
                    ),
                    None,
                ),
                (
                    "Hardware integrity regression tests",
                    [
                        config.cert_python,
                        "-m",
                        "pytest",
                        "services/device-service/tests/test_hardware_inventory_schema.py",
                        "services/device-service/tests/test_hardware_inventory_migration.py",
                        "services/device-service/tests/test_hardware_legacy_remediation.py",
                        "-q",
                    ],
                    ("hardware_integrity",),
                    with_pythonpath(
                        env,
                        REPO_ROOT,
                        REPO_ROOT / "services" / "device-service",
                    ),
                    None,
                ),
                (
                    "Hardware error-handling regression tests",
                    [
                        config.cert_python,
                        "-m",
                        "pytest",
                        "services/device-service/tests/test_hardware_inventory.py::test_user_cannot_submit_manual_hardware_unit_id",
                        "services/device-service/tests/test_hardware_inventory.py::test_metadata_json_is_not_accepted_in_hardware_unit_contract",
                        "services/device-service/tests/test_hardware_inventory.py::test_invalid_unit_type_is_rejected_by_backend",
                        "services/device-service/tests/test_hardware_inventory.py::test_invalid_installation_role_is_rejected_by_backend",
                        "services/device-service/tests/test_hardware_inventory.py::test_invalid_hardware_role_pairing_is_rejected",
                        "services/device-service/tests/test_hardware_inventory.py::test_installation_rejects_tenant_and_plant_mismatch",
                        "services/device-service/tests/test_hardware_inventory.py::test_installation_service_rejects_tenant_mismatch",
                        "-q",
                    ],
                    ("error_handling",),
                    with_pythonpath(
                        env,
                        REPO_ROOT,
                        REPO_ROOT / "services" / "device-service",
                    ),
                    None,
                ),
            ]
        )

    return targeted_commands


class PreprodValidationRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.artifacts_dir = config.artifacts_dir
        self.commands_dir = self.artifacts_dir / "commands"
        self.logs_dir = self.artifacts_dir / "logs"
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = datetime.now(timezone.utc)
        self.reset_steps_performed: list[str] = []
        self.validation_setup: dict[str, Any] = {
            "mode": config.mode,
            "environment": {
                "repo_root": str(REPO_ROOT),
                "cert_python": config.cert_python,
                "auth_url": config.auth_url,
                "device_url": config.device_url,
                "data_url": config.data_url,
                "rule_url": config.rule_url,
                "reporting_url": config.reporting_url,
                "analytics_url": config.analytics_url,
                "started_at": self.started_at.isoformat(),
            },
            "reset_steps_performed": self.reset_steps_performed,
            "orgs": [],
            "users": [],
            "plants": [],
            "devices": [],
        }
        self.findings: list[Finding] = []
        self.commands: list[CommandResult] = []
        self.logs_review: list[dict[str, Any]] = []
        self.follow_ups: list[str] = []
        self.checklist: dict[str, ChecklistResult] = {
            item_id: ChecklistResult(item_id=item_id, title=title)
            for item_id, title in CHECKLIST_TITLES.items()
        }
        self.abort_for_defect = False
        self.seed_payload: dict[str, Any] | None = None
        self.context: dict[str, Any] = {}
        self.auth = AuthClient(config.auth_url, config.http_timeout)
        self.device = DeviceClient(config.device_url, config.http_timeout)
        self.data = DataClient(config.data_url, config.http_timeout)
        self.rule = RuleClient(config.rule_url, config.http_timeout)
        self.reporting = ReportingClient(config.reporting_url, config.http_timeout)
        self.analytics = AnalyticsClient(config.analytics_url, config.http_timeout)

    def close(self) -> None:
        self.auth.close()
        self.device.close()
        self.data.close()
        self.rule.close()
        self.reporting.close()
        self.analytics.close()

    def _append_evidence(self, item_id: str, evidence: Evidence) -> None:
        self.checklist[item_id].evidence.append(evidence)

    def mark_pass(self, item_id: str, summary: str, evidence: Iterable[Evidence] = ()) -> None:
        item = self.checklist[item_id]
        item.status = "PASS"
        item.evidence_summary = summary
        for entry in evidence:
            item.evidence.append(entry)

    def mark_fail(
        self,
        item_id: str,
        summary: str,
        *,
        command: str,
        error_output: str,
        affected_module: str,
        affected_role: str = "platform",
        evidence: Iterable[Evidence] = (),
        classification: str | None = None,
        production_blocking: bool | None = None,
    ) -> None:
        classification = classification or self._classify_failure(command, error_output)
        if production_blocking is None:
            production_blocking = item_id in MONEY_SENSITIVE_ITEMS and classification == "confirmed product defect"
        item = self.checklist[item_id]
        item.status = "FAIL"
        item.evidence_summary = summary
        item.failure = {
            "classification": classification,
            "production_blocking": production_blocking,
            "command": command,
            "error_output": error_output[-12000:],
        }
        for entry in evidence:
            item.evidence.append(entry)
        if classification == "confirmed product defect":
            self.findings.append(
                Finding(
                    title=item.title,
                    severity="critical" if production_blocking else "medium",
                    affected_module=affected_module,
                    affected_role=affected_role,
                    root_cause=summary,
                    classification=classification,
                    production_blocking=production_blocking,
                    command=command,
                    error_output=error_output[-12000:],
                    evidence=list(item.evidence),
                )
            )
            if self.config.stop_on_first_defect:
                self.abort_for_defect = True

    def _classify_failure(self, command: str, error_output: str) -> str:
        lowered = f"{command}\n{error_output}".lower()
        if any(token in lowered for token in ("importerror", "modulenotfounderror", "no module named", "usage: pytest", "collected 0 items")):
            return "validation harness issue"
        if any(
            token in lowered
            for token in (
                "connection refused",
                "name or service not known",
                "timed out",
                "timeout",
                "service unavailable",
                "tenant pair does not have",
                "reporting service unavailable",
                "did not become healthy",
                "401 unauthorized",
            )
        ):
            return "environment/data issue"
        if any(token in lowered for token in ("flaky", "retried", "re-run", "rerun")):
            return "flaky/unreproduced"
        return "confirmed product defect"

    def _write_command_artifacts(self, slug: str, stdout: str, stderr: str) -> tuple[str, str]:
        stdout_path = self.commands_dir / f"{slug}.stdout.log"
        stderr_path = self.commands_dir / f"{slug}.stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return str(stdout_path), str(stderr_path)

    def run_command(
        self,
        name: str,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.time()
        completed = subprocess.run(
            list(command),
            cwd=str(cwd or REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        duration = time.time() - started
        slug = slugify(name)
        stdout_path, stderr_path = self._write_command_artifacts(slug, completed.stdout, completed.stderr)
        result = CommandResult(
            name=name,
            command=shell_join(command),
            status="PASS" if completed.returncode == 0 else "FAIL",
            returncode=completed.returncode,
            duration_seconds=duration,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        self.commands.append(result)
        return result

    def _wait_for_http_ok(self, url: str, *, timeout_seconds: int = 120) -> tuple[bool, str | None]:
        deadline = time.time() + timeout_seconds
        last_error: str | None = None
        while time.time() < deadline:
            try:
                response = httpx.get(url, timeout=10)
                if response.status_code == 200:
                    return True, None
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(3)
        return False, f"{url} did not become healthy within {timeout_seconds}s ({last_error})"

    def _inspect_container_health(self, container: str) -> str:
        command = [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
            container,
        ]
        completed = subprocess.run(command, cwd=str(REPO_ROOT), capture_output=True, text=True)
        return completed.stdout.strip() or completed.stderr.strip() or f"docker inspect exited {completed.returncode}"

    def _purge_standalone_simulators(self) -> CommandResult:
        return self.run_command(
            "simulator-purge",
            ["./scripts/simulatorctl.sh", "purge"],
        )

    def reset_stack_if_requested(self) -> None:
        if not self.config.reset_stack:
            return
        purge = self._purge_standalone_simulators()
        if purge.status != "PASS":
            self.mark_fail(
                "fresh_reset_sanity",
                "Standalone telemetry simulator cleanup failed before reset.",
                command=purge.command,
                error_output=Path(purge.stderr_path).read_text(encoding="utf-8"),
                affected_module="infrastructure",
                classification="environment/data issue",
                production_blocking=True,
                evidence=[
                    Evidence("simulator purge stdout", purge.stdout_path),
                    Evidence("simulator purge stderr", purge.stderr_path),
                ],
            )
            return
        self.reset_steps_performed.append("./scripts/simulatorctl.sh purge")
        down = self.run_command(
            "docker-compose-down",
            ["docker", "compose", "down", "-v", "--remove-orphans"],
        )
        if down.status != "PASS":
            self.mark_fail(
                "fresh_reset_sanity",
                "docker compose down -v --remove-orphans failed.",
                command=down.command,
                error_output=Path(down.stderr_path).read_text(encoding="utf-8"),
                affected_module="infrastructure",
                classification="environment/data issue",
                production_blocking=True,
                evidence=[
                    Evidence("docker compose down stdout", down.stdout_path),
                    Evidence("docker compose down stderr", down.stderr_path),
                ],
            )
            return
        self.reset_steps_performed.append("docker compose down -v --remove-orphans")
        up = self.run_command(
            "docker-compose-up",
            ["docker", "compose", "up", "-d", "--build"],
        )
        if up.status != "PASS":
            self.mark_fail(
                "fresh_reset_sanity",
                "docker compose up -d --build failed.",
                command=up.command,
                error_output=Path(up.stderr_path).read_text(encoding="utf-8"),
                affected_module="infrastructure",
                classification="environment/data issue",
                production_blocking=True,
                evidence=[
                    Evidence("docker compose up stdout", up.stdout_path),
                    Evidence("docker compose up stderr", up.stderr_path),
                ],
            )
            return
        self.reset_steps_performed.append("docker compose up -d --build")

    def verify_service_health(self) -> None:
        evidence: list[Evidence] = []
        problems: list[str] = []
        for name, url in SERVICE_ENDPOINTS.items():
            ok, detail = self._wait_for_http_ok(url)
            evidence.append(Evidence(label=f"{name} healthcheck", detail=detail or "healthy"))
            if not ok:
                problems.append(f"{name}: {detail}")
        for container in CONTAINER_NAMES:
            health = self._inspect_container_health(container)
            evidence.append(Evidence(label=f"{container} container", detail=health))
            if not any(token in health for token in ("running", "healthy")):
                problems.append(f"{container}: {health}")

        if problems:
            self.mark_fail(
                "fresh_reset_sanity",
                "Required service health verification failed.",
                command="health-verification",
                error_output="\n".join(problems),
                affected_module="infrastructure",
                classification="environment/data issue",
                production_blocking=True,
                evidence=evidence,
            )
            return

        self.mark_pass(
            "fresh_reset_sanity",
            "Required services and containers are healthy.",
            evidence=evidence,
        )

    @staticmethod
    def _load_local_env_into(out_env: dict[str, str]) -> None:
        env_local = REPO_ROOT / ".env.local"
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
            if key and (key not in out_env or key in override_keys):
                out_env[key] = value

    def _seed_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "CERTIFY_STACK_AUTH_URL": self.config.auth_url,
                "CERTIFY_STACK_EMAIL": self.config.super_admin_email,
                "CERTIFY_STACK_PASSWORD": self.config.super_admin_password,
                "VALIDATE_SUPER_ADMIN_EMAIL": self.config.super_admin_email,
                "VALIDATE_SUPER_ADMIN_PASSWORD": self.config.super_admin_password,
                "CERTIFY_SEED_PASSWORD": self.config.seed_password,
                "AUTH_URL": self.config.auth_url,
                "DEVICE_URL": self.config.device_url,
                "DATA_URL": self.config.data_url,
                "RULE_URL": self.config.rule_url,
                "REPORTING_URL": self.config.reporting_url,
                "ANALYTICS_URL": self.config.analytics_url,
                "WASTE_URL": self.config.waste_url,
                "UI_WEB_BASE_URL": self.config.ui_url,
            }
        )
        env.setdefault("JWT_SECRET_KEY", VALIDATION_JWT_SECRET)
        self._load_local_env_into(env)
        _internal_secret = env.get("INTERNAL_SERVICE_SHARED_SECRET")
        if _internal_secret:
            env.setdefault("INTERNAL_SERVICE_SHARED_SECRET", _internal_secret)
        return env

    def _ensure_primary_bundle(self) -> None:
        if self.checklist["fresh_reset_sanity"].status == "FAIL" and self.config.reset_stack:
            return
        seed_command = [self.config.cert_python, "scripts/ensure_certification_orgs.py"]
        result = self.run_command("seed-certification-orgs", seed_command, env=self._seed_env())
        stdout = Path(result.stdout_path).read_text(encoding="utf-8")
        stderr = Path(result.stderr_path).read_text(encoding="utf-8")
        if result.status != "PASS":
            self.mark_fail(
                "org_plant_setup",
                "Certification org seeding failed.",
                command=result.command,
                error_output=stderr or stdout,
                affected_module="validation-data",
                classification=self._classify_failure(result.command, stderr or stdout),
                evidence=[
                    Evidence("seed stdout", result.stdout_path),
                    Evidence("seed stderr", result.stderr_path),
                ],
            )
            return
        try:
            self.seed_payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            self.mark_fail(
                "org_plant_setup",
                "Certification org seeding returned invalid JSON.",
                command=result.command,
                error_output=f"{exc}\n{stdout[:4000]}",
                affected_module="validation-data",
                classification="validation harness issue",
                evidence=[
                    Evidence("seed stdout", result.stdout_path),
                    Evidence("seed stderr", result.stderr_path),
                ],
            )
            return
        self.expand_validation_context()
        self.mark_pass(
            "org_plant_setup",
            "Certification org bundles were seeded successfully.",
            evidence=[
                Evidence("seed stdout", result.stdout_path),
                Evidence("seed stderr", result.stderr_path),
            ],
        )

    def expand_validation_context(self) -> None:
        if not self.seed_payload:
            raise ValidationError("Seed payload is not available.")
        primary = self.seed_payload["primary_org"]
        secondary = self.seed_payload["secondary_org"]
        smoke_context = self.seed_payload["smoke_context"]
        self.context = {
            "primary_org": primary,
            "secondary_org": secondary,
            "smoke_context": smoke_context,
        }
        smoke_context_path = self.artifacts_dir / "smoke_context.json"
        smoke_context_path.write_text(json.dumps(smoke_context, indent=2), encoding="utf-8")
        Path("/tmp/smoke_context.json").write_text(json.dumps(smoke_context, indent=2), encoding="utf-8")
        orgs = [primary, secondary]
        self.validation_setup["orgs"] = orgs
        users = [
            primary["org_admin"],
            primary["plant_manager"],
            secondary["org_admin"],
            secondary["plant_manager"],
            primary.get("operator"),
            primary.get("viewer"),
            {
                "email": self.config.super_admin_email,
                "password": self.config.super_admin_password,
                "role": "super_admin",
            },
        ]
        self.validation_setup["users"] = [user for user in users if user]
        plants = []
        devices = []
        for org in orgs:
            plants.extend(org["plants"])
            devices.extend(org.get("devices") or [])
            devices.extend(org.get("smoke_devices") or [])
        self.validation_setup["plants"] = plants
        self.validation_setup["devices"] = devices

    def _ensure_named_plant(self, org: dict[str, Any], name: str) -> dict[str, Any]:
        for plant in org["plants"]:
            if plant["name"] == name:
                return plant
        raise ValidationError(f"Missing plant {name}")

    def _ensure_org_user(self, role: str) -> tuple[str, str]:
        primary = self.context["primary_org"]
        if role == "org_admin":
            user = primary["org_admin"]
        elif role == "plant_manager":
            user = primary["plant_manager"]
        elif role == "operator":
            user = primary["operator"]
        elif role == "viewer":
            user = primary["viewer"]
        else:
            raise ValidationError(f"Unsupported role {role}")
        return str(user["email"]), str(user["password"])

    def verify_logins_and_device_contracts(self) -> None:
        if not self.context:
            return
        primary = self.context["primary_org"]
        secondary = self.context["secondary_org"]
        smoke_context = self.context["smoke_context"]
        evidence: list[Evidence] = []

        try:
            super_admin_token = self.auth.login(self.config.super_admin_email, self.config.super_admin_password)
            evidence.append(Evidence(label="Login for super admin", detail=self.config.super_admin_email))
        except Exception as exc:
            self.mark_fail(
                "fresh_reset_sanity",
                "Super admin login failed.",
                command="/api/v1/auth/login",
                error_output=str(exc),
                affected_module="auth",
                classification="environment/data issue",
                production_blocking=True,
            )
            return

        if self.config.live_org_admin_email and self.config.live_org_admin_password:
            try:
                self.auth.login(self.config.live_org_admin_email, self.config.live_org_admin_password)
                evidence.append(Evidence(label="Login for provided live org admin", detail=self.config.live_org_admin_email))
            except Exception as exc:
                self.mark_fail(
                    "fresh_reset_sanity",
                    "Provided live org admin login failed.",
                    command="/api/v1/auth/login",
                    error_output=str(exc),
                    affected_module="auth",
                    classification="environment/data issue",
                    production_blocking=True,
                    evidence=evidence,
                )
                return

        login_failures: list[str] = []
        role_tokens: dict[str, str] = {"super_admin": super_admin_token}
        for role in ("org_admin", "plant_manager", "operator", "viewer"):
            email, password = self._ensure_org_user(role)
            try:
                role_tokens[role] = self.auth.login(email, password)
                evidence.append(Evidence(label=f"Login for {role}", detail=email))
            except Exception as exc:
                login_failures.append(f"{role}: {exc}")

        if login_failures:
            self.mark_fail(
                "fresh_reset_sanity",
                "Bootstrap and validation role logins did not complete successfully.",
                command="/api/v1/auth/login",
                error_output="\n".join(login_failures),
                affected_module="auth",
                classification="environment/data issue",
                production_blocking=True,
                evidence=evidence,
            )
            return

        self.mark_pass(
            "role_scoping",
            "Bootstrap and validation logins succeeded for the full role matrix.",
            evidence=evidence,
        )

        org_admin_token = role_tokens["org_admin"]
        devices = self.device.list_devices(org_admin_token, primary["id"])
        smoke_devices = {device["device_name"]: device for device in primary["smoke_devices"]}
        missing_names = [name for name in ("Smoke Device A", "Smoke Device B", "Smoke Device C") if name not in {item.get("device_name") for item in devices}]
        if missing_names:
            self.mark_fail(
                "device_onboarding",
                "Seeded validation smoke devices were not visible from the device inventory.",
                command="GET /api/v1/devices",
                error_output=", ".join(missing_names),
                affected_module="device-service",
                evidence=[Evidence("device inventory", detail=compact_json(devices[:20]))],
            )
        else:
            device_evidence = [
                Evidence(label=device["device_name"], detail=f"plant_id={device['plant_id']}")
                for device in primary["smoke_devices"]
            ]
            create_without_plant = self.device.create_device(
                org_admin_token,
                primary["id"],
                {
                    "device_name": f"Preprod Missing Plant {int(time.time())}",
                    "device_type": "compressor",
                    "location": "Validation",
                    "data_source_type": "metered",
                    "phase_type": "single",
                    "device_id_class": "active",
                },
            )
            device_evidence.append(
                Evidence(
                    "create without plant",
                    detail=f"status={create_without_plant.status_code} body={_json_or_text(create_without_plant)}",
                )
            )
            if create_without_plant.status_code < 400:
                self.mark_fail(
                    "device_onboarding",
                    "Device creation without a plant unexpectedly succeeded.",
                    command="POST /api/v1/devices",
                    error_output=str(create_without_plant.text),
                    affected_module="device-service",
                    evidence=device_evidence,
                )
            else:
                self.mark_pass(
                    "device_onboarding",
                    "Validation roles, plants, and smoke devices were provisioned.",
                    evidence=device_evidence,
                )

        role_scope_evidence: list[Evidence] = []
        scope_failures: list[str] = []
        expected_visibility = {
            "org_admin": {"Smoke Device A", "Smoke Device B", "Smoke Device C"},
            "plant_manager": {"Smoke Device A", "Smoke Device B"},
            "operator": {"Smoke Device A"},
            "viewer": {"Smoke Device A"},
        }
        for role, expected in expected_visibility.items():
            status_code, payload = self.device.fleet_snapshot(role_tokens[role], None if role != "super_admin" else primary["id"])
            devices_text = compact_json(payload)
            role_scope_evidence.append(Evidence(f"{role} fleet snapshot", detail=f"status={status_code}"))
            for visible in expected:
                if visible not in devices_text:
                    scope_failures.append(f"{role} missing {visible}")
            for hidden in {"Smoke Device A", "Smoke Device B", "Smoke Device C"} - expected:
                if hidden in devices_text:
                    scope_failures.append(f"{role} can see unexpected {hidden}")
        if scope_failures:
            self.mark_fail(
                "role_scoping",
                "Role-scoped fleet visibility leaked outside the assigned plant scope.",
                command="GET /api/v1/devices/dashboard/fleet-snapshot",
                error_output="\n".join(scope_failures),
                affected_module="device-service",
                evidence=role_scope_evidence,
            )
        else:
            self.mark_pass(
                "role_scoping",
                "Role-scoped fleet visibility matches org admin, plant manager, operator, and viewer assignments.",
                evidence=role_scope_evidence,
            )

        reset_evidence = [Evidence("smoke context", str(self.artifacts_dir / "smoke_context.json"))]
        if self.config.reset_stack:
            reset_evidence.append(Evidence("bootstrap proof", detail=smoke_context["super_admin_email"]))
        self.mark_pass(
            "fresh_reset_sanity",
            "Required services and bootstrap logins are healthy.",
            evidence=self.checklist["fresh_reset_sanity"].evidence + reset_evidence,
        )

    def publish_and_verify_telemetry(self) -> None:
        if not self.context or self.abort_for_defect:
            return
        primary = self.context["primary_org"]
        tenant_id = primary["id"]
        org_admin_token = self.auth.login(primary["org_admin"]["email"], primary["org_admin"]["password"])
        smoke_device = primary["smoke_devices"][0]
        mqtt_creds = self.device.register_mqtt_credential(org_admin_token, tenant_id, smoke_device["device_id"])
        simulator = TelemetrySimulator(
            "localhost", 1883, smoke_device["device_id"], tenant_id,
            mqtt_username=mqtt_creds.get("mqtt_username"),
            mqtt_password=mqtt_creds.get("mqtt_password"),
        )
        try:
            simulator.send_normal(count=4, interval_sec=0.2)
            simulator.send_spike(count=3, interval_sec=0.2)
        finally:
            simulator.disconnect()

        deadline = time.time() + 60
        latest: dict[str, Any] | None = None
        history: list[dict[str, Any]] = []
        batch: dict[str, Any] = {}
        while time.time() < deadline:
            history = self.data.history(org_admin_token, tenant_id, smoke_device["device_id"])
            latest = self.data.latest(org_admin_token, tenant_id, smoke_device["device_id"])
            batch = self.data.latest_batch(org_admin_token, tenant_id, [smoke_device["device_id"]])
            if len(history) >= 3 and latest and batch.get(smoke_device["device_id"]):
                break
            time.sleep(2)

        telemetry_evidence = [
            Evidence("history count", detail=str(len(history))),
            Evidence("latest telemetry", detail=compact_json(latest or {})),
            Evidence("latest batch", detail=compact_json(batch)),
        ]
        if len(history) < 3 or not latest or not batch.get(smoke_device["device_id"]):
            self.mark_fail(
                "real_telemetry_ingestion",
                "Fresh telemetry did not arrive through the live MQTT ingestion path.",
                command="TelemetrySimulator.send_normal/send_spike",
                error_output=compact_json({"history": len(history), "latest": latest, "batch": batch}),
                affected_module="data-service",
                evidence=telemetry_evidence,
            )
            return

        metadata_fields = {"tenant_id", "table", "debug_marker", "enriched_at"}
        leaked_fields = sorted(field for field in metadata_fields if field in latest or field in batch.get(smoke_device["device_id"], {}))
        if leaked_fields:
            self.mark_fail(
                "telemetry_influx_contract",
                "Telemetry API responses leaked metadata fields that should stay out of the public contract.",
                command="GET/POST telemetry API contract",
                error_output=", ".join(leaked_fields),
                affected_module="data-service",
                production_blocking=True,
                evidence=telemetry_evidence,
            )
        else:
            self.mark_pass(
                "real_telemetry_ingestion",
                "Fresh telemetry arrived through the live MQTT ingestion path and latest/range/batch reads succeeded.",
                evidence=telemetry_evidence,
            )
            influx_evidence = self._verify_influx_contract(tenant_id, smoke_device["device_id"])
            self.mark_pass(
                "telemetry_influx_contract",
                "Influx telemetry rows use the expected measurement/tag contract and downstream APIs stayed metadata-lean.",
                evidence=telemetry_evidence + influx_evidence,
            )

    def _verify_influx_contract(self, tenant_id: str, device_id: str) -> list[Evidence]:
        query = f'''
from(bucket: "telemetry")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "device_telemetry" and r.device_id == "{device_id}" and r.tenant_id == "{tenant_id}")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 20)
'''
        response = httpx.post(
            "http://localhost:8086/api/v2/query?org=energy-org",
            headers={
                "Authorization": "Token energy-token",
                "Accept": "application/csv",
                "Content-Type": "application/vnd.flux",
            },
            content=query,
            timeout=30,
        )
        response.raise_for_status()
        rows = list(csv.DictReader(line for line in io.StringIO(response.text) if not line.startswith("#")))
        if not rows:
            raise ValidationError("No Influx telemetry rows were returned for the smoke device.")
        fields = sorted({row.get("_field") for row in rows if row.get("_field")})
        tag_columns = sorted({key for key in rows[0] if key in {"device_id", "tenant_id"}})
        unexpected = sorted(
            {
                key
                for row in rows
                for key, value in row.items()
                if key not in {"result", "table", "_start", "_stop", "_time", "_value", "_field", "_measurement", "device_id", "tenant_id"}
                and value not in ("", None)
            }
        )
        if "current" not in fields or "power" not in fields or unexpected:
            raise ValidationError(
                compact_json({"fields": fields, "tags": tag_columns, "unexpected_columns": unexpected})
            )
        return [
            Evidence("influx fields", detail=", ".join(fields)),
            Evidence("influx tags", detail=", ".join(tag_columns)),
        ]

    def validate_rules_reporting_and_analytics(self) -> None:
        if not self.context or self.abort_for_defect:
            return
        primary = self.context["primary_org"]
        tenant_id = primary["id"]
        org_admin_token = self.auth.login(primary["org_admin"]["email"], primary["org_admin"]["password"])
        smoke_device = primary["smoke_devices"][0]

        existing_rules = self.rule.list_rules(org_admin_token, tenant_id)
        scope_text = compact_json(existing_rules)
        if self.context["secondary_org"]["id"] in scope_text:
            self.mark_fail(
                "rules",
                "Rules list leaked secondary tenant identifiers into the primary org scope.",
                command="GET /api/v1/rules",
                error_output=scope_text,
                affected_module="rule-engine-service",
                production_blocking=True,
            )
        else:
            self.mark_pass(
                "rules",
                "Scoped rule visibility for the primary org remained tenant-safe.",
                evidence=[Evidence("rules list", detail=scope_text[:2000])],
            )

        rule_name = f"Preprod Validation High Current {int(time.time())}"
        rule_payload = {
            "rule_type": "threshold",
            "rule_name": rule_name,
            "scope": "selected_devices",
            "device_ids": [smoke_device["device_id"]],
            "property": "current",
            "condition": ">",
            "threshold": 30.0,
            "timezone": "Asia/Kolkata",
            "notification_channels": ["email"],
            "notification_recipients": [{"channel": "email", "value": "alerts-validation@example.com"}],
            "cooldown_mode": "interval",
            "cooldown_minutes": 5,
        }
        created_rule = self.rule.create_rule(org_admin_token, tenant_id, rule_payload)
        rule_evidence = [Evidence("rule", detail=compact_json(created_rule))]
        recipients = created_rule.get("notification_recipients") or []
        if not recipients:
            self.mark_fail(
                "per_rule_notification_recipients",
                "Rule creation succeeded but no per-rule notification recipients were persisted.",
                command="POST /api/v1/rules",
                error_output=compact_json(created_rule),
                affected_module="rule-engine-service",
                evidence=rule_evidence,
            )
        else:
            self.mark_pass(
                "per_rule_notification_recipients",
                "Rule creation persisted scoped per-rule recipients on the live stack.",
                evidence=rule_evidence,
            )

        mqtt_creds_2 = self.device.register_mqtt_credential(org_admin_token, tenant_id, smoke_device["device_id"])
        simulator = TelemetrySimulator(
            "localhost", 1883, smoke_device["device_id"], tenant_id,
            mqtt_username=mqtt_creds_2.get("mqtt_username"),
            mqtt_password=mqtt_creds_2.get("mqtt_password"),
        )
        try:
            simulator.send_spike(count=2, interval_sec=0.2)
        finally:
            simulator.disconnect()
        deadline = time.time() + 45
        alerts: list[dict[str, Any]] = []
        while time.time() < deadline:
            alerts = self.rule.alerts(org_admin_token, tenant_id, smoke_device["device_id"])
            if any(alert.get("rule_id") == created_rule.get("rule_id") for alert in alerts):
                break
            time.sleep(2)
        matching_alerts = [alert for alert in alerts if alert.get("rule_id") == created_rule.get("rule_id")]
        if not matching_alerts:
            self.mark_fail(
                "real_rule_trigger_execution",
                "Rule trigger did not produce a live alert artifact after telemetry crossed the threshold.",
                command="live-rule-trigger",
                error_output=compact_json({"rule": created_rule, "alerts": alerts}),
                affected_module="rule-engine-service",
                production_blocking=True,
                evidence=rule_evidence,
            )
        else:
            self.mark_pass(
                "real_rule_trigger_execution",
                "Telemetry triggered the rule and created a live alert artifact.",
                evidence=rule_evidence + [Evidence("alerts", detail=compact_json(matching_alerts[:3]))],
            )
            if len(matching_alerts) > 1:
                self.mark_fail(
                    "notification_delivery_intent",
                    "One telemetry spike sequence produced duplicate live alert artifacts.",
                    command="GET /api/v1/alerts",
                    error_output=compact_json(matching_alerts),
                    affected_module="rule-engine-service",
                    production_blocking=True,
                    evidence=[Evidence("duplicate alerts", detail=compact_json(matching_alerts))],
                )
            else:
                self.mark_pass(
                    "notification_delivery_intent",
                    "A single live trigger produced a single alert artifact with the intended recipient scope.",
                    evidence=[Evidence("alert", detail=compact_json(matching_alerts[0]))],
                )

        report_start = date.today() - timedelta(days=1)
        report_end = date.today()
        report_response = self.reporting.create_energy_report(
            org_admin_token,
            tenant_id,
            device_id=smoke_device["device_id"],
            start_date=report_start,
            end_date=report_end,
            report_name="Preprod Validation Energy Report",
        )
        if report_response.status_code >= 400:
            self.mark_fail(
                "reports",
                "Live energy report creation request failed.",
                command="POST /api/reports/energy/consumption",
                error_output=str(_json_or_text(report_response)),
                affected_module="reporting-service",
                evidence=[Evidence("report payload", detail=compact_json({
                    "device_id": smoke_device["device_id"],
                    "start_date": str(report_start),
                    "end_date": str(report_end),
                    "report_name": "Preprod Validation Energy Report",
                }))],
            )
        else:
            report_payload = report_response.json()
            report_id = report_payload.get("report_id") or report_payload.get("data", {}).get("report_id")
            report_evidence = [Evidence("report create", detail=compact_json(report_payload))]
            report_status = None
            if report_id:
                deadline = time.time() + 90
                while time.time() < deadline:
                    report_status = self.reporting.report_status(org_admin_token, tenant_id, str(report_id))
                    if str(report_status.get("status", "")).lower() in {"completed", "failed"}:
                        break
                    time.sleep(3)
                report_evidence.append(Evidence("report status", detail=compact_json(report_status or {})))
            if not report_id or not report_status or str(report_status.get("status", "")).lower() != "completed":
                self.mark_fail(
                    "reports",
                    "Energy report did not complete successfully in the live stack.",
                    command="POST /api/reports/energy/consumption",
                    error_output=compact_json(report_status or report_payload),
                    affected_module="reporting-service",
                    production_blocking=True,
                    evidence=report_evidence,
                )
            else:
                pdf_bytes = self.reporting.download_report(org_admin_token, tenant_id, str(report_id))
                report_evidence.append(Evidence("pdf_bytes", detail=str(len(pdf_bytes))))
                self.mark_pass(
                    "reports",
                    "Live energy report generation, status polling, and PDF download succeeded.",
                    evidence=report_evidence,
                )

        if self.config.mode == "full-validation" or self.config.mode == "full-reset":
            try:
                models = self.analytics.models(org_admin_token, tenant_id)
                analytics_evidence = [Evidence("models", detail=compact_json(models))]
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(hours=2)
                job = self.analytics.run_job(
                    org_admin_token,
                    tenant_id,
                    {
                        "device_id": smoke_device["device_id"],
                        "analysis_type": "anomaly",
                        "model_name": "anomaly_ensemble",
                        "start_time": start_time.isoformat(),
                        "end_time": end_time.isoformat(),
                    },
                )
                job_id = str(job["job_id"])
                deadline = time.time() + 180
                status_payload = None
                while time.time() < deadline:
                    status_payload = self.analytics.status(org_admin_token, tenant_id, job_id)
                    if str(status_payload.get("status", "")).lower() in {"completed", "failed"}:
                        break
                    time.sleep(5)
                analytics_evidence.append(Evidence("job status", detail=compact_json(status_payload or {})))
                if not status_payload or str(status_payload.get("status", "")).lower() != "completed":
                    self.mark_fail(
                        "analytics",
                        "Analytics live job did not complete successfully.",
                        command="POST /api/v1/analytics/run",
                        error_output=compact_json(status_payload or job),
                        affected_module="analytics-service",
                        production_blocking=True,
                        evidence=analytics_evidence,
                    )
                else:
                    results = self.analytics.results(org_admin_token, tenant_id, job_id)
                    analytics_evidence.append(Evidence("results", detail=compact_json(results)[:3000]))
                    self.mark_pass(
                        "analytics",
                        "Analytics UI/API availability, job submission, and result retrieval succeeded.",
                        evidence=analytics_evidence,
                    )
            except Exception as exc:
                self.mark_fail(
                    "analytics",
                    "Analytics validation failed during live job execution.",
                    command="analytics-live-validation",
                    error_output=str(exc),
                    affected_module="analytics-service",
                    production_blocking=True,
                )

    def run_isolation_and_targeted_suites(self) -> None:
        if self.abort_for_defect:
            return
        env = self._seed_env()
        if self.seed_payload:
            strict_env = self.seed_payload.get("strict_env") or {}
            for key, value in strict_env.items():
                env[str(key)] = str(value)
            env["VALIDATE_CERTIFICATION_SEED_JSON"] = json.dumps(self.seed_payload)
            env["VALIDATE_SMOKE_PASSWORD"] = self.config.seed_password

        isolation = self.run_command(
            "Org isolation validator",
            [self.config.cert_python, "scripts/validate_isolation.py"],
            env=env,
        )
        if isolation.status != "PASS":
            output = Path(isolation.stderr_path).read_text(encoding="utf-8") or Path(isolation.stdout_path).read_text(encoding="utf-8")
            self.mark_fail(
                "multi_org_isolation",
                "Isolation validator failed on the current live stack.",
                command=isolation.command,
                error_output=output,
                affected_module="tenant-scope",
                production_blocking=True,
                evidence=[
                    Evidence("Org isolation validator stdout", isolation.stdout_path),
                    Evidence("Org isolation validator stderr", isolation.stderr_path),
                ],
            )
        else:
            self.mark_pass(
                "multi_org_isolation",
                "Org isolation validator passed against the current live stack.",
                evidence=[
                    Evidence("Org isolation validator stdout", isolation.stdout_path),
                    Evidence("Org isolation validator stderr", isolation.stderr_path),
                ],
            )

        smoke = self.run_command(
            "Preprod scoped UI smoke",
            ["npx", "playwright", "test", "tests/e2e/preprod-scoped-ui-smoke.spec.js"],
            cwd=UI_ROOT,
            env=env,
        )
        smoke_output = Path(smoke.stderr_path).read_text(encoding="utf-8") + Path(smoke.stdout_path).read_text(encoding="utf-8")
        smoke_evidence = [
            Evidence("Preprod scoped UI smoke stdout", smoke.stdout_path),
            Evidence("Preprod scoped UI smoke stderr", smoke.stderr_path),
            Evidence("smoke context", str(self.artifacts_dir / "smoke_context.json")),
        ]
        if smoke.status == "PASS":
            self.mark_pass("machines_page", "Preprod scoped UI smoke validated plant chips, device scoping, and scoped reports visibility.", evidence=smoke_evidence)
            self.mark_pass("machine_detail_page", "Preprod scoped UI smoke validated viewer machine detail tabs and read-only visibility.", evidence=smoke_evidence)
        else:
            self.mark_fail(
                "machines_page",
                "Preprod scoped UI smoke failed on the live stack.",
                command=smoke.command,
                error_output=smoke_output,
                affected_module="ui-web",
                evidence=smoke_evidence,
            )
            self.mark_fail(
                "machine_detail_page",
                "Preprod scoped UI smoke failed on the live stack.",
                command=smoke.command,
                error_output=smoke_output,
                affected_module="ui-web",
                evidence=smoke_evidence,
            )

        targeted_commands = build_targeted_validation_commands(
            self.config,
            env,
            include_full_validation_extras=self.config.mode == "full-validation",
        )
        if self.config.mode in {"quick-gate", "current-live", "full-validation", "full-reset"}:
            for name, command, item_ids, command_env, command_cwd in targeted_commands:
                if self.abort_for_defect:
                    break
                result = self.run_command(name, command, env=command_env, cwd=command_cwd)
                stdout = Path(result.stdout_path).read_text(encoding="utf-8")
                stderr = Path(result.stderr_path).read_text(encoding="utf-8")
                output = f"{stdout}\n{stderr}".strip()
                if result.status == "PASS":
                    for item_id in item_ids:
                        self.mark_pass(
                            item_id,
                            f"{name} passed on the current stack.",
                            evidence=[
                                Evidence(f"{name} stdout", result.stdout_path),
                                Evidence(f"{name} stderr", result.stderr_path),
                            ],
                        )
                else:
                    classification = self._classify_failure(result.command, output)
                    for item_id in item_ids:
                        self.mark_fail(
                            item_id,
                            f"{name} failed with exit code {result.returncode}.",
                            command=result.command,
                            error_output=output,
                            affected_module="automation-suite",
                            classification=classification,
                            production_blocking=item_id in MONEY_SENSITIVE_ITEMS and classification == "confirmed product defect",
                            evidence=[
                                Evidence(f"{name} stdout", result.stdout_path),
                                Evidence(f"{name} stderr", result.stderr_path),
                            ],
                        )

        if self.config.mode in {"quick-gate", "current-live"}:
            self.checklist["analytics"].evidence_summary = "Not executed by this run."
            self.follow_ups.append("Analytics needs a full-validation run before deployment.")

    def sweep_logs(self) -> None:
        issues: list[dict[str, Any]] = []
        evidence: list[Evidence] = []
        tokens = ("Traceback (most recent call last)", "unhandled exception", "crashloop", "lost connection to mysql server during query", "duplicate scheduler")
        for service in CONTAINER_NAMES:
            completed = subprocess.run(
                ["docker", "logs", "--tail", "200", service],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            log_path = self.logs_dir / f"{service}.log"
            combined = (completed.stdout or "") + ("\n" if completed.stdout and completed.stderr else "") + (completed.stderr or "")
            log_path.write_text(combined, encoding="utf-8")
            lowered = combined.lower()
            matched = [token for token in tokens if token in lowered]
            detail = "no critical token" if not matched else ", ".join(matched)
            evidence.append(Evidence(service, str(log_path), detail))
            if matched:
                issues.append(
                    {
                        "service": service,
                        "status": "warning",
                        "expected_vs_defect": f"Unexpected log token(s): {', '.join(matched)}",
                        "artifact": str(log_path),
                    }
                )
        self.logs_review = issues or [
            {
                "service": "all",
                "status": "ok",
                "expected_vs_defect": "No crash-loop, unhandled exception, or duplicate scheduler tokens found in the final log sweep.",
                "artifact": str(self.logs_dir),
            }
        ]
        if issues:
            self.mark_fail(
                "logs_runtime_stability",
                "Log sweep found runtime instability indicators in service logs.",
                command="docker logs --tail 200 <service>",
                error_output=compact_json(issues),
                affected_module="runtime",
                classification="confirmed product defect",
                production_blocking=True,
                evidence=evidence,
            )
        else:
            self.mark_pass(
                "logs_runtime_stability",
                "Final log sweep found no critical runtime stability indicators in required services.",
                evidence=evidence,
            )

    def _recommendation(self) -> dict[str, str]:
        failed = [item for item in self.checklist.values() if item.status == "FAIL"]
        not_executed = [item for item in self.checklist.values() if item.status == "NOT_EXECUTED" and item.item_id != "final_go_no_go"]
        if failed:
            return {
                "decision": "NO-GO",
                "reason": f"{len(failed)} checklist item(s) failed.",
            }
        if self.config.mode in {"quick-gate", "current-live"}:
            return {
                "decision": "NO-GO",
                "reason": "Quick gate does not execute the full release checklist.",
            }
        if not_executed:
            return {
                "decision": "NO-GO",
                "reason": f"{len(not_executed)} checklist item(s) remain not executed.",
            }
        return {"decision": "GO", "reason": "All checklist items passed in full validation mode."}

    def build_report(self) -> dict[str, Any]:
        recommendation = self._recommendation()
        if recommendation["decision"] == "GO":
            self.mark_pass("final_go_no_go", recommendation["reason"])
        elif self.config.mode in {"quick-gate", "current-live"} and not any(
            item.status == "FAIL" for item in self.checklist.values() if item.item_id != "final_go_no_go"
        ):
            final_gate = self.checklist["final_go_no_go"]
            final_gate.status = "NOT_EXECUTED"
            final_gate.evidence_summary = "Release GO / NO-GO remains reserved for full validation mode."
            final_gate.command = None
            final_gate.error_output = None
            final_gate.affected_module = None
            final_gate.classification = None
            final_gate.production_blocking = False
            final_gate.evidence = []
        else:
            self.mark_fail(
                "final_go_no_go",
                recommendation["reason"],
                command="final-decision",
                error_output=recommendation["reason"],
                affected_module="release-gate",
                classification="confirmed product defect" if any(item.status == "FAIL" for item in self.checklist.values() if item.item_id != "final_go_no_go") else "flaky/unreproduced",
                production_blocking=True,
            )
        follow_ups = list(dict.fromkeys(self.follow_ups))
        for item in self.checklist.values():
            if item.status == "FAIL":
                follow_ups.append(f"{item.title}: {item.evidence_summary}")
            elif item.status == "NOT_EXECUTED" and item.item_id != "final_go_no_go":
                follow_ups.append(f"{item.title}: not executed in {self.config.mode} mode.")
        return {
            "validation_setup": self.validation_setup,
            "findings": [
                {
                    **asdict(finding),
                    "evidence": [asdict(entry) for entry in finding.evidence],
                }
                for finding in self.findings
            ],
            "fixes_applied": [],
            "validation_results": [
                {
                    **asdict(item),
                    "evidence": [asdict(entry) for entry in item.evidence],
                }
                for item in self.checklist.values()
            ],
            "logs_review": self.logs_review,
            "production_recommendation": recommendation,
            "follow_ups": follow_ups,
            "commands": [asdict(command) for command in self.commands],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def write_reports(self) -> tuple[Path, Path]:
        report = self.build_report()
        report_path = self.artifacts_dir / "report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        summary_path = self.artifacts_dir / "summary.md"

        def _line(text: str) -> str:
            return f"{text}\n"

        summary = []
        summary.append(_line("## Validation Setup"))
        summary.append(_line(f"- reset steps performed: {', '.join(self.reset_steps_performed) if self.reset_steps_performed else 'none'}"))
        summary.append(_line(f"- exact org/plants/users/devices used or created: {compact_json({'orgs': self.validation_setup['orgs'], 'users': self.validation_setup['users'], 'plants': self.validation_setup['plants'], 'devices': self.validation_setup['devices']})}"))
        summary.append(_line(f"- environment used: {compact_json(self.validation_setup['environment'])}"))
        summary.append(_line("- credentials source used: environment variables / provided live credentials"))
        summary.append(_line(""))
        summary.append(_line("## Findings"))
        if report["findings"]:
            for finding in report["findings"]:
                summary.append(_line(f"- {finding['title']}: severity={finding['severity']}; affected={finding['affected_module']}/{finding['affected_role']}; classification={finding['classification']}; production-blocking={finding['production_blocking']}; root cause={finding['root_cause']}"))
        else:
            summary.append(_line("- none"))
        summary.append(_line(""))
        summary.append(_line("## Fixes Applied"))
        summary.append(_line("- none"))
        summary.append(_line(""))
        summary.append(_line("## Validation Results"))
        for item in report["validation_results"]:
            summary.append(_line(f"- {item['title']}: {item['status']} | {item['evidence_summary']}"))
        summary.append(_line(""))
        summary.append(_line("## Logs Review"))
        for entry in report["logs_review"]:
            summary.append(_line(f"- {entry['service']}: {entry['status']} | {entry['expected_vs_defect']}"))
        summary.append(_line(""))
        summary.append(_line("## Production Recommendation"))
        summary.append(_line(f"- {report['production_recommendation']['decision']}: {report['production_recommendation']['reason']}"))
        summary.append(_line(""))
        summary.append(_line("## Follow-ups"))
        for follow_up in report["follow_ups"]:
            summary.append(_line(f"- {follow_up}"))
        summary_path.write_text("".join(summary), encoding="utf-8")
        return report_path, summary_path

    def run(self) -> int:
        self.reset_stack_if_requested()
        self.verify_service_health()
        if self.checklist["fresh_reset_sanity"].status != "FAIL":
            self._ensure_primary_bundle()
        if self.checklist["org_plant_setup"].status != "FAIL":
            self.verify_logins_and_device_contracts()
            if not self.abort_for_defect:
                try:
                    self.publish_and_verify_telemetry()
                except Exception as exc:
                    self.mark_fail(
                        "telemetry_influx_contract",
                        "Telemetry contract verification raised an unexpected error.",
                        command="live-telemetry-validation",
                        error_output=str(exc),
                        affected_module="data-service",
                        production_blocking=True,
                    )
            if not self.abort_for_defect:
                self.validate_rules_reporting_and_analytics()
            if not self.abort_for_defect:
                self.run_isolation_and_targeted_suites()
        self.sweep_logs()
        report_path, summary_path = self.write_reports()
        print(f"Preprod validation report: {report_path}")
        print(f"Preprod validation summary: {summary_path}")
        recommendation = self._recommendation()
        if self.config.mode in {"quick-gate", "current-live"}:
            has_runtime_failures = any(
                item.status == "FAIL" for item in self.checklist.values() if item.item_id != "final_go_no_go"
            )
            return 1 if has_runtime_failures else 0
        return 0 if recommendation["decision"] == "GO" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the FactoryOPS pre-production validation harness.")
    parser.add_argument(
        "--mode",
        choices=("quick-gate", "full-validation", "full-reset", "current-live"),
        default="current-live",
        help="quick-gate runs high-risk live checks, full-validation adds the broader regression suites, full-reset performs docker reset first.",
    )
    parser.add_argument(
        "--stop-on-first-defect",
        action="store_true",
        help="Stop the run as soon as a confirmed product defect is recorded.",
    )
    return parser


def make_config(args: argparse.Namespace) -> RunnerConfig:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    mode = args.mode
    reset_stack = mode == "full-reset"
    effective_mode = "full-validation" if mode == "full-reset" else mode
    return RunnerConfig(
        mode=effective_mode if effective_mode != "current-live" else "current-live",
        stop_on_first_defect=bool(args.stop_on_first_defect),
        artifacts_dir=ARTIFACTS_ROOT / timestamp,
        cert_python=certify_release_contracts.resolve_certification_python(),
        auth_url=os.environ.get("AUTH_URL", "http://localhost:8090").rstrip("/"),
        device_url=os.environ.get("DEVICE_URL", "http://localhost:8000").rstrip("/"),
        data_url=os.environ.get("DATA_URL", "http://localhost:8081").rstrip("/"),
        rule_url=os.environ.get("RULE_URL", "http://localhost:8002").rstrip("/"),
        reporting_url=os.environ.get("REPORTING_URL", "http://localhost:8085").rstrip("/"),
        analytics_url=os.environ.get("ANALYTICS_URL", "http://localhost:8003").rstrip("/"),
        waste_url=os.environ.get("WASTE_URL", "http://localhost:8087").rstrip("/"),
        energy_url=os.environ.get("ENERGY_URL", "http://localhost:8010").rstrip("/"),
        ui_url=os.environ.get("UI_WEB_BASE_URL", "http://localhost:3000").rstrip("/"),
        super_admin_email=os.environ.get("BOOTSTRAP_SUPER_ADMIN_EMAIL", "manash.ray@cittagent.com"),
        super_admin_password=os.environ.get("BOOTSTRAP_SUPER_ADMIN_PASSWORD", "Shivex@2706!"),
        super_admin_full_name=os.environ.get("BOOTSTRAP_SUPER_ADMIN_FULL_NAME", "Shivex Super-Admin"),
        live_org_admin_email=os.environ.get("ORG_ADMIN_EMAIL"),
        live_org_admin_password=os.environ.get("ORG_ADMIN_PASSWORD"),
        seed_password=os.environ.get("CERTIFY_SEED_PASSWORD", DEFAULT_PASSWORD),
        http_timeout=float(os.environ.get("PREPROD_HTTP_TIMEOUT", str(DEFAULT_TIMEOUT))),
        reset_stack=reset_stack,
    )


def main() -> int:
    args = build_parser().parse_args()
    config = make_config(args)
    runner = PreprodValidationRunner(config)
    try:
        return runner.run()
    finally:
        runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
