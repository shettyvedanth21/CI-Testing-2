#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import preprod_validation


ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "ci-validation"

INCLUDED_SUITES = [
    "Entitlement gating regressions",
    "Platform maintenance regressions",
    "Simulatorctl startup regressions",
    "Deploy recovery regressions",
    "Machine activity-history resilience regressions",
    "Machine dashboard bootstrap latency guard regressions",
    "Machine detail bootstrap frontend contract regressions",
    "Analytics long-running truthfulness regressions",
    "Targeted financial consistency regressions",
    "Notification and settings regressions",
    "Scheduled report reliability regressions",
]

EXCLUDED_AREAS = [
    "Live service health checks and Docker stack bootstrapping",
    "Org isolation live-stack verification",
    "Preprod Playwright smoke against a running UI stack",
    "Telemetry publish / Influx / rule-trigger live validation",
    "Analytics live job execution against running services",
    "Docker log sweeps",
    "Full-certification-only hardware lifecycle/integrity slices",
]


def make_config() -> preprod_validation.RunnerConfig:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    cert_python = os.environ.get("CERTIFY_PYTHON") or preprod_validation.certify_release_contracts.resolve_certification_python()
    return preprod_validation.RunnerConfig(
        mode="ci-targeted",
        stop_on_first_defect=False,
        artifacts_dir=ARTIFACTS_ROOT / timestamp,
        cert_python=cert_python,
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
        super_admin_password=os.environ.get("BOOTSTRAP_SUPER_ADMIN_PASSWORD", "Shivex@2706"),
        super_admin_full_name=os.environ.get("BOOTSTRAP_SUPER_ADMIN_FULL_NAME", "Shivex Super-Admin"),
        live_org_admin_email=os.environ.get("ORG_ADMIN_EMAIL"),
        live_org_admin_password=os.environ.get("ORG_ADMIN_PASSWORD"),
        seed_password=os.environ.get("CERTIFY_SEED_PASSWORD", preprod_validation.DEFAULT_PASSWORD),
        http_timeout=float(os.environ.get("PREPROD_HTTP_TIMEOUT", str(preprod_validation.DEFAULT_TIMEOUT))),
        reset_stack=False,
    )


def build_ci_env() -> dict[str, str]:
    env = os.environ.copy()
    defaults = {
        "DATABASE_URL": "mysql+aiomysql://test:test@127.0.0.1:3306/test_db",
        "REDIS_URL": "redis://localhost:6379/0",
        "JWT_SECRET_KEY": "ci-targeted-validation-secret-key-32chars",
        "INTERNAL_SERVICE_SHARED_SECRET": "ci-internal-shared-secret",
        "INFLUXDB_URL": "http://localhost:8086",
        "INFLUXDB_TOKEN": "ci-token",
        "INFLUXDB_ORG": "ci-org",
        "INFLUXDB_BUCKET": "telemetry",
        "DEVICE_SERVICE_URL": "http://localhost:8000",
        "ENERGY_SERVICE_URL": "http://localhost:8010",
    }
    for key, value in defaults.items():
        env.setdefault(key, value)
    return env


def write_report(
    artifacts_dir: Path,
    commands: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> tuple[Path, Path]:
    report = {
        "mode": "ci-targeted",
        "included_suites": INCLUDED_SUITES,
        "excluded_areas": EXCLUDED_AREAS,
        "commands": commands,
        "result": "PASS" if not failures else "FAIL",
        "failures": failures,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# CI Targeted Validation",
        "",
        f"- result: {report['result']}",
        f"- included suites: {len(INCLUDED_SUITES)}",
        f"- failed suites: {len(failures)}",
        "",
        "## Included",
    ]
    lines.extend(f"- {suite}" for suite in INCLUDED_SUITES)
    lines.extend(["", "## Intentionally Excluded"])
    lines.extend(f"- {area}" for area in EXCLUDED_AREAS)
    lines.extend(["", "## Suite Results"])
    lines.extend(
        f"- {command['name']}: {command['status']} ({command['duration_seconds']:.2f}s)"
        for command in commands
    )
    lines.extend(["", "## Failures"])
    if failures:
        lines.extend(
            f"- {failure['name']}: classification={failure['classification']}; command={failure['command']}"
            for failure in failures
        )
    else:
        lines.append("- none")
    summary_path = artifacts_dir / "summary.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path, summary_path


def main() -> int:
    config = make_config()
    runner = preprod_validation.PreprodValidationRunner(config)
    try:
        env = build_ci_env()
        commands = []
        failures = []
        for name, command, item_ids, command_env, command_cwd in preprod_validation.build_targeted_validation_commands(
            config,
            env,
            include_full_validation_extras=False,
        ):
            result = runner.run_command(name, command, env=command_env, cwd=command_cwd)
            commands.append(
                {
                    "name": name,
                    "status": result.status,
                    "command": result.command,
                    "duration_seconds": result.duration_seconds,
                    "stdout_path": result.stdout_path,
                    "stderr_path": result.stderr_path,
                    "checklist_items": list(item_ids),
                }
            )
            if result.status != "PASS":
                stdout = Path(result.stdout_path).read_text(encoding="utf-8")
                stderr = Path(result.stderr_path).read_text(encoding="utf-8")
                failures.append(
                    {
                        "name": name,
                        "command": result.command,
                        "returncode": result.returncode,
                        "classification": runner._classify_failure(result.command, f"{stdout}\n{stderr}"),
                        "stdout_path": result.stdout_path,
                        "stderr_path": result.stderr_path,
                        "checklist_items": list(item_ids),
                    }
                )

        report_path, summary_path = write_report(config.artifacts_dir, commands, failures)
        print(f"CI targeted validation report: {report_path}")
        print(f"CI targeted validation summary: {summary_path}")
        return 0 if not failures else 1
    finally:
        runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
