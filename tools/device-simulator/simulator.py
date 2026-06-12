"""Core device simulator implementation."""
import logging
import signal
import sys
import time
import re
from collections import deque
from typing import Deque, Optional
from urllib import request
from urllib.parse import urlencode

from config import SimulatorConfig
from credential_bootstrap import DeviceMQTTCredentialBootstrap
from internal_service_auth import build_internal_service_headers
from mqtt_client import MQTTClient
from telemetry_generator import TelemetryGenerator

logger = logging.getLogger(__name__)


def _sanitize_component(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-")


class DeviceSimulator:
    """Production-grade device simulator for MQTT telemetry.
    
    This simulator generates realistic telemetry data and publishes it to
    an MQTT broker with automatic reconnection and graceful shutdown support.
    
    Features:
    - Realistic time-series data generation
    - Dynamic metric support
    - MQTT QoS 1 publishing
    - Automatic reconnection with exponential backoff
    - Graceful shutdown on SIGINT/SIGTERM
    - Structured logging
    - Fault injection for testing
    """
    
    def __init__(self, config: SimulatorConfig):
        """Initialize device simulator.
        
        Args:
            config: Simulator configuration
        """
        self._config = config
        self._mqtt_client: Optional[MQTTClient] = None
        self._telemetry_generator: Optional[TelemetryGenerator] = None
        self._running = False
        self._message_count = 0
        self._last_heartbeat_sent = 0.0
        self._pending_payloads: Deque[dict] = deque(maxlen=10000)
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def start(self) -> None:
        """Start the device simulator."""
        logger.info(
            "Starting device simulator",
            extra={
                "device_id": self._config.device_id,
                "tenant_id": self._config.tenant_id,
                "interval": self._config.publish_interval,
                "broker": f"{self._config.broker_host}:{self._config.broker_port}",
                "fault_mode": self._config.fault_mode,
                "metrics": list(self._config.metric_config.keys()),
                "device_service_url": self._config.device_service_url,
                "heartbeat_interval_sec": self._config.heartbeat_interval_sec,
            }
        )
        
        self._telemetry_generator = TelemetryGenerator(
            device_id=self._config.device_id,
            fault_mode=self._config.fault_mode,
            metric_config=self._config.metric_config,
        )

        mqtt_username = self._config.mqtt_username or None
        mqtt_password = self._config.mqtt_password or None
        if self._config.should_bootstrap_mqtt_credential:
            credential = DeviceMQTTCredentialBootstrap(
                auth_service_url=self._config.auth_service_url,
                device_service_url=self._config.device_service_url,
                tenant_id=self._config.tenant_id,
                device_id=self._config.device_id,
                bootstrap_email=self._config.mqtt_credential_bootstrap_email,
                bootstrap_password=self._config.mqtt_credential_bootstrap_password,
            ).fetch()
            mqtt_username = credential.mqtt_username
            mqtt_password = credential.mqtt_password
            logger.info(
                "Fetched per-device MQTT credential for simulator",
                extra={
                    "tenant_id": self._config.tenant_id,
                    "device_id": self._config.device_id,
                    "mqtt_username": credential.mqtt_username,
                    "publish_topic": credential.publish_topic,
                },
            )

        self._mqtt_client = MQTTClient(
            broker_host=self._config.broker_host,
            broker_port=self._config.broker_port,
            client_id=(
                "simulator_"
                f"{_sanitize_component(self._config.tenant_id)}_"
                f"{_sanitize_component(self._config.device_id)}"
            ),
            username=mqtt_username,
            password=mqtt_password,
        )
        
        if not self._mqtt_client.connect():
            logger.error("Failed to connect to MQTT broker. Exiting.")
            sys.exit(1)
        
        self._running = True
        self._run_loop()
    
    def stop(self) -> None:
        """Stop the device simulator gracefully."""
        if not self._running:
            return
            
        logger.info(
            "Stopping device simulator",
            extra={
                "device_id": self._config.device_id,
                "messages_published": self._message_count
            }
        )
        
        self._running = False
        
        if self._mqtt_client:
            self._mqtt_client.disconnect()
        
        logger.info("Device simulator stopped")
    
    def _run_loop(self) -> None:
        """Main simulation loop."""
        last_publish_time = 0.0
        
        while self._running:
            current_time = time.time()
            
            if self._mqtt_client and not self._mqtt_client.is_connected:
                self._send_fallback_heartbeat_if_due(current_time)
                logger.warning(
                    "Connection lost, triggering reconnect from main loop",
                    extra={"device_id": self._config.device_id}
                )
                self._mqtt_client.reconnect()
                time.sleep(1)
                continue
            
            if current_time - last_publish_time >= self._config.publish_interval:
                self._publish_telemetry()
                last_publish_time = current_time

            self._flush_pending_payloads()
            if self._should_send_fallback_heartbeat():
                self._send_fallback_heartbeat_if_due(current_time)
            
            time.sleep(0.1)
    
    def _publish_telemetry(self) -> None:
        """Generate and publish telemetry data."""
        if not self._telemetry_generator or not self._mqtt_client:
            return

        telemetry = self._telemetry_generator.generate()
        payload = telemetry.to_dict()

        if not self._mqtt_client.is_connected:
            self._buffer_payload(payload)
            logger.warning(
                "MQTT disconnected; telemetry buffered",
                extra={
                    "device_id": self._config.device_id,
                    "pending_queue_size": len(self._pending_payloads),
                },
            )
            return
        
        success = self._mqtt_client.publish(
            topic=self._config.topic,
            payload=payload
        )
        
        if success:
            self._message_count += 1
            log_data = {
                "device_id": payload["device_id"],
                "message_count": self._message_count,
            }
            for key in payload:
                if key not in ("device_id", "timestamp", "schema_version"):
                    log_data[key] = payload[key]
            
            logger.info(
                "Telemetry published",
                extra=log_data,
            )
        else:
            self._buffer_payload(payload)
            logger.warning(
                "Failed to publish telemetry",
                extra={
                    "device_id": self._config.device_id,
                    "pending_queue_size": len(self._pending_payloads),
                },
            )

    def _flush_pending_payloads(self) -> None:
        """Replay buffered payloads after broker recovery."""
        if not self._pending_payloads or not self._mqtt_client or not self._mqtt_client.is_connected:
            return

        flushed = 0
        while self._pending_payloads and flushed < 100:
            payload = self._pending_payloads[0]
            success = self._mqtt_client.publish(
                topic=self._config.topic,
                payload=payload,
            )
            if not success:
                break
            self._pending_payloads.popleft()
            flushed += 1
            self._message_count += 1

        if flushed > 0:
            logger.info(
                "Flushed buffered telemetry",
                extra={
                    "device_id": self._config.device_id,
                    "flushed_count": flushed,
                    "remaining_queue_size": len(self._pending_payloads),
                },
            )

    def _buffer_payload(self, payload: dict) -> None:
        if len(self._pending_payloads) == self._pending_payloads.maxlen:
            logger.warning(
                "Buffered telemetry queue full; dropping oldest sample",
                extra={
                    "device_id": self._config.device_id,
                    "queue_capacity": self._pending_payloads.maxlen,
                },
            )
        self._pending_payloads.append(payload)

    def _should_send_fallback_heartbeat(self) -> bool:
        """Only use the HTTP heartbeat when MQTT/session health is degraded."""
        if not self._config.device_service_url or not self._mqtt_client:
            return False
        if not self._mqtt_client.is_connected:
            return True
        return bool(self._pending_payloads)

    def _send_fallback_heartbeat_if_due(self, current_time: float) -> None:
        """Keep runtime status alive when broker/session is degraded."""
        if (current_time - self._last_heartbeat_sent) < self._config.heartbeat_interval_sec:
            return
        self._last_heartbeat_sent = current_time
        self._send_device_heartbeat()

    def _send_device_heartbeat(self) -> None:
        if not self._config.device_service_url:
            return
        url = (
            f"{self._config.device_service_url.rstrip('/')}"
            f"/api/v1/devices/{self._config.device_id}/heartbeat"
        )
        req = request.Request(
            url=url,
            method="POST",
            headers=build_internal_service_headers("telemetry-simulator", self._config.tenant_id),
        )
        try:
            with request.urlopen(req, timeout=3) as response:
                if 200 <= response.status < 300:
                    logger.debug(
                        "Fallback heartbeat sent",
                        extra={"device_id": self._config.device_id, "status_code": response.status},
                    )
                else:
                    logger.warning(
                        "Fallback heartbeat returned non-success status",
                        extra={"device_id": self._config.device_id, "status_code": response.status},
                    )
        except RuntimeError as exc:
            logger.warning(
                "Fallback heartbeat auth contract is not configured",
                extra={"device_id": self._config.device_id, "error": str(exc)},
            )
        except Exception as exc:
            logger.warning(
                "Fallback heartbeat failed",
                extra={"device_id": self._config.device_id, "error": str(exc)},
            )
    
    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals gracefully."""
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        logger.info(f"Received {signal_name}, initiating graceful shutdown")
        self.stop()
        sys.exit(0)
