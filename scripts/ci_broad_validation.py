#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_MD_PATH = REPO_ROOT / "implementation-docs" / "CI.md"
ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "broad-ci-validation"

DEFAULT_ENV = {
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "JWT_SECRET_KEY": "broad-ci-validation-secret-key-32chars",
    "INTERNAL_SERVICE_SHARED_SECRET": "broad-ci-internal-secret",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "INFLUXDB_URL": "http://127.0.0.1:8086",
    "INFLUXDB_TOKEN": "broad-ci-token",
    "INFLUXDB_ORG": "broad-ci-org",
    "INFLUXDB_BUCKET": "telemetry",
    "MQTT_BROKER_HOST": "mqtt.test.local",
    "MQTT_BROKER_PORT": "1883",
    "MYSQL_HOST": "127.0.0.1",
    "MYSQL_PORT": "3306",
    "MYSQL_DATABASE": "ai_factoryops",
    "MYSQL_USER": "energy",
    "MYSQL_PASSWORD": "energy",
}

SUITE_ROW_RE = re.compile(
    r"^\| (?P<name>.+?) \| (?P<items>\d+) \| (?P<covered>\d+) \| (?P<partial>\d+) \| (?P<missing>\d+) \| (?P<status>.+?) \|$"
)


@dataclass(frozen=True)
class SuiteCommand:
    label: str
    argv: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SuiteDefinition:
    key: str
    name: str
    category: str
    commands: tuple[SuiteCommand, ...]


@dataclass
class CommandResult:
    label: str
    command: str
    cwd: str
    status: str
    returncode: int
    duration_seconds: float
    stdout_path: str
    stderr_path: str


def tail_text(path: Path, *, max_lines: int = 80) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(["..."] + lines[-max_lines:])


