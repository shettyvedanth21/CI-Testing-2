import test from "node:test";
import assert from "node:assert/strict";

import { buildMqttProvisioningQrPayload, stringifyMqttProvisioningQrPayload } from "../../lib/mqttProvisioning.ts";

test("buildMqttProvisioningQrPayload maps the onboarding bundle to the QR contract", () => {
  const payload = buildMqttProvisioningQrPayload({
    broker_host: "broker.factory.local",
    broker_port: 1883,
    tenant_id: "SH00000001",
    device_id: "AD00000001",
    username: "device:SH00000001:AD00000001",
    password: "one-time-secret",
    publish_topic: "SH00000001/devices/AD00000001/telemetry",
    status_topic: "SH00000001/devices/AD00000001/status",
    subscribe_topics: [
      "SH00000001/devices/AD00000001/cmd",
      "SH00000001/devices/AD00000001/config",
      "SH00000001/devices/AD00000001/ota",
    ],
  });

  assert.deepEqual(payload, {
    version: 1,
    broker: "broker.factory.local",
    port: 1883,
    tenant_id: "SH00000001",
    device_id: "AD00000001",
    username: "device:SH00000001:AD00000001",
    password: "one-time-secret",
    topic: "SH00000001/devices/AD00000001/telemetry",
    status_topic: "SH00000001/devices/AD00000001/status",
    subscribe_topics: [
      "SH00000001/devices/AD00000001/cmd",
      "SH00000001/devices/AD00000001/config",
      "SH00000001/devices/AD00000001/ota",
    ],
  });
});

test("stringifyMqttProvisioningQrPayload returns a stable JSON string", () => {
  const payload = stringifyMqttProvisioningQrPayload({
    broker_host: "broker.factory.local",
    broker_port: 1883,
    tenant_id: "SH00000001",
    device_id: "AD00000001",
    username: "device:SH00000001:AD00000001",
    password: "one-time-secret",
    publish_topic: "SH00000001/devices/AD00000001/telemetry",
    status_topic: "SH00000001/devices/AD00000001/status",
    subscribe_topics: [
      "SH00000001/devices/AD00000001/cmd",
      "SH00000001/devices/AD00000001/config",
      "SH00000001/devices/AD00000001/ota",
    ],
  });

  assert.equal(
    payload,
    JSON.stringify({
      version: 1,
      broker: "broker.factory.local",
      port: 1883,
      tenant_id: "SH00000001",
      device_id: "AD00000001",
      username: "device:SH00000001:AD00000001",
      password: "one-time-secret",
      topic: "SH00000001/devices/AD00000001/telemetry",
      status_topic: "SH00000001/devices/AD00000001/status",
      subscribe_topics: [
        "SH00000001/devices/AD00000001/cmd",
        "SH00000001/devices/AD00000001/config",
        "SH00000001/devices/AD00000001/ota",
      ],
    }),
  );
});
