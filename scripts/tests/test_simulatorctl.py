from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "simulatorctl.sh"


def _write_fake_docker(bin_dir: Path, log_path: Path, *, network_exists: bool = True) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_path = bin_dir / "docker"
    inspect_exit = "0" if network_exists else "1"
    docker_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'printf \"%s\\n\" \"$*\" >> \"{log_path}\"',
                'if [[ \"$1\" == \"compose\" ]]; then',
                '  if [[ \" $* \" == *\" config \"* ]]; then',
                '    printf \"name: shivex-main\\n\"',
                "    exit 0",
                "  fi",
                '  if [[ \" $* \" == *\" build telemetry-simulator \"* ]]; then',
                "    exit 0",
                "  fi",
                "fi",
                'if [[ \"$1 $2 $3\" == \"network inspect shivex-main_energy-net\" ]]; then',
                f"  exit {inspect_exit}",
                "fi",
                'if [[ \"$1\" == \"ps\" ]]; then',
                "  exit 0",
                "fi",
                'if [[ \"$1\" == \"run\" ]]; then',
                "  exit 0",
                "fi",
                'if [[ \"$1\" == \"start\" || \"$1\" == \"update\" || \"$1\" == \"stop\" || \"$1\" == \"logs\" || \"$1\" == \"rm\" ]]; then',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        ),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)


def _write_fake_curl(bin_dir: Path, log_path: Path, *, lookup_status: str = "200", list_body: str = '{"data": []}') -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    curl_path = bin_dir / "curl"
    lookup_body = {
        "200": '{"data":{"device_id":"VD00000003"}}',
        "401": '{"detail":{"code":"INVALID_INTERNAL_SERVICE_AUTH","message":"Internal service proof is invalid."}}',
        "404": '{"detail":{"code":"DEVICE_NOT_FOUND","message":"Device with ID \\"VD00000003\\" not found"}}',
    }.get(lookup_status, '{"detail":{"message":"Unexpected error"}}')
    curl_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'printf \"%s\\n\" \"$*\" >> \"{log_path}\"',
                'out_file=""',
                'write_code=""',
                'headers=()',
                'url=""',
                'while [[ $# -gt 0 ]]; do',
                '  case "$1" in',
                '    -H)',
                '      headers+=("$2")',
                '      shift 2',
                '      ;;',
                '    -o)',
                '      out_file="$2"',
                '      shift 2',
                '      ;;',
                '    -w)',
                '      write_code="$2"',
                '      shift 2',
                '      ;;',
                '    -s|-S|-sS|-f|-fsS)',
                '      shift',
                '      ;;',
                '    http://*)',
                '      url="$1"',
                '      shift',
                '      ;;',
                '    *)',
                '      shift',
                '      ;;',
                '  esac',
                'done',
                'service=""',
                'signature=""',
                'timestamp=""',
                'tenant=""',
                'for header in "${headers[@]}"; do',
                '  case "$header" in',
                '    X-Internal-Service:*) service="${header#X-Internal-Service: }" ;;',
                '    X-Internal-Service-Signature:*) signature="${header#X-Internal-Service-Signature: }" ;;',
                '    X-Internal-Service-Timestamp:*) timestamp="${header#X-Internal-Service-Timestamp: }" ;;',
                '    X-Tenant-Id:*) tenant="${header#X-Tenant-Id: }" ;;',
                '  esac',
                'done',
                'status="500"',
                'body="{\"detail\":{\"message\":\"Unhandled\"}}"',
                'if [[ "$url" == *"/api/v1/devices/VD00000003" ]]; then',
                '  if [[ -n "$service" && -n "$signature" && -n "$timestamp" && -n "$tenant" ]]; then',
                f'    status="{lookup_status}"',
                f"    body='{lookup_body}'",
                '  else',
                '    status="401"',
                '    body="{\"detail\":{\"code\":\"INVALID_INTERNAL_SERVICE_AUTH\",\"message\":\"Internal service proof is required.\"}}"',
                '  fi',
                'elif [[ "$url" == *"/api/v1/devices" ]]; then',
                '  status="200"',
                f"  body='{list_body}'",
                'fi',
                'if [[ -n "$out_file" ]]; then',
                '  printf "%s" "$body" > "$out_file"',
                "else",
                '  printf "%s" "$body"',
                "fi",
                'if [[ "$write_code" == "%{http_code}" ]]; then',
                '  printf "%s" "$status"',
                'fi',
                'if [[ "$status" =~ ^2 ]]; then',
                '  exit 0',
                'fi',
                'if [[ "$url" == *"/api/v1/devices" && "$write_code" != "%{http_code}" ]]; then',
                '  exit 22',
                'fi',
                'exit 0',
            ]
        ),
        encoding="utf-8",
    )
    curl_path.chmod(0o755)


