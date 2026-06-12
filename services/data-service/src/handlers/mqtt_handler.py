"""MQTT message handler."""

import asyncio
import json
import random
import re
import threading
import uuid
from concurrent.futures import Future
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt

from src.config import settings
from src.services.telemetry_service import TelemetryService
from src.utils import get_logger

logger = get_logger(__name__)


class MQTTHandler:
    """MQTT message handler with resilient reconnect and loop-safe async handoff."""

    _DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
    _LEGACY_TOPIC = "devices/+/telemetry"
    _TENANT_TOPIC = "+/devices/+/telemetry"

    @classmethod
    def _subscription_topics(cls) -> list[tuple[str, int]]:
        topics: list[str] = []
        for topic in (settings.mqtt_topic, cls._LEGACY_TOPIC, cls._TENANT_TOPIC):
            if topic and topic not in topics:
                topics.append(topic)
        return [(topic, settings.mqtt_qos) for topic in topics]

    @classmethod
    def _extract_device_id_from_topic(cls, topic: str) -> tuple[Optional[str], Optional[str]]:
        parts = [p for p in (topic or "").split("/") if p]
        tenant_id: Optional[str] = None
        if len(parts) == 4 and parts[1] == "devices" and parts[3].lower() == "telemetry":
            tenant_id = parts[0].strip() or None
            candidate = parts[2].strip()
        elif len(parts) == 3 and parts[0] == "devices" and parts[2].lower() == "telemetry":
            candidate = parts[1].strip()
        else:
            return None, None
        if not candidate or not cls._DEVICE_ID_PATTERN.match(candidate):
            return None, None
        return tenant_id, candidate

    @staticmethod
    def _resolve_tenant_id(payload: Dict[str, Any], topic_tenant_id: Optional[str]) -> Optional[str]:
        payload_tenant_id = payload.get("tenant_id")
        if topic_tenant_id:
            if payload_tenant_id is None:
                payload["tenant_id"] = topic_tenant_id
            elif str(payload_tenant_id).strip() != topic_tenant_id:
                logger.warning(
                    "Dropping telemetry due to tenant mismatch",
                    topic_tenant_id=topic_tenant_id,
                    payload_tenant_id=payload_tenant_id,
                )
                return None
            return topic_tenant_id

        if payload_tenant_id is None:
            return None

        normalized = str(payload_tenant_id).strip()
        if not normalized:
            return None
        payload["tenant_id"] = normalized
        return normalized

    def __init__(self, telemetry_service: Optional[TelemetryService] = None):
        self.telemetry_service = telemetry_service
        self.client: Optional[mqtt.Client] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client_lock = threading.Lock()

        self._connected = False
        self._shutdown_requested = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._connect_attempt = 0

        self._reconnect_interval = max(1, settings.mqtt_reconnect_interval)

        logger.info(
            "MQTTHandler initialized",
            broker_host=settings.mqtt_broker_host,
            broker_port=settings.mqtt_broker_port,
            topic=settings.mqtt_topic,
            qos=settings.mqtt_qos,
        )

    def connect(self) -> None:
        """Connect to MQTT broker and start network loop."""
        self._loop = asyncio.get_running_loop()
        self._shutdown_requested = False
        try:
            self._replace_client_and_start()
        except Exception as exc:
            logger.error("Failed to connect to MQTT broker", error=str(exc))
            self._schedule_reconnect()

    def disconnect(self) -> None:
        """Disconnect from MQTT broker and stop reconnect loop."""
        self._shutdown_requested = True
        self._connected = False

        if self._loop and self._reconnect_task:
            self._loop.call_soon_threadsafe(self._reconnect_task.cancel)

        self._close_client()

        logger.info("MQTT client disconnected")

    def _build_client(self) -> None:
        self.client = mqtt.Client(
            client_id=f"data-service-{uuid.uuid4().hex[:8]}",
            clean_session=settings.mqtt_clean_session,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        if settings.mqtt_username and settings.mqtt_password:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def _close_client(self) -> None:
        with self._client_lock:
            client = self.client
            self.client = None

        if client is None:
            return

        try:
            client.loop_stop()
        except Exception as exc:
            logger.warning("Error stopping MQTT loop", error=str(exc))

        try:
            client.disconnect()
        except Exception as exc:
            logger.warning("Error disconnecting MQTT client", error=str(exc))

    def _replace_client_and_start(self) -> None:
        self._close_client()
        with self._client_lock:
            self._build_client()
            client = self.client

        if client is None:
            raise RuntimeError("MQTT client not initialized")

        client.connect(
            host=settings.mqtt_broker_host,
            port=settings.mqtt_broker_port,
            keepalive=settings.mqtt_keepalive,
        )
        client.loop_start()
        logger.info(
            "MQTT client connecting",
            host=settings.mqtt_broker_host,
            port=settings.mqtt_broker_port,
        )

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Dict[str, Any],
        rc: int,
    ) -> None:
        if rc != 0:
            self._connected = False
            logger.error("Failed to connect to MQTT broker", return_code=rc)
            self._schedule_reconnect()
            return

        self._connected = True
        self._connect_attempt = 0
        logger.info("Connected to MQTT broker", host=settings.mqtt_broker_host, port=settings.mqtt_broker_port)

        topics = self._subscription_topics()
        result, mid = client.subscribe(topics if len(topics) > 1 else topics[0])
        if result == mqtt.MQTT_ERR_SUCCESS:
            logger.info(
                "Subscribed to MQTT topic",
                topic=[topic for topic, _ in topics],
                qos=settings.mqtt_qos,
                message_id=mid,
            )
        else:
            logger.error("Failed to subscribe to topic", topic=[topic for topic, _ in topics], result=result)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int) -> None:
        self._connected = False

        if rc == 0:
            logger.info("MQTT client disconnected cleanly")
            return

        if self._shutdown_requested:
            return

        logger.warning("Unexpected MQTT disconnection", return_code=rc)
        self._schedule_reconnect()

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        granted_qos: list,
    ) -> None:
        logger.info("MQTT subscription acknowledged", message_id=mid, granted_qos=granted_qos)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        correlation_id = str(uuid.uuid4())
        logger.debug(
            "MQTT message received",
            topic=msg.topic,
            qos=msg.qos,
            payload_size=len(msg.payload),
            correlation_id=correlation_id,
        )

        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.error(
                "Failed to parse MQTT message payload",
                topic=msg.topic,
                error=str(exc),
                correlation_id=correlation_id,
            )
            return

        topic_tenant_id, topic_device_id = self._extract_device_id_from_topic(msg.topic)
        if not topic_device_id:
            logger.warning(
                "Dropping telemetry message with invalid topic format",
                topic=msg.topic,
                correlation_id=correlation_id,
            )
            return

        payload_device_id = payload.get("device_id")
        if payload_device_id is None:
            payload["device_id"] = topic_device_id
        elif str(payload_device_id).strip() != topic_device_id:
            logger.warning(
                "Dropping telemetry due to topic/payload device_id mismatch",
                topic=msg.topic,
                topic_device_id=topic_device_id,
                payload_device_id=payload_device_id,
                correlation_id=correlation_id,
            )
            return

        resolved_tenant_id = self._resolve_tenant_id(payload, topic_tenant_id)
        if resolved_tenant_id is None:
            logger.info(
                "Telemetry received without explicit tenant scope; deferring resolution to enrichment",
                topic=msg.topic,
                correlation_id=correlation_id,
            )

        if not self.telemetry_service or self._loop is None:
            logger.warning("Telemetry service unavailable; dropping message", correlation_id=correlation_id)
            return

        fut = asyncio.run_coroutine_threadsafe(
            self.telemetry_service.process_telemetry_message(
                raw_payload=payload,
                correlation_id=correlation_id,
            ),
            self._loop,
        )
        fut.add_done_callback(self._log_processing_error)

    def _log_processing_error(self, fut: Future) -> None:
        try:
            fut.result()
        except Exception as exc:
            logger.error("Telemetry processing task failed", error=str(exc))

    def _schedule_reconnect(self) -> None:
        if self._shutdown_requested or self._loop is None:
            return

        def _ensure_task() -> None:
            if self._reconnect_task and not self._reconnect_task.done():
                return
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

        self._loop.call_soon_threadsafe(_ensure_task)

    async def _reconnect_loop(self) -> None:
        """Reconnect indefinitely with exponential backoff and jitter."""
        while not self._shutdown_requested and not self._connected:
            self._connect_attempt += 1
            wait_seconds = min(
                self._reconnect_interval * (2 ** (self._connect_attempt - 1)),
                60,
            )
            wait_seconds += random.uniform(0, 1.0)

            logger.info(
                "Attempting MQTT reconnect",
                attempt=self._connect_attempt,
                wait_time=round(wait_seconds, 2),
            )
            await asyncio.sleep(wait_seconds)

            if self._shutdown_requested:
                return

            try:
                self._replace_client_and_start()
                await asyncio.sleep(1.0)
                if self._connected:
                    logger.info("MQTT reconnected successfully")
                    return
            except Exception as exc:
                logger.warning("MQTT reconnect attempt failed", attempt=self._connect_attempt, error=str(exc))

    @property
    def is_connected(self) -> bool:
        return self._connected
