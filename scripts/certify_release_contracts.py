#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = REPO_ROOT / "ui-web"
REPORTS_DIR = REPO_ROOT / "artifacts" / "certification"
DEFAULT_LIVE_STACK_AUTH_URL = os.environ.get("CERTIFY_STACK_AUTH_URL", "http://localhost:8090")
DEFAULT_LIVE_STACK_EMAIL = os.environ.get(
    "CERTIFY_STACK_EMAIL",
    os.environ.get("BOOTSTRAP_SUPER_ADMIN_EMAIL", "manash.ray@cittagent.com"),
)
DEFAULT_LIVE_STACK_PASSWORD = os.environ.get(
    "CERTIFY_STACK_PASSWORD",
    os.environ.get("BOOTSTRAP_SUPER_ADMIN_PASSWORD", "Shivex@2706!"),
)
REQUIRED_TEST_MODULES = ("pytest", "fastapi", "pydantic", "pytest_asyncio")
MAX_OUTPUT_CHARS = 16000
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
CROSS_SERVICE_E2E_TIMEOUT_SECONDS = 900
LIVE_BROWSER_TIMEOUT_SECONDS = 600


def _truncate_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return output[-MAX_OUTPUT_CHARS:]


@dataclass
class StepResult:
    name: str
    status: str
    command: str
    duration_seconds: float
    detail: str
    stdout: str = ""
    stderr: str = ""


@dataclass
class CertificationReport:
    started_at_epoch: float
    mode: str
    results: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.status == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == "FAIL")

    @property
    def blocked(self) -> int:
        return sum(1 for result in self.results if result.status == "BLOCKED")


def append_result(
    report: CertificationReport,
    *,
    name: str,
    status: str,
    command: str,
    detail: str,
    duration_seconds: float = 0.0,
    stdout: str = "",
    stderr: str = "",
) -> None:
    report.results.append(
        StepResult(
            name=name,
            status=status,
            command=command,
            duration_seconds=duration_seconds,
            detail=detail,
            stdout=stdout,
            stderr=stderr,
        )
    )


