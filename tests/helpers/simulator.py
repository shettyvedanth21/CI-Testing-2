"""
MQTT telemetry publisher for live E2E tests.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


class TelemetrySimulator:
    TOPIC = "{tenant_id}/devices/{device_id}/telemetry"

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        device_id: str,
        tenant_id: str,
        mqtt_username: str | None = None,
        mqtt_password: str | None = None,
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.device_id = device_id
        self.tenant_id = tenant_id
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.topic = self.TOPIC.format(tenant_id=tenant_id, device_id=device_id)
        self._client = None

    def _connect(self):
        if self._client and self._client.is_connected():
            return
        self._client = mqtt.Client()
        if self.mqtt_username and self.mqtt_password:
            self._client.username_pw_set(self.mqtt_username, self.mqtt_password)
        self._client.connect(self.broker_host, self.broker_port, 60)
        self._client.loop_start()
        time.sleep(0.5)

    def _pub(self, payload: dict):
        self._connect()
        payload["device_id"] = self.device_id
        payload["tenant_id"] = self.tenant_id
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        payload["schema_version"] = "v1"
        info = self._client.publish(self.topic, json.dumps(payload), qos=1)
        info.wait_for_publish()
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with code {info.rc}")

    def send_normal(self, count: int = 5, interval_sec: float = 1.0):
        for i in range(count):
            self._pub(
                {
                    "voltage": 231.0 + (i % 3) * 0.5,
                    "current": 12.5 + (i % 2) * 0.3,
                    "power": 2875.0,
                    "power_factor": 0.97,
                    "energy_kwh": 1200.0 + i * 0.05,
                }
            )
            time.sleep(interval_sec)

    def send_idle(self, count: int = 5, interval_sec: float = 1.0):
        for i in range(count):
            self._pub(
                {
                    "voltage": 231.0,
                    "current": 0.7,
                    "power": 160.0,
                    "power_factor": 0.83,
                    "energy_kwh": 1200.0 + i * 0.001,
                }
            )
            time.sleep(interval_sec)

    def send_overconsumption(self, count: int = 5, interval_sec: float = 1.0):
        for i in range(count):
            self._pub(
                {
                    "voltage": 231.0,
                    "current": 26.0,
                    "power": 6006.0,
                    "power_factor": 0.97,
                    "energy_kwh": 1200.0 + i * 0.12,
                }
            )
            time.sleep(interval_sec)

    def send_spike(self, count: int = 3, interval_sec: float = 0.5):
        for i in range(count):
            self._pub(
                {
                    "voltage": 231.0,
                    "current": 35.0,
                    "power": 8085.0,
                    "power_factor": 0.97,
                    "energy_kwh": 1200.0 + i * 0.2,
                }
            )
            time.sleep(interval_sec)

    def send_bulk(self, count: int, mode: str = "normal", interval_sec: float = 0.15):
        fn = {
            "normal": self.send_normal,
            "idle": self.send_idle,
            "overconsumption": self.send_overconsumption,
        }.get(mode, self.send_normal)
        batches = max(1, count // 5)
        for _ in range(batches):
            fn(count=5, interval_sec=interval_sec)

    def disconnect(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
