# Shivex MQTT Auth Production Rollout Contract

This document defines the production MQTT contract for device publishers.

- device publishers use MQTT over plain TCP on `1883`
- username/password authentication remains mandatory
- MySQL-backed EMQX authentication and authorization remain mandatory
- anonymous MQTT access remains disabled
- topic isolation remains enforced by ACLs

## 1. Proven Baseline

The repository now encodes and validates this behavior:

- device MQTT credentials are stored in MySQL in `device_mqtt_credentials`
- per-device ACL intent is stored in MySQL in `device_mqtt_acl`
- EMQX on `1883` authenticates username/password against MySQL
- EMQX authorization on `1883` is default-deny with disconnect on unauthorized publish
- the canonical telemetry publish topic is `<tenant_id>/devices/<device_id>/telemetry`
- the canonical status publish topic is `<tenant_id>/devices/<device_id>/status`
- the canonical control subscribe topics are:
  - `<tenant_id>/devices/<device_id>/cmd`
  - `<tenant_id>/devices/<device_id>/config`
  - `<tenant_id>/devices/<device_id>/ota`
- the compose-managed simulator publishes on `1883` with a per-device credential
- `data-service` remains on `1883` and continues ingesting successfully

## 2. Firmware / Device Contract

Firmware source is not present in this repository. The device integration
contract below is derived from the validated simulator path and backend
credential model.

Each device must use:

- broker port: `1883`
- transport: MQTT over plain TCP
- broker authentication: MQTT username/password
- MQTT username format: `device:<tenant_id>:<device_id>`
- MQTT password source: one-time plaintext secret returned by Shivex during
  credential register or rotate
- telemetry publish topic: `<tenant_id>/devices/<device_id>/telemetry`
- status publish topic: `<tenant_id>/devices/<device_id>/status`
- subscribe topics:
  - `<tenant_id>/devices/<device_id>/cmd`
  - `<tenant_id>/devices/<device_id>/config`
  - `<tenant_id>/devices/<device_id>/ota`
- payload requirement: include the same `device_id` as the topic device segment for telemetry payloads

Firmware must not:

- connect anonymously
- publish to another device topic
- subscribe to another device control topic
- assume TLS or certificate material is required for the normal Shivex device path
- expect Shivex to return the MQTT password again after registration/rotation

Expected broker-side failures:

- invalid password: MQTT connect rejected
- anonymous client: MQTT connect rejected
- valid login but wrong topic: publish denied and client disconnected
- valid login but unspecified control-topic subscription: denied and disconnected

## 3. Production EMQX Contract

Production EMQX must implement the same device lane used locally:

- `1883`: authenticated device publisher and backend subscriber lane

Production EMQX must implement:

- MySQL password-based authentication against `device_mqtt_credentials`
- SHA-256 password verification with no salt column
- ACL lookup against `device_mqtt_acl`
- anonymous access denied
- `no_match = deny`
- `deny_action = disconnect`

Required auth query shape:

```sql
SELECT password_hash, '' AS salt, 0 AS is_superuser
FROM device_mqtt_credentials
WHERE mqtt_username = ${username} AND is_active = 1
LIMIT 1
```

Required ACL query shape:

```sql
SELECT permission, access AS action, topic
FROM device_mqtt_acl
WHERE mqtt_username = ${username} AND is_active = 1
ORDER BY id ASC
```

## 4. Production Config Inputs

These values must be supplied in production infrastructure:

- production MySQL host
- production MySQL port
- production MySQL database
- EMQX MySQL username and password for auth/ACL queries
- production listener hostname and DNS for the app host if needed

These current repo values are dev-only and must not be reused in production:

- `EMQX_ALLOW_ANONYMOUS=true` in `.env.local`
- MySQL credentials `energy` / `energy` from local compose
- simulator bootstrap admin email/password from `.env.local`
- local compose mount of `ops/emqx/local.base.hocon`

## 5. Safe Rollout Order

1. Prepare production EMQX `1883` with MySQL auth/ACL connectivity.
2. Confirm `device-service` credential APIs and tables are present in the target production database.
3. Create a canary device credential through Shivex.
4. Update one canary firmware/device client to use `1883`, `device:<tenant_id>:<device_id>` username, and the one-time MQTT secret.
5. Confirm the canary can publish only to its canonical telemetry/status topics, subscribe only to its control topics, and that telemetry is ingested end-to-end.
6. Roll out additional devices in small cohorts.

## 6. Rollback Order

1. Stop onboarding new devices for the affected cohort.
2. Revert the affected device cohort to the previous publishing configuration if needed.
3. Leave MySQL-backed auth/ACL config intact unless the issue is isolated to broker configuration.
4. Do not delete device credential rows during initial rollback; first stabilize the publishing path, then decide whether any credentials need revocation or rotation.

## 7. Post-Deploy Smoke Checklist

For each rollout wave, verify:

- EMQX `1883` listener is healthy
- canary device authenticates successfully
- canary device publishes to its own canonical telemetry topic successfully
- canary device publishes to its own canonical status topic successfully
- canary device subscribes to its own command/config/OTA topics successfully
- wrong password is rejected
- anonymous client is rejected
- wrong topic publish is denied and disconnected
- wrong control-topic subscription is denied and disconnected
- latest telemetry for the canary device is visible through Shivex
- `data-service` remains healthy and its current MQTT path remains connected