def _run_simulatorctl(
    tmp_path: Path,
    args: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
    repo_root: Path | None = None,
    script_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["INTERNAL_SERVICE_SHARED_SECRET"] = "test-shared-secret"
    if env_overrides:
        env.update(env_overrides)
    effective_repo_root = repo_root or REPO_ROOT
    effective_script_path = script_path or SCRIPT_PATH
    return subprocess.run(
        [str(effective_script_path), *args],
        cwd=str(effective_repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_simulatorctl_purge_removes_standalone_simulators(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "docker.log"
    docker_path = bin_dir / "docker"
    docker_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'printf \"%s\\n\" \"$*\" >> \"{log_path}\"',
                'if [[ \"$1 $2\" == \"ps -aq\" ]]; then',
                '  printf \"container-1\\ncontainer-2\\n\"',
                "  exit 0",
                "fi",
                'if [[ \"$1\" == \"rm\" && \"$2\" == \"-f\" ]]; then',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        ),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    completed = subprocess.run(
        [str(SCRIPT_PATH), "purge"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert completed.returncode == 0
    assert "Purged all simulator containers" in completed.stdout
    log_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert log_lines == [
        "ps -aq --filter name=^/telemetry-simulator-",
        "rm -f container-1 container-2",
    ]


def test_simulatorctl_legacy_secure_flag_is_plain_tcp_compatibility_mode(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    curl_log = tmp_path / "curl.log"
    _write_fake_docker(tmp_path / "bin", docker_log)
    _write_fake_curl(tmp_path / "bin", curl_log)

    completed = _run_simulatorctl(
        tmp_path,
        ["start", "--secure", "VD00000003"],
        env_overrides={
            "BOOTSTRAP_SUPER_ADMIN_EMAIL": "super@example.com",
            "BOOTSTRAP_SUPER_ADMIN_PASSWORD": "Validate123!",
        },
    )

    assert completed.returncode == 0
    assert "Flag --secure is deprecated and now acts as a no-op." in completed.stdout
    assert "Simulator started for SH00000001/VD00000003" in completed.stdout
    assert "on MQTT 1883 with username/password auth" in completed.stdout

    curl_output = curl_log.read_text(encoding="utf-8")
    assert "X-Internal-Service: telemetry-simulator" in curl_output
    assert "X-Internal-Service-Timestamp:" in curl_output
    assert "X-Internal-Service-Signature:" in curl_output
    assert "X-Tenant-Id: SH00000001" in curl_output

    docker_output = docker_log.read_text(encoding="utf-8")
    assert "compose --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml build telemetry-simulator" in docker_output
    assert "network inspect shivex-main_energy-net" in docker_output
    assert "MQTT_BROKER_PORT=1883" in docker_output
    assert "MQTT_TLS_ENABLED" not in docker_output
    assert "MQTT_CA_CERT_PATH" not in docker_output
    assert "INTERNAL_SERVICE_SHARED_SECRET=test-shared-secret" in docker_output
    assert "MQTT_CREDENTIAL_BOOTSTRAP_EMAIL=super@example.com" in docker_output
    assert "MQTT_CREDENTIAL_BOOTSTRAP_PASSWORD=Validate123!" in docker_output


def test_simulatorctl_start_reports_missing_bootstrap_credentials(tmp_path: Path) -> None:
    isolated_repo = tmp_path / "repo"
    (isolated_repo / "scripts").mkdir(parents=True)
    script_copy = isolated_repo / "scripts" / "simulatorctl.sh"
    script_copy.write_text(SCRIPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    script_copy.chmod(0o755)

    docker_log = tmp_path / "docker.log"
    curl_log = tmp_path / "curl.log"
    _write_fake_docker(tmp_path / "bin", docker_log)
    _write_fake_curl(tmp_path / "bin", curl_log)

    completed = _run_simulatorctl(
        tmp_path,
        ["start", "VD00000003"],
        env_overrides={
            "BOOTSTRAP_SUPER_ADMIN_EMAIL": "",
            "BOOTSTRAP_SUPER_ADMIN_PASSWORD": "",
        },
        repo_root=isolated_repo,
        script_path=script_copy,
    )

    assert completed.returncode == 1
    assert "Simulator start requires BOOTSTRAP_SUPER_ADMIN_EMAIL and BOOTSTRAP_SUPER_ADMIN_PASSWORD in .env.local" in completed.stdout
    assert "run -d" not in docker_log.read_text(encoding="utf-8")


def test_simulatorctl_reports_missing_device_truthfully(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    curl_log = tmp_path / "curl.log"
    _write_fake_docker(tmp_path / "bin", docker_log)
    _write_fake_curl(
        tmp_path / "bin",
        curl_log,
        lookup_status="404",
        list_body='{"data":[{"device_id":"VD00000001"},{"device_id":"VD00000002"}]}',
    )

    completed = _run_simulatorctl(tmp_path, ["start", "VD00000003"])

    assert completed.returncode == 1
    assert "Device SH00000001/VD00000003 is not onboarded in device-service." in completed.stdout
    assert "Available onboarded devices:" in completed.stdout
    assert "  - VD00000001" in completed.stdout
    assert "  - VD00000002" in completed.stdout


def test_simulatorctl_reports_internal_auth_contract_failure_truthfully(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    curl_log = tmp_path / "curl.log"
    _write_fake_docker(tmp_path / "bin", docker_log)
    _write_fake_curl(tmp_path / "bin", curl_log, lookup_status="401")

    completed = _run_simulatorctl(tmp_path, ["start", "VD00000003"])

    assert completed.returncode == 1
    assert "was rejected by the internal-service auth contract" in completed.stdout
    assert "Auth failure: Internal service proof is invalid." in completed.stdout


def test_simulatorctl_reports_missing_platform_network_truthfully(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    curl_log = tmp_path / "curl.log"
    _write_fake_docker(tmp_path / "bin", docker_log, network_exists=False)
    _write_fake_curl(tmp_path / "bin", curl_log)

    completed = _run_simulatorctl(tmp_path, ["start", "VD00000003"])

    assert completed.returncode == 1
    assert "Docker network shivex-main_energy-net is missing." in completed.stdout