def resolve_validation_python() -> str:
    validation_venv_dir = os.environ.get("VALIDATION_VENV_DIR")
    candidates = [
        os.environ.get("CERTIFY_PYTHON"),
        str(Path(validation_venv_dir) / "bin" / "python") if validation_venv_dir else None,
        str(REPO_ROOT / ".validation-venv" / "bin" / "python"),
        str(REPO_ROOT / ".venv-phase1" / "bin" / "python"),
        str(REPO_ROOT / ".venv" / "bin" / "python"),
        shutil.which("python3"),
        shutil.which("python"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("Unable to resolve a Python interpreter for broad CI validation.")


PYTHON_BIN = resolve_validation_python()


def python_pytest(*targets: str) -> tuple[str, ...]:
    return (PYTHON_BIN, "-m", "pytest", *targets, "-q")


def npm_cmd(*parts: str) -> tuple[str, ...]:
    return ("npm", *parts)


def suite_definitions() -> list[SuiteDefinition]:
    return [
        SuiteDefinition(
            key="auth-and-identity",
            name="Auth And Identity",
            category="python",
            commands=(
                SuiteCommand(
                    label="auth identity regressions",
                    argv=python_pytest(
                        "tests/test_invite_and_reset_lifecycle.py",
                        "tests/test_token_version_revocation.py",
                        "tests/test_auth_cookie_security.py",
                    ),
                    cwd="services/auth-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="org-plant-and-user-management",
            name="Org Plant And User Management",
            category="python",
            commands=(
                SuiteCommand(
                    label="org user and plant management regressions",
                    argv=python_pytest(
                        "tests/test_org_user_scope.py",
                        "tests/test_org_plant_lifecycle.py",
                    ),
                    cwd="services/auth-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="premium-feature-gating",
            name="Premium Feature Gating",
            category="browser",
            commands=(
                SuiteCommand(
                    label="analytics feature gate api",
                    argv=python_pytest("tests/integration/test_feature_gate_api.py"),
                    cwd="services/analytics-service",
                ),
                SuiteCommand(
                    label="reporting feature gate api",
                    argv=python_pytest("tests/test_feature_gate_api.py"),
                    cwd="services/reporting-service",
                ),
                SuiteCommand(
                    label="waste feature gate api",
                    argv=python_pytest("tests/test_feature_gate_api.py"),
                    cwd="services/waste-analysis-service",
                ),
                SuiteCommand(
                    label="copilot feature gate api",
                    argv=python_pytest("tests/test_feature_gate_api.py"),
                    cwd="services/copilot-service",
                ),
                SuiteCommand(
                    label="premium feature gating ui",
                    argv=npm_cmd("run", "test:e2e", "--", "premium-feature-gating.spec.js"),
                    cwd="ui-web",
                    env={"CI": "1"},
                ),
                SuiteCommand(
                    label="machine detail kpi truthfulness unit regressions",
                    argv=("npm", "exec", "--", "tsx", "--test", "tests/unit/machineDetailKpiState.test.ts"),
                    cwd="ui-web",
                    env={"CI": "1"},
                ),
                SuiteCommand(
                    label="machine detail bootstrap recovery ui regressions",
                    argv=npm_cmd("run", "test:e2e", "--", "tests/e2e/machine-dashboard-bootstrap-recovery.spec.js"),
                    cwd="ui-web",
                    env={"CI": "1"},
                ),
            ),
        ),
        SuiteDefinition(
            key="org-suspension-and-access-enforcement",
            name="Org Suspension And Access Enforcement",
            category="python",
            commands=(
                SuiteCommand(
                    label="org suspension enforcement regressions",
                    argv=python_pytest(
                        "tests/test_org_plant_lifecycle.py",
                        "tests/test_org_user_scope.py",
                    ),
                    cwd="services/auth-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="device-onboarding-and-provisioning",
            name="Device Onboarding And Provisioning",
            category="python",
            commands=(
                SuiteCommand(
                    label="device onboarding and provisioning regressions",
                    argv=python_pytest(
                        "tests/test_device_onboarding_phase2.py",
                        "tests/test_plant_lifecycle_guards.py",
                        "tests/test_device_id_generation.py",
                    ),
                    cwd="services/device-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="mqtt-auth-and-telemetry-ingestion",
            name="MQTT Auth And Telemetry Ingestion",
            category="python",
            commands=(
                SuiteCommand(
                    label="mqtt device credential regressions",
                    argv=python_pytest("tests/test_device_mqtt_credentials.py"),
                    cwd="services/device-service",
                ),
                SuiteCommand(
                    label="telemetry ingestion regressions",
                    argv=python_pytest(
                        "tests/test_telemetry_phase2.py",
                        "tests/test_mqtt_handler.py",
                        "tests/test_backpressure.py",
                    ),
                    cwd="services/data-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="machine-runtime-states",
            name="Machine Runtime States",
            category="python",
            commands=(
                SuiteCommand(
                    label="machine runtime state regressions",
                    argv=python_pytest(
                        "tests/test_live_projection_service.py",
                        "tests/test_load_state_consistency.py",
                        "tests/test_live_update_unknown_device.py",
                        "tests/test_startup_reconcile.py",
                    ),
                    cwd="services/device-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="machine-dashboard-calculations",
            name="Machine Dashboard Calculations",
            category="python",
            commands=(
                SuiteCommand(
                    label="machine dashboard calculation regressions",
                    argv=python_pytest(
                        "tests/test_live_dashboard_summary.py",
                        "tests/test_device_loss_stats.py",
                        "tests/test_snapshot_storage.py",
                    ),
                    cwd="services/device-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="parameter-config-and-health-score",
            name="Parameter Config And Health Score",
            category="python",
            commands=(
                SuiteCommand(
                    label="parameter config and health score regressions",
                    argv=python_pytest(
                        "tests/test_phase3_machine_api_validation.py",
                        "tests/test_health_config_uniqueness.py",
                        "tests/test_dashboard_health_scope.py",
                        "tests/test_health_trend_parameter_resolution.py",
                    ),
                    cwd="services/device-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="shift-and-uptime",
            name="Shift And Uptime",
            category="python",
            commands=(
                SuiteCommand(
                    label="shift and uptime regressions",
                    argv=python_pytest(
                        "tests/test_phase3_shift_dashboard_calendar.py",
                        "tests/test_dashboard_bootstrap_latency_guard.py",
                    ),
                    cwd="services/device-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="maintenance-records",
            name="Maintenance Records",
            category="python",
            commands=(
                SuiteCommand(
                    label="maintenance record regressions",
                    argv=python_pytest(
                        "tests/test_maintenance_log_api.py",
                        "tests/test_phase3_machine_api_validation.py",
                    ),
                    cwd="services/device-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="calendar-and-consumption-consistency",
            name="Calendar And Consumption Consistency",
            category="python",
            commands=(
                SuiteCommand(
                    label="calendar and consumption regressions",
                    argv=python_pytest(
                        "tests/test_phase3_shift_dashboard_calendar.py",
                        "tests/test_snapshot_storage.py",
                    ),
                    cwd="services/device-service",
                ),
                SuiteCommand(
                    label="energy-service current-day overlay parity regressions",
                    argv=python_pytest("tests/test_device_range_live_overlay.py"),
                    cwd="services/energy-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="analytics-job-flow",
            name="Analytics Job Flow",
            category="python",
            commands=(
                SuiteCommand(
                    label="analytics job flow regressions",
                    argv=python_pytest(
                        "tests/integration/test_phase4_truthfulness_scope.py",
                        "tests/unit/test_job_runner.py",
                        "tests/unit/test_job_status_route_payload.py",
                        "tests/unit/test_result_scope.py",
                        "tests/unit/test_main_startup_metrics.py",
                        "tests/unit/test_worker_heartbeat.py",
                        "tests/unit/test_worker_restart_cleanup.py",
                    ),
                    cwd="services/analytics-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="reports-and-scheduled-reports",
            name="Reports And Scheduled Reports",
            category="python",
            commands=(
                SuiteCommand(
                    label="reports and scheduler regressions",
                    argv=python_pytest(
                        "tests/test_phase4_truthfulness_scope.py",
                        "tests/test_long_running_job_contract.py",
                        "tests/test_report_history_scope.py",
                        "tests/test_report_device_scope.py",
                        "tests/test_scheduler_reliability.py",
                        "tests/test_report_task_tariff_warning.py",
                    ),
                    cwd="services/reporting-service",
                    env={"INTERNAL_SERVICE_SHARED_SECRET": "test-internal-secret"},
                ),
            ),
        ),
        SuiteDefinition(
            key="waste-analysis",
            name="Waste Analysis",
            category="python",
            commands=(
                SuiteCommand(
                    label="waste analysis regressions",
                    argv=python_pytest(
                        "tests/test_phase4_truthfulness_scope.py",
                        "tests/test_long_running_job_contract.py",
                        "tests/test_migration_guard_invocation.py",
                        "tests/test_waste_worker_queue_migration_api.py",
                        "tests/test_waste_historical_loss_parity.py",
                    ),
                    cwd="services/waste-analysis-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="factory-copilot",
            name="Factory Copilot",
            category="python",
            commands=(
                SuiteCommand(
                    label="factory copilot regressions",
                    argv=python_pytest(
                        "tests/test_phase4_chat_gate.py",
                        "tests/test_chat_provider_optional.py",
                    ),
                    cwd="services/copilot-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="rules-and-notifications",
            name="Rules And Notifications",
            category="python",
            commands=(
                SuiteCommand(
                    label="rules and notifications regressions",
                    argv=python_pytest(
                        "tests/test_phase5_rules_notifications.py",
                        "tests/test_rule_plant_scope.py",
                        "tests/test_notification_audit_ledger.py",
                    ),
                    cwd="services/rule-engine-service",
                    env={"INTERNAL_SERVICE_SHARED_SECRET": "test-internal-secret"},
                ),
            ),
        ),
        SuiteDefinition(
            key="notification-usage-and-delivery-accounting",
            name="Notification Usage And Delivery Accounting",
            category="python",
            commands=(
                SuiteCommand(
                    label="notification usage accounting regressions",
                    argv=python_pytest(
                        "tests/test_admin_notification_usage_api.py",
                        "tests/test_notification_audit_ledger.py",
                    ),
                    cwd="services/rule-engine-service",
                    env={"INTERNAL_SERVICE_SHARED_SECRET": "test-internal-secret"},
                ),
            ),
        ),
        SuiteDefinition(
            key="tariff-and-tenant-isolation",
            name="Tariff And Tenant Isolation",
            category="python",
            commands=(
                SuiteCommand(
                    label="reporting tariff validation regressions",
                    argv=python_pytest(
                        "tests/test_phase5_tariff_validation.py",
                        "tests/test_revision_and_tariff_foundation.py",
                    ),
                    cwd="services/reporting-service",
                ),
                SuiteCommand(
                    label="cross-service tariff and tenant isolation regressions",
                    argv=python_pytest(
                        "tests/test_reporting_tariff_resolver.py",
                        "tests/test_reporting_settings_tenant_isolation.py",
                        "tests/test_energy_service_cost_alignment.py",
                        "tests/api/test_current_day_truth_parity.py",
                    ),
                ),
            ),
        ),
        SuiteDefinition(
            key="platform-maintenance",
            name="Platform Maintenance",
            category="python",
            commands=(
                SuiteCommand(
                    label="platform maintenance regressions",
                    argv=python_pytest(
                        "tests/test_platform_maintenance_phase5.py",
                        "tests/test_platform_maintenance.py",
                        "tests/test_platform_maintenance_status.py",
                        "tests/test_platform_maintenance_delivery.py",
                    ),
                    cwd="services/auth-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="runtime-stability-and-recovery",
            name="Runtime Stability And Recovery",
            category="python",
            commands=(
                SuiteCommand(
                    label="auth runtime truthfulness regressions",
                    argv=python_pytest(
                        "tests/test_phase6_runtime_truthfulness.py",
                        "tests/test_token_cleanup_service.py",
                        "tests/test_tenant_identity_hard_cut.py",
                    ),
                    cwd="services/auth-service",
                ),
                SuiteCommand(
                    label="data runtime truthfulness regressions",
                    argv=python_pytest(
                        "tests/test_phase6_runtime_truthfulness.py",
                        "tests/test_mqtt_handler.py",
                        "tests/test_circuit_breaker.py",
                    ),
                    cwd="services/data-service",
                ),
            ),
        ),
        SuiteDefinition(
            key="database-integrity-and-concurrency",
            name="Database Integrity And Concurrency",
            category="stateful",
            commands=(
                SuiteCommand(
                    label="data-service outbox integrity regressions",
                    argv=python_pytest(
                        "tests/test_outbox_integrity.py",
                    ),
                    cwd="services/data-service",
                ),
                SuiteCommand(
                    label="device optimistic lock regressions",
                    argv=python_pytest("tests/test_optimistic_lock.py"),
                    cwd="services/device-service",
                ),
                SuiteCommand(
                    label="waste migration guard bootstrap regressions",
                    argv=python_pytest("tests/test_migration_guard_bootstrap.py"),
                    cwd="services/waste-analysis-service",
                    env={"MYSQL_ROOT_PASSWORD": "root"},
                ),
            ),
        ),
    ]


SUITES = suite_definitions()
SUITES_BY_NAME = {suite.name: suite for suite in SUITES}


def parse_ci_suite_names(ci_md_path: Path = CI_MD_PATH) -> list[str]:
    suite_names: list[str] = []
    for line in ci_md_path.read_text(encoding="utf-8").splitlines():
        match = SUITE_ROW_RE.match(line.strip())
        if not match:
            continue
        suite_names.append(match.group("name"))
    if len(suite_names) != 22:
        raise RuntimeError(f"Expected 22 suite rows in {ci_md_path}, found {len(suite_names)}.")
    return suite_names


def validate_against_ci_md() -> None:
    ci_names = parse_ci_suite_names()
    manifest_names = [suite.name for suite in SUITES]
    if ci_names != manifest_names:
        raise RuntimeError(
            "Broad CI suite manifest no longer matches CI.md.\n"
            f"CI.md: {ci_names}\n"
            f"Manifest: {manifest_names}"
        )


def suites_for_category(category: str) -> list[SuiteDefinition]:
    return [suite for suite in SUITES if suite.category == category]


def suite_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "suite"


def command_env(overrides: dict[str, str], *, cwd: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in DEFAULT_ENV.items():
        env.setdefault(key, value)
    pythonpath_parts = [str(cwd), str(REPO_ROOT / "services"), str(REPO_ROOT)]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env.update(overrides)
    return env


def run_command(
    suite: SuiteDefinition,
    command: SuiteCommand,
    *,
    artifacts_dir: Path,
) -> CommandResult:
    suite_dir = artifacts_dir / suite_slug(suite.name)
    commands_dir = suite_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    index = len(list(commands_dir.glob("*.stdout.log"))) + 1
    stdout_path = commands_dir / f"{index:02d}-{suite_slug(command.label)}.stdout.log"
    stderr_path = commands_dir / f"{index:02d}-{suite_slug(command.label)}.stderr.log"
    cwd = REPO_ROOT / command.cwd if command.cwd else REPO_ROOT
    argv = list(command.argv)
    env = command_env(command.env, cwd=cwd)
    started = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            check=False,
        )
    duration = time.monotonic() - started
    return CommandResult(
        label=command.label,
        command=" ".join(shlex.quote(part) for part in argv),
        cwd=str(cwd),
        status="PASS" if completed.returncode == 0 else "FAIL",
        returncode=completed.returncode,
        duration_seconds=duration,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )


def write_report(
    suites: list[SuiteDefinition],
    suite_results: list[dict[str, Any]],
    artifacts_dir: Path,
) -> tuple[Path, Path]:
    summary_lines = [
        "# Broad CI Validation",
        "",
        f"- generated_at: {datetime.now(timezone.utc).isoformat()}",
        f"- selected_suites: {len(suites)}",
        f"- result: {'PASS' if all(item['status'] == 'PASS' for item in suite_results) else 'FAIL'}",
        "",
        "## Suite Results",
    ]
    for item in suite_results:
        summary_lines.append(f"- {item['name']}: {item['status']} ({item['duration_seconds']:.2f}s)")
    report = {
        "mode": "broad-ci",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result": "PASS" if all(item["status"] == "PASS" for item in suite_results) else "FAIL",
        "suites": suite_results,
    }
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_dir / "report.json"
    summary_path = artifacts_dir / "summary.md"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    return report_path, summary_path


def run_selected_suites(selected: list[SuiteDefinition], artifacts_dir: Path) -> int:
    suite_results: list[dict[str, Any]] = []
    for suite in selected:
        started = time.monotonic()
        command_results = [asdict(run_command(suite, command, artifacts_dir=artifacts_dir)) for command in suite.commands]
        status = "PASS" if all(result["status"] == "PASS" for result in command_results) else "FAIL"
        suite_results.append(
            {
                "key": suite.key,
                "name": suite.name,
                "category": suite.category,
                "status": status,
                "duration_seconds": time.monotonic() - started,
                "commands": command_results,
            }
        )
        if status == "FAIL":
            for result in command_results:
                if result["status"] == "PASS":
                    continue
                stderr_tail = tail_text(Path(result["stderr_path"]))
                stdout_tail = tail_text(Path(result["stdout_path"]))
                print(
                    "\n".join(
                        [
                            f"[broad-ci] Suite '{suite.name}' command '{result['label']}' failed.",
                            f"[broad-ci] cwd: {result['cwd']}",
                            f"[broad-ci] command: {result['command']}",
                            f"[broad-ci] stderr log: {result['stderr_path']}",
                            f"[broad-ci] stdout log: {result['stdout_path']}",
                            "[broad-ci] stderr tail:",
                            stderr_tail or "(empty)",
                            "[broad-ci] stdout tail:",
                            stdout_tail or "(empty)",
                        ]
                    ),
                    file=sys.stderr,
                )
    write_report(selected, suite_results, artifacts_dir)
    return 0 if all(item["status"] == "PASS" for item in suite_results) else 1


def matrix_payload(category: str) -> str:
    suites = suites_for_category(category)
    payload = [
        {
            "key": suite.key,
            "name": suite.name,
            "slug": suite_slug(suite.name),
            "category": suite.category,
        }
        for suite in suites
    ]
    return json.dumps(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Broad CI suite runner aligned to CI.md.")
    parser.add_argument("--suite", action="append", default=[], help="Run a specific suite by exact CI.md name.")
    parser.add_argument("--artifacts-dir", help="Explicit artifacts directory for the run.")
    parser.add_argument("--list-suites", action="store_true", help="Print suite names and exit.")
    parser.add_argument(
        "--matrix-category",
        choices=("python", "browser", "stateful"),
        help="Emit GitHub Actions matrix JSON for one category and exit.",
    )
    return parser.parse_args()


def main() -> int:
    validate_against_ci_md()
    args = parse_args()

    if args.list_suites:
        for suite in SUITES:
            print(f"{suite.category}\t{suite.name}")
        return 0

    if args.matrix_category:
        print(matrix_payload(args.matrix_category))
        return 0

    selected_names = args.suite or [suite.name for suite in SUITES]
    selected: list[SuiteDefinition] = []
    for name in selected_names:
        suite = SUITES_BY_NAME.get(name)
        if suite is None:
            raise RuntimeError(f"Unknown suite '{name}'. Use --list-suites to inspect valid names.")
        selected.append(suite)

    artifacts_dir = (
        Path(args.artifacts_dir)
        if args.artifacts_dir
        else ARTIFACTS_ROOT / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    )
    if not artifacts_dir.is_absolute():
        artifacts_dir = REPO_ROOT / artifacts_dir

    return run_selected_suites(selected, artifacts_dir)


if __name__ == "__main__":
    raise SystemExit(main())
