"""MQTT client wrapper with reconnect support and QoS 1."""
import json
import logging
import random
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MQTTClient:
    """Production-grade MQTT client with automatic reconnection."""

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        reconnect_min_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
        keepalive: int = 60,
    ):
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._reconnect_min_delay = reconnect_min_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._keepalive = keepalive

        self._client: Optional[mqtt.Client] = None
        self._connected = False
        self._shutdown_requested = False
        self._network_loop_started = False
        self._reconnecting = False
        self._consecutive_publish_failures = 0

        self._state_lock = threading.Lock()
        self._reconnect_lock = threading.Lock()
        self._reconnect_thread: Optional[threading.Thread] = None

    def connect(self) -> bool:
        """Connect to MQTT broker. Retries indefinitely until connected or shutdown."""
        self._shutdown_requested = False
        self._build_client()
        return self._connect_with_backoff(initial_connect=True)

    def publish(self, topic: str, payload: dict) -> bool:
        """Publish message to MQTT topic with QoS 1 and recovery signaling."""
        if not self.is_connected or not self._client:
            logger.warning("Cannot publish: not connected to broker")
            return False

        try:
            message = json.dumps(payload, separators=(",", ":"))
            info = self._client.publish(topic, message, qos=1)

            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                self._mark_publish_failure(
                    reason=f"rc={info.rc}",
                    topic=topic,
                    payload_size=len(message),
                )
                return False

            info.wait_for_publish(timeout=5.0)
            if not info.is_published():
                self._mark_publish_failure(
                    reason="publish_timeout",
                    topic=topic,
                    payload_size=len(message),
                )
                return False

            self._consecutive_publish_failures = 0
            logger.debug(
                "Message published",
                extra={"topic": topic, "payload_size": len(message)},
            )
            return True
        except Exception as exc:
            self._mark_publish_failure(
                reason=f"exception={exc}",
                topic=topic,
                payload_size=len(json.dumps(payload)),
            )
            return False

    def disconnect(self) -> None:
        """Disconnect from MQTT broker gracefully."""
        self._shutdown_requested = True

        with self._reconnect_lock:
            self._reconnecting = False

        if self._client:
            logger.info("Disconnecting from MQTT broker")
            try:
                self._client.disconnect()
            except Exception as exc:
                logger.warning("Error while disconnecting MQTT client", extra={"error": str(exc)})
            try:
                self._client.loop_stop()
            except Exception as exc:
                logger.warning("Error while stopping MQTT loop", extra={"error": str(exc)})

        with self._state_lock:
            self._connected = False
            self._network_loop_started = False

    def reconnect(self) -> bool:
        """Trigger background reconnect if disconnected."""
        if not self.is_connected:
            self._start_background_reconnect()
        return self.is_connected

    def _build_client(self) -> None:
        self._client = mqtt.Client(client_id=self._client_id, clean_session=False)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_publish = self._on_publish
        if self._username and self._password:
            self._client.username_pw_set(self._username, self._password)
        self._client.reconnect_delay_set(
            min_delay=int(self._reconnect_min_delay),
            max_delay=int(self._reconnect_max_delay),
        )
        # Keep internal queue bounded but large enough for transient outages.
        self._client.max_queued_messages_set(5000)
        self._client.max_inflight_messages_set(200)

    def _connect_with_backoff(self, initial_connect: bool) -> bool:
        delay = self._reconnect_min_delay
        attempt = 0

        while not self._shutdown_requested:
            attempt += 1
            try:
                if self._client is None:
                    self._build_client()
                if self._client is None:
                    raise RuntimeError("MQTT client was not initialized")

                if not self._network_loop_started:
                    self._client.loop_start()
                    self._network_loop_started = True

                logger.info(
                    "Connecting to MQTT broker",
                    extra={
                        "broker_host": self._broker_host,
                        "broker_port": self._broker_port,
                        "attempt": attempt,
                        "initial_connect": initial_connect,
                    },
                )

                if initial_connect and attempt == 1:
                    self._client.connect(
                        self._broker_host,
                        self._broker_port,
                        keepalive=self._keepalive,
                    )
                else:
                    self._client.reconnect()

                if self._wait_for_connection(timeout=10.0):
                    logger.info("Connected to MQTT broker successfully")
                    self._consecutive_publish_failures = 0
                    return True

                raise TimeoutError("MQTT connect timeout")
            except Exception as exc:
                logger.warning(
                    "MQTT connection attempt failed",
                    extra={
                        "attempt": attempt,
                        "error": str(exc),
                        "retry_delay_seconds": round(delay, 2),
                    },
                )
                jitter = random.uniform(0.0, min(1.0, delay * 0.25))
                time.sleep(delay + jitter)
                delay = min(delay * 2, self._reconnect_max_delay)
                initial_connect = False

        return False

    def _wait_for_connection(self, timeout: float) -> bool:
        start = time.monotonic()
        while time.monotonic() - start < timeout and not self._shutdown_requested:
            if self.is_connected:
                return True
            time.sleep(0.1)
        return self.is_connected

    def _mark_publish_failure(self, reason: str, topic: str, payload_size: int) -> None:
        self._consecutive_publish_failures += 1
        logger.warning(
            "MQTT publish failed",
            extra={
                "topic": topic,
                "payload_size": payload_size,
                "reason": reason,
                "consecutive_failures": self._consecutive_publish_failures,
            },
        )
        # Force fast recovery when session goes stale but disconnect callback is delayed.
        if self._consecutive_publish_failures >= 3:
            self._start_background_reconnect()

    def _start_background_reconnect(self) -> None:
        with self._reconnect_lock:
            if self._shutdown_requested:
                return
            if self._reconnecting:
                return
            self._reconnecting = True

        def reconnect_task() -> None:
            try:
                self._connect_with_backoff(initial_connect=False)
            finally:
                with self._reconnect_lock:
                    self._reconnecting = False

        self._reconnect_thread = threading.Thread(target=reconnect_task, daemon=True)
        self._reconnect_thread.start()

    def _on_connect(self, client, userdata, flags, rc):
        with self._state_lock:
            self._connected = rc == 0
        if rc == 0:
            logger.info("Connected to MQTT broker callback acknowledged")
        else:
            logger.error("MQTT connect rejected", extra={"return_code": rc})

    def _on_disconnect(self, client, userdata, rc):
        with self._state_lock:
            self._connected = False
        if rc == 0:
            logger.info("Disconnected from MQTT broker cleanly")
            return
        if self._shutdown_requested:
            return
        logger.warning(
            "Unexpected MQTT disconnection; scheduling reconnect",
            extra={"return_code": rc},
        )
        self._start_background_reconnect()

    def _on_publish(self, client, userdata, mid):
        logger.debug("Message published callback received", extra={"message_id": mid})

    @property
    def is_connected(self) -> bool:
        with self._state_lock:
            return self._connected
