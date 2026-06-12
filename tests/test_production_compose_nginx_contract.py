from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

NGINX_UPSTREAM_SERVICES = {
    "device-service": ("DEVICE_SERVICE", "8000"),
    "data-service": ("DATA_SERVICE", "8081"),
    "rule-engine-service": ("RULE_ENGINE_SERVICE", "8002"),
    "analytics-service": ("ANALYTICS_SERVICE", "8003"),
    "reporting-service": ("REPORTING_SERVICE", "8085"),
    "waste-analysis-service": ("WASTE_ANALYSIS_SERVICE", "8087"),
    "copilot-service": ("COPILOT_SERVICE", "8007"),
}


def _service_block(compose_text: str, service_name: str) -> str:
    pattern = re.compile(
        rf"^  {re.escape(service_name)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(compose_text)
    assert match is not None, f"{service_name} is missing from docker-compose.yml"
    return match.group("body")


def test_host_nginx_upstream_services_are_bound_to_loopback_ports() -> None:
    compose_text = COMPOSE_FILE.read_text()

    for service_name, (env_prefix, container_port) in NGINX_UPSTREAM_SERVICES.items():
        block = _service_block(compose_text, service_name)

        assert "ports:" in block, f"{service_name} must publish a loopback host port for host nginx"
        assert (
            f"${{{env_prefix}_HOST_BIND:-127.0.0.1}}" in block
        ), f"{service_name} must bind to loopback by default"
        assert (
            f"${{{env_prefix}_HOST_PORT:-{container_port}}}:{container_port}" in block
        ), f"{service_name} must default to nginx upstream port {container_port}"
