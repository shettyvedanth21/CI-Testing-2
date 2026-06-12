"""CLI entry point for device simulator."""
import argparse
import logging
import os
import sys
import socket

from config import SimulatorConfig
from provisioning_bundle import parse_provisioning_bundle
from simulator import DeviceSimulator


def setup_logging(log_level: str) -> None:
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, log_level.upper()))

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(handler)

    if log_level.upper() != "DEBUG":
        logging.getLogger("paho").setLevel(logging.WARNING)


def _default_internal_service_url(service_name: str, port: int) -> str:
    """Resolve to Docker DNS when available, otherwise fall back to localhost.

    This keeps the simulator usable both:
    - inside the compose network, where service DNS names resolve
    - from a host-shell launch, where only localhost is reachable
    """
    try:
        socket.getaddrinfo(service_name, port)
        host = service_name
    except socket.gaierror:
        host = "localhost"
    return f"http://{host}:{port}"


def parse_arguments() -> SimulatorConfig:
    """CLI + ENV compatible configuration with support for custom metrics."""

    env_device_id = os.getenv("DEVICE_ID")
    env_tenant_id = os.getenv("TENANT_ID") or "SH00000001"
    env_broker = os.getenv("MQTT_BROKER_HOST", "localhost")
    env_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    env_mqtt_username = os.getenv("MQTT_USERNAME", "")
    env_mqtt_password = os.getenv("MQTT_PASSWORD", "")
    env_interval = float(os.getenv("PUBLISH_INTERVAL", "5"))
    env_fault_mode = os.getenv("FAULT_MODE", "none")
    env_log_level = os.getenv("LOG_LEVEL", "INFO")
    env_metrics = os.getenv("METRICS", "")
    env_auth_service_url = os.getenv("AUTH_SERVICE_URL", _default_internal_service_url("auth-service", 8090))
    env_mqtt_credential_bootstrap_email = os.getenv("MQTT_CREDENTIAL_BOOTSTRAP_EMAIL", "")
    env_mqtt_credential_bootstrap_password = os.getenv("MQTT_CREDENTIAL_BOOTSTRAP_PASSWORD", "")
    env_device_service_url = os.getenv("DEVICE_SERVICE_URL", _default_internal_service_url("device-service", 8000))
    env_heartbeat_interval = float(os.getenv("HEARTBEAT_INTERVAL_SEC", "20"))
    env_provisioning_bundle = os.getenv("MQTT_PROVISIONING_BUNDLE", "")

    parser = argparse.ArgumentParser(
        description="Energy Intelligence Platform - Device Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--device-id",
        type=str,
        default=env_device_id,
        help="Device identifier (env: DEVICE_ID)"
    )

    parser.add_argument(
        "--tenant-id",
        "-t",
        type=str,
        default=env_tenant_id,
        dest="tenant_id",
        help="Tenant identifier used in MQTT topic (env: TENANT_ID)"
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=env_interval,
        help="Publish interval in seconds (env: PUBLISH_INTERVAL)"
    )

    parser.add_argument(
        "--broker",
        type=str,
        default=env_broker,
        help="MQTT broker host (env: MQTT_BROKER_HOST)"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=env_port,
        help="MQTT broker port (env: MQTT_BROKER_PORT)"
    )

    parser.add_argument(
        "--mqtt-username",
        type=str,
        default=env_mqtt_username,
        help="Explicit MQTT username override (env: MQTT_USERNAME)",
    )

    parser.add_argument(
        "--mqtt-password",
        type=str,
        default=env_mqtt_password,
        help="Explicit MQTT password override (env: MQTT_PASSWORD)",
    )

    parser.add_argument(
        "--provisioning-bundle",
        type=str,
        default=env_provisioning_bundle,
        help="JSON provisioning bundle captured from the onboarding QR (env: MQTT_PROVISIONING_BUNDLE)",
    )

    parser.add_argument(
        "--provisioning-bundle-file",
        type=str,
        default="",
        help="Path to a JSON file containing the onboarding provisioning bundle",
    )

    parser.add_argument(
        "--fault-mode",
        type=str,
        default=env_fault_mode,
        choices=["none", "spike", "drop", "overheating", "phase_imbalance", "power_factor_drop", "load_cycle"],
        help="Fault mode (env: FAULT_MODE)"
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default=env_log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (env: LOG_LEVEL)"
    )

    parser.add_argument(
        "--metrics",
        type=str,
        default=env_metrics,
        help="Comma-separated list of metrics to generate (e.g., 'voltage,current,power,temperature') or JSON array (env: METRICS)"
    )

    parser.add_argument(
        "--metrics-json",
        type=str,
        default="",
        help="JSON object defining metrics with their ranges: '{\"pressure\": [0, 10], \"temperature\": [20, 100]}'"
    )

    parser.add_argument(
        "--device-service-url",
        type=str,
        default=env_device_service_url,
        help="Device service URL for heartbeat fallback (env: DEVICE_SERVICE_URL)",
    )

    parser.add_argument(
        "--auth-service-url",
        type=str,
        default=env_auth_service_url,
        help="Auth service URL for local MQTT credential bootstrap (env: AUTH_SERVICE_URL)",
    )

    parser.add_argument(
        "--mqtt-credential-bootstrap-email",
        type=str,
        default=env_mqtt_credential_bootstrap_email,
        help="Admin email used to fetch per-device MQTT credentials (env: MQTT_CREDENTIAL_BOOTSTRAP_EMAIL)",
    )

    parser.add_argument(
        "--mqtt-credential-bootstrap-password",
        type=str,
        default=env_mqtt_credential_bootstrap_password,
        help="Admin password used to fetch per-device MQTT credentials (env: MQTT_CREDENTIAL_BOOTSTRAP_PASSWORD)",
    )

    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=env_heartbeat_interval,
        help="Heartbeat fallback interval in seconds (env: HEARTBEAT_INTERVAL_SEC)",
    )

    args = parser.parse_args()

    provisioning_bundle = None
    raw_provisioning_bundle = args.provisioning_bundle
    if args.provisioning_bundle_file:
        with open(args.provisioning_bundle_file, "r", encoding="utf-8") as handle:
            raw_provisioning_bundle = handle.read()

    if raw_provisioning_bundle:
        provisioning_bundle = parse_provisioning_bundle(raw_provisioning_bundle)

    device_id = args.device_id
    tenant_id = args.tenant_id
    broker_host = args.broker
    broker_port = args.port
    mqtt_username = args.mqtt_username
    mqtt_password = args.mqtt_password
    publish_topic_override = None

    if provisioning_bundle is not None:
        device_id = provisioning_bundle.device_id
        tenant_id = provisioning_bundle.tenant_id
        broker_host = provisioning_bundle.broker
        broker_port = provisioning_bundle.port
        mqtt_username = provisioning_bundle.username
        mqtt_password = provisioning_bundle.password
        publish_topic_override = provisioning_bundle.topic

    if not device_id:
        raise ValueError(
            "device-id must be provided either via --device-id or DEVICE_ID env variable"
        )

    return SimulatorConfig(
        device_id=device_id,
        tenant_id=tenant_id,
        publish_interval=args.interval,
        broker_host=broker_host,
        broker_port=broker_port,
        mqtt_username=mqtt_username,
        mqtt_password=mqtt_password,
        publish_topic_override=publish_topic_override,
        auth_service_url=args.auth_service_url,
        mqtt_credential_bootstrap_email=args.mqtt_credential_bootstrap_email,
        mqtt_credential_bootstrap_password=args.mqtt_credential_bootstrap_password,
        fault_mode=args.fault_mode,
        log_level=args.log_level,
        metrics=args.metrics,
        metrics_json=args.metrics_json,
        device_service_url=args.device_service_url,
        heartbeat_interval_sec=args.heartbeat_interval,
    )


def main() -> int:
    try:
        config = parse_arguments()
        setup_logging(config.log_level)

        logging.getLogger(__name__).info(
            "Starting device simulator",
            extra={
                "device_id": config.device_id,
                "tenant_id": config.tenant_id,
                "broker": config.broker_host,
                "port": config.broker_port,
                "topic": config.topic,
                "metrics": config.metrics,
            },
        )

        simulator = DeviceSimulator(config)
        simulator.start()

        return 0

    except ValueError as e:
        logging.error(f"Configuration error: {e}")
        return 1
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
        return 0
    except Exception as e:
        logging.error("Unexpected error", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
