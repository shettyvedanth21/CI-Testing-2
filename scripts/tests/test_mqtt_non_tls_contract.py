from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_emqx_configs_use_tcp_1883_without_ssl_listener() -> None:
    local_config = (REPO_ROOT / "ops" / "emqx" / "local.base.hocon").read_text(encoding="utf-8")
    production_config = (REPO_ROOT / "ops" / "emqx" / "production.base.hocon").read_text(encoding="utf-8")

    for config_text in (local_config, production_config):
        assert 'bind = "0.0.0.0:1883"' in config_text
        assert "listeners.ssl.default" not in config_text
        assert "quick_deny_anonymous" in config_text


def test_local_validator_targets_plain_tcp_auth_acl_flow() -> None:
    script_text = (REPO_ROOT / "scripts" / "validate_mqtt_device_auth_local.sh").read_text(encoding="utf-8")

    assert 'client.connect("emqx", 1883, 10)' in script_text
    assert 'echo "Validating broker auth success on 1883..."' in script_text
    assert 'echo "Validating anonymous client rejection..."' in script_text
    assert "tls_set(" not in script_text
    assert "8883" not in script_text


def test_local_simulator_paths_no_longer_inject_tls_env() -> None:
    simulatorctl = (REPO_ROOT / "scripts" / "simulatorctl.sh").read_text(encoding="utf-8")
    local_compose = (REPO_ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")

    assert 'local mqtt_broker_port="1883"' in simulatorctl
    assert "MQTT_TLS_ENABLED" not in simulatorctl
    assert "MQTT_CA_CERT_PATH" not in simulatorctl
    assert "MQTT_BROKER_PORT=1883" in local_compose
    assert "MQTT_TLS_ENABLED" not in local_compose
    assert "MQTT_CA_CERT_PATH" not in local_compose