def shell_join(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def write_report(report: CertificationReport) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_path = REPORTS_DIR / f"release-certification-{timestamp}.json"
    payload = {
      "started_at_epoch": report.started_at_epoch,
      "mode": report.mode,
      "summary": {
        "passed": report.passed,
        "failed": report.failed,
        "blocked": report.blocked,
      },
      "results": [asdict(result) for result in report.results],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def print_summary(report: CertificationReport, output_path: Path) -> None:
    print("")
    print("======================================")
    print(" Release Certification Summary")
    print("======================================")
    print(f" Passed : {report.passed}")
    print(f" Failed : {report.failed}")
    print(f" Blocked: {report.blocked}")
    print(f" Report : {output_path}")
    print("")


def finish(report: CertificationReport, exit_code: int) -> int:
    output_path = write_report(report)
    print_summary(report, output_path)
    return exit_code


def run_command(
    report: CertificationReport,
    name: str,
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    allow_blocked: bool = False,
    blocked_reason: str | None = None,
) -> None:
    command_text = shell_join(command)
    if blocked_reason:
        append_result(
            report,
            name=name,
            status="BLOCKED" if allow_blocked else "FAIL",
            command=command_text,
            duration_seconds=0.0,
            detail=blocked_reason,
        )
        return

    started = time.time()
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd or REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = _truncate_output(exc.stdout)
        stderr = _truncate_output(exc.stderr)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            extra_stdout, extra_stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            extra_stdout, extra_stderr = process.communicate()
        stdout = _truncate_output(stdout + (extra_stdout or ""))
        stderr = _truncate_output(stderr + (extra_stderr or ""))
    duration = time.time() - started
    if timed_out:
        status = "FAIL"
        detail = f"timeout after {timeout_seconds}s"
    else:
        status = "PASS" if process.returncode == 0 else "FAIL"
        detail = f"exit_code={process.returncode}"
    append_result(
        report,
        name=name,
        status=status,
        command=command_text,
        duration_seconds=duration,
        detail=detail,
        stdout=_truncate_output(stdout),
        stderr=_truncate_output(stderr),
    )


def has_live_browser_env(*, require_org: bool = True, env: dict[str, str] | None = None) -> tuple[bool, str | None]:
    source = env or os.environ
    required = [
        "CERTIFY_SUPER_ADMIN_EMAIL",
        "CERTIFY_SUPER_ADMIN_PASSWORD",
    ]
    if require_org:
        required.append("CERTIFY_TENANT_ID")
    missing = [key for key in required if not source.get(key)]
    if missing:
        return False, f"Missing live-browser env vars: {', '.join(missing)}"
    return True, None


def has_isolation_env(env: dict[str, str] | None = None) -> tuple[bool, str | None]:
    source = env or os.environ
    required = [
        "VALIDATE_SUPER_ADMIN_EMAIL",
        "VALIDATE_SUPER_ADMIN_PASSWORD",
    ]
    missing = [key for key in required if not source.get(key)]
    if missing:
        return False, f"Missing isolation-validator env vars: {', '.join(missing)}"
    return True, None


def has_live_stack_test_account() -> tuple[bool, str | None]:
    login_url = f"{DEFAULT_LIVE_STACK_AUTH_URL.rstrip('/')}/api/v1/auth/login"
    payload = json.dumps(
        {"email": DEFAULT_LIVE_STACK_EMAIL, "password": DEFAULT_LIVE_STACK_PASSWORD}
    ).encode("utf-8")
    req = request.Request(
        login_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                return (
                    False,
                    f"Live-stack auth prerequisite failed: {login_url} returned HTTP {response.status}",
                )
    except error.HTTPError as exc:
        return (
            False,
            "Live-stack auth prerequisite failed: "
            f"{login_url} rejected the certification account with HTTP {exc.code}",
        )
    except Exception as exc:
        return False, f"Live-stack auth prerequisite failed: could not reach {login_url} ({exc})"
    return True, None


def wait_for_http_200(url: str, *, timeout_seconds: int = 90, interval_seconds: int = 3) -> tuple[bool, str | None]:
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    while time.time() < deadline:
        try:
            with request.urlopen(url, timeout=10) as response:
                if response.status == 200:
                    return True, None
                last_error = f"HTTP {response.status}"
        except Exception as exc:  # pragma: no cover - depends on local stack state
            last_error = str(exc)
        time.sleep(interval_seconds)
    return False, f"{url} did not become healthy within {timeout_seconds}s ({last_error})"


def probe_http_200(url: str, *, timeout_seconds: int = 5) -> tuple[bool, str | None]:
    try:
        with request.urlopen(url, timeout=timeout_seconds) as response:
            if response.status == 200:
                return True, None
            return False, f"HTTP {response.status}"
    except Exception as exc:  # pragma: no cover - depends on local stack state
        return False, str(exc)


def certification_health_checks(ui_base: str) -> list[tuple[str, str]]:
    return [
        ("UI login page readiness", f"{ui_base}/login"),
        ("Device service readiness", "http://localhost:8000/health"),
        ("Analytics service readiness", "http://localhost:8003/health/live"),
        ("Reporting service readiness", "http://localhost:8085/health"),
        ("Waste service readiness", "http://localhost:8087/health"),
    ]


def run_command_capture(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def resolve_playwright_command() -> list[str]:
    local_runner = UI_ROOT / "node_modules" / ".bin" / "playwright"
    if local_runner.exists():
        return [str(local_runner)]
    return ["npx", "playwright"]


def _python_supports_certification_dependencies(python_executable: str) -> bool:
    probe = subprocess.run(
        [
            python_executable,
            "-c",
            "import pytest, fastapi, pydantic, pytest_asyncio",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def resolve_certification_python() -> str:
    configured = os.environ.get("CERTIFY_PYTHON")
    candidates: list[str] = []
    if configured:
        candidates.append(configured)

    validation_venv = REPO_ROOT / ".validation-venv" / "bin" / "python"
    if validation_venv.exists():
        candidates.append(str(validation_venv))

    repo_venv = REPO_ROOT / ".venv" / "bin" / "python"
    if repo_venv.exists():
        candidates.append(str(repo_venv))

    python311 = shutil.which("python3.11")
    if python311:
        candidates.append(python311)

    shell_python = shutil.which("python3")
    if shell_python:
        candidates.append(shell_python)

    pyenv_root = Path.home() / ".pyenv" / "versions"
    if pyenv_root.exists():
        for candidate in sorted(pyenv_root.glob("*/bin/python3")):
            candidates.append(str(candidate))

    candidates.append(sys.executable)

    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if _python_supports_certification_dependencies(normalized):
            return normalized

    return sys.executable


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


def prepare_certification_env(seed_payload: dict[str, object] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CERTIFY_STACK_AUTH_URL", DEFAULT_LIVE_STACK_AUTH_URL)
    env.setdefault("CERTIFY_STACK_EMAIL", DEFAULT_LIVE_STACK_EMAIL)
    env.setdefault("CERTIFY_STACK_PASSWORD", DEFAULT_LIVE_STACK_PASSWORD)
    env.setdefault("VALIDATE_SUPER_ADMIN_EMAIL", env.get("CERTIFY_STACK_EMAIL", DEFAULT_LIVE_STACK_EMAIL))
    env.setdefault("VALIDATE_SUPER_ADMIN_PASSWORD", env.get("CERTIFY_STACK_PASSWORD", DEFAULT_LIVE_STACK_PASSWORD))
    env.setdefault("CERTIFY_SUPER_ADMIN_EMAIL", env.get("CERTIFY_STACK_EMAIL", DEFAULT_LIVE_STACK_EMAIL))
    env.setdefault("CERTIFY_SUPER_ADMIN_PASSWORD", env.get("CERTIFY_STACK_PASSWORD", DEFAULT_LIVE_STACK_PASSWORD))
    _load_local_env_into(env)
    _internal_secret = env.get("INTERNAL_SERVICE_SHARED_SECRET")
    if _internal_secret:
        env.setdefault("INTERNAL_SERVICE_SHARED_SECRET", _internal_secret)

    if seed_payload:
        strict_env = seed_payload.get("strict_env")
        if isinstance(strict_env, dict):
            for key, value in strict_env.items():
                if value is not None:
                    env[str(key)] = str(value)
        env["VALIDATE_CERTIFICATION_SEED_JSON"] = json.dumps(seed_payload)

    return env


def seed_certification_orgs(
    report: CertificationReport,
    *,
    certification_python: str,
    env: dict[str, str],
    name: str = "Certification org seeding",
) -> tuple[dict[str, str], dict[str, object] | None]:
    seed_command = [certification_python, "scripts/ensure_certification_orgs.py"]
    started = time.time()
    try:
        completed = run_command_capture(seed_command, env=env, timeout_seconds=180)
    except subprocess.TimeoutExpired as exc:
        duration = time.time() - started
        append_result(
            report,
            name=name,
            status="FAIL",
            command=shell_join(seed_command),
            duration_seconds=duration,
            detail="timeout after 180s",
            stdout=_truncate_output(exc.stdout),
            stderr=_truncate_output(exc.stderr),
        )
        return env, None

    duration = time.time() - started
    if completed.returncode != 0:
        append_result(
            report,
            name=name,
            status="FAIL",
            command=shell_join(seed_command),
            duration_seconds=duration,
            detail=f"exit_code={completed.returncode}",
            stdout=_truncate_output(completed.stdout),
            stderr=_truncate_output(completed.stderr),
        )
        return env, None

    try:
        seed_payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        append_result(
            report,
            name=name,
            status="FAIL",
            command=shell_join(seed_command),
            duration_seconds=duration,
            detail=f"seed output was not valid JSON: {exc}",
            stdout=_truncate_output(completed.stdout),
            stderr=_truncate_output(completed.stderr),
        )
        return env, None

    append_result(
        report,
        name=name,
        status="PASS",
        command=shell_join(seed_command),
        duration_seconds=duration,
        detail="exit_code=0",
        stdout=_truncate_output(completed.stdout),
        stderr=_truncate_output(completed.stderr),
    )
    return prepare_certification_env(seed_payload), seed_payload


def with_pythonpath(env: dict[str, str], *paths: Path) -> dict[str, str]:
    updated = env.copy()
    existing = updated.get("PYTHONPATH", "")
    ordered = [str(path) for path in paths if path]
    if existing:
        ordered.append(existing)
    updated["PYTHONPATH"] = os.pathsep.join(ordered)
    return updated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Release certification harness for generated device identity and shared scope contracts.",
    )
    parser.add_argument(
        "--mode",
        choices=("thorough", "quick"),
        default="thorough",
        help="thorough runs the full certification sequence; quick skips the slow pytest E2E suite.",
    )
    parser.add_argument(
        "--skip-live-browser",
        action="store_true",
        help="Skip the real browser contract checks even if credentials are provided.",
    )
    parser.add_argument(
        "--skip-compose-build",
        action="store_true",
        help="Skip docker compose rebuild before live browser checks.",
    )
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help="Exit 0 when the only non-pass statuses are BLOCKED.",
    )
    parser.add_argument(
        "--strict-release-gate",
        action="store_true",
        help="Treat missing live prerequisites as release-blocking failures and require the full live certification path.",
    )
    return parser


def blocked_is_allowed(args: argparse.Namespace) -> bool:
    return bool(args.allow_blocked and not args.strict_release_gate)


def strict_gate_prerequisite_failures(
    args: argparse.Namespace,
    *,
    live_browser_status: tuple[bool, str | None] | None = None,
    isolation_status: tuple[bool, str | None] | None = None,
    live_stack_status: tuple[bool, str | None] | None = None,
) -> list[str]:
    if not args.strict_release_gate:
        return []

    failures: list[str] = []
    if args.mode != "thorough":
        failures.append("Strict release gate requires --mode thorough.")
    if args.skip_live_browser:
        failures.append("Strict release gate cannot skip the live browser certification step.")
    if args.allow_blocked:
        failures.append("Strict release gate cannot be combined with --allow-blocked.")

    live_browser_ok, live_browser_reason = live_browser_status or has_live_browser_env(require_org=False)
    if not live_browser_ok and live_browser_reason:
        failures.append(live_browser_reason)

    live_stack_ok, live_stack_reason = live_stack_status or has_live_stack_test_account()
    if not live_stack_ok and live_stack_reason:
        failures.append(live_stack_reason)

    return failures


def main() -> int:
    args = build_parser().parse_args()
    certification_python = resolve_certification_python()
    report = CertificationReport(started_at_epoch=time.time(), mode=args.mode)
    try:
        strict_failures = strict_gate_prerequisite_failures(args)
        if strict_failures:
            append_result(
                report,
                name="Strict release gate prerequisites",
                status="FAIL",
                command="strict-release-gate",
                detail=" | ".join(strict_failures),
            )
            return finish(report, 1)

        live_stack_auth_ok, live_stack_auth_blocked_reason = has_live_stack_test_account()
        allow_blocked = blocked_is_allowed(args)
        certification_env = prepare_certification_env()

        seed_payload: dict[str, object] | None = None
        if args.strict_release_gate:
            certification_env, seed_payload = seed_certification_orgs(
                report,
                certification_python=certification_python,
                env=certification_env,
            )

            if report.failed:
                return finish(report, 1)

        run_command(
            report,
            "Backend generated-id regression tests",
        [
            certification_python,
            "-m",
            "pytest",
            "services/device-service/tests/test_device_id_generation.py",
            "services/device-service/tests/test_delete_device_regression.py",
            "-q",
        ],
        env=certification_env,
        timeout_seconds=180,
    )

        data_service_env = with_pythonpath(
        certification_env,
        REPO_ROOT,
        REPO_ROOT / "services" / "data-service",
    )
        run_command(
        report,
        "Telemetry topic contract regression tests",
        [
            certification_python,
            "-m",
            "pytest",
            "services/data-service/tests/test_mqtt_handler.py",
            "-q",
        ],
        env=data_service_env,
        timeout_seconds=180,
    )

        reporting_env = with_pythonpath(
        certification_env,
        REPO_ROOT,
        REPO_ROOT / "services" / "reporting-service",
    )
        run_command(
        report,
        "Reporting tenant-scope tests",
        [
            certification_python,
            "-m",
            "pytest",
            "tests/test_reporting_tenant_scope.py",
            "-q",
        ],
        allow_blocked=allow_blocked,
        blocked_reason=None if live_stack_auth_ok else live_stack_auth_blocked_reason,
        env=reporting_env,
        timeout_seconds=180,
    )

        waste_env = with_pythonpath(
        certification_env,
        REPO_ROOT,
        REPO_ROOT / "services" / "waste-analysis-service",
    )
        run_command(
        report,
        "Waste tenant-scope tests",
        [
            certification_python,
            "-m",
            "pytest",
            "tests/test_waste_history_tenant_scope.py",
            "-q",
        ],
        allow_blocked=allow_blocked,
        blocked_reason=None if live_stack_auth_ok else live_stack_auth_blocked_reason,
        env=waste_env,
        timeout_seconds=180,
    )

        rule_engine_env = with_pythonpath(
        certification_env,
        REPO_ROOT,
        REPO_ROOT / "services" / "rule-engine-service",
    )
        run_command(
        report,
        "Rule-engine and shared tenant guardrail tests",
        [
            certification_python,
            "-m",
            "pytest",
            "tests/test_rule_engine_tenant_scope.py",
            "tests/test_shared_tenant_guardrails.py",
            "-q",
        ],
        allow_blocked=allow_blocked,
        blocked_reason=None if live_stack_auth_ok else live_stack_auth_blocked_reason,
        env=rule_engine_env,
        timeout_seconds=180,
    )

        if args.mode == "thorough":
            run_command(
            report,
            "Cross-service pytest E2E suite",
            [
                certification_python,
                "-m",
                "pytest",
                "tests/e2e/test_02_device_onboarding.py",
                "tests/e2e/test_03_telemetry.py",
                "tests/e2e/test_07_rules.py",
                "tests/e2e/test_08_analytics.py",
                "tests/e2e/test_09_reporting.py",
                "tests/e2e/test_10_waste_analysis.py",
                "-q",
            ],
            allow_blocked=allow_blocked,
            blocked_reason=None if live_stack_auth_ok else live_stack_auth_blocked_reason,
            env=certification_env,
            timeout_seconds=CROSS_SERVICE_E2E_TIMEOUT_SECONDS,
        )

        isolation_ok, isolation_blocked_reason = has_isolation_env(certification_env)
        run_command(
        report,
        "Org isolation validator",
        [certification_python, "scripts/validate_isolation.py"],
        allow_blocked=allow_blocked,
        blocked_reason=None if isolation_ok else isolation_blocked_reason,
        env=certification_env,
        timeout_seconds=240,
    )

        run_command(
        report,
        "UI typecheck",
        ["npx", "tsc", "--noEmit"],
        cwd=UI_ROOT,
        env=certification_env,
        timeout_seconds=240,
    )

        run_command(
        report,
        "UI unit tests",
        ["npm", "run", "test:unit"],
        cwd=UI_ROOT,
        env=certification_env,
        timeout_seconds=240,
    )

        run_command(
        report,
        "Mocked onboarding Playwright regression",
        [
            *resolve_playwright_command(),
            "test",
            "tests/e2e/device-onboard-generated-id.spec.js",
        ],
        cwd=UI_ROOT,
        env=certification_env,
        timeout_seconds=240,
    )

        if not args.skip_live_browser:
            live_browser_env = certification_env.copy()
            live_ok, blocked_reason = has_live_browser_env(
                require_org=not (args.strict_release_gate and seed_payload is not None),
                env=live_browser_env,
            )
            if not live_ok and seed_payload is None:
                live_browser_env, seed_payload = seed_certification_orgs(
                    report,
                    certification_python=certification_python,
                    env=live_browser_env,
                    name="Live browser certification seeding",
                )
                if report.failed:
                    return finish(report, 1)
            if args.strict_release_gate:
                live_browser_env["CERTIFY_REQUIRE_PM"] = "1"
            live_ok, blocked_reason = has_live_browser_env(
                require_org=not (args.strict_release_gate and seed_payload is not None),
                env=live_browser_env,
            )
            if not args.skip_compose_build:
                ui_base = live_browser_env.get("CERTIFY_UI_BASE_URL", "http://localhost:3000").rstrip("/")
                health_checks = certification_health_checks(ui_base)
                stack_ready = True
                for _step_name, url in health_checks:
                    ok, _reason = probe_http_200(url)
                    if not ok:
                        stack_ready = False
                        break
                if stack_ready:
                    append_result(
                        report,
                        name="Compose rebuild before live browser certification",
                        status="PASS",
                        command="docker compose up -d --build ui-web",
                        detail="Skipped rebuild because the existing live stack was already healthy.",
                    )
                else:
                    run_command(
                        report,
                        "Compose rebuild before live browser certification",
                        ["docker", "compose", "up", "-d", "--build", "ui-web"],
                        timeout_seconds=600,
                    )
                for step_name, url in health_checks:
                    ok, reason = wait_for_http_200(url)
                    append_result(
                        report,
                        name=step_name,
                        status="PASS" if ok else "FAIL",
                        command=f"wait_for_http_200 {url}",
                        detail="ready" if ok else (reason or "service did not become healthy"),
                    )
                if report.failed:
                    return finish(report, 1)
            run_command(
                report,
                "Live browser scope and contract certification",
                ["node", "scripts/live_scope_certifier.js"],
                cwd=UI_ROOT,
                env=live_browser_env,
                allow_blocked=allow_blocked,
                blocked_reason=None if live_ok else blocked_reason,
                timeout_seconds=LIVE_BROWSER_TIMEOUT_SECONDS,
            )

        if report.failed:
            return finish(report, 1)
        if report.blocked and not allow_blocked:
            return finish(report, 2)
        return finish(report, 0)
    except Exception as exc:
        append_result(
            report,
            name="Release certification harness failure",
            status="FAIL",
            command="main",
            detail=f"{type(exc).__name__}: {exc}",
            stderr=_truncate_output(traceback.format_exc()),
        )
        return finish(report, 1)


if __name__ == "__main__":
    raise SystemExit(main())
