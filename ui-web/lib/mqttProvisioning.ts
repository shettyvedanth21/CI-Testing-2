import type { DeviceMqttProvisioningBundle } from "./deviceApi";

export interface MqttProvisioningQrPayload {
  version: 1;
  broker: string;
  port: 1883;
  tenant_id: string;
  device_id: string;
  username: string;
  password: string;
  topic: string;
  status_topic: string;
  subscribe_topics: string[];
}

export function buildMqttProvisioningQrPayload(
  provisioning: DeviceMqttProvisioningBundle,
): MqttProvisioningQrPayload {
  return {
    version: 1,
    broker: provisioning.broker_host,
    port: 1883,
    tenant_id: provisioning.tenant_id,
    device_id: provisioning.device_id,
    username: provisioning.username,
    password: provisioning.password,
    topic: provisioning.publish_topic,
    status_topic: provisioning.status_topic,
    subscribe_topics: provisioning.subscribe_topics,
  };
}

export function stringifyMqttProvisioningQrPayload(
  provisioning: DeviceMqttProvisioningBundle,
): string {
  return JSON.stringify(buildMqttProvisioningQrPayload(provisioning));
}
