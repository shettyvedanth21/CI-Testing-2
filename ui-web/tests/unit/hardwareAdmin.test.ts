import test from "node:test";
import assert from "node:assert/strict";

import {
  buildAdminOrgTabs,
  buildHardwareUnitUpdatePayload,
  buildInstallableDeviceOptions,
  buildInventoryRows,
  filterInstallationHistory,
  flattenDeviceHistory,
  getHardwareUnitStatusLabel,
  getHardwareUnitTypeLabel,
  getInstallationRoleLabel,
  getPlantName,
  HARDWARE_UNIT_TYPE_OPTIONS,
  INSTALLATION_ROLE_OPTIONS,
  isAllowedHardwareUnitType,
} from "../../lib/hardwareAdmin.ts";

test("buildAdminOrgTabs adds hardware tab with live inventory count", () => {
  assert.deepEqual(buildAdminOrgTabs({ plants: 2, users: 1, hardware: 5 }), [
    { key: "plants", label: "Plants", count: 2 },
    { key: "users", label: "Org Admins", count: 1 },
    { key: "hardware", label: "Hardware", count: 5 },
  ]);
});

test("buildAdminOrgTabs appends notification usage tab for super admin org detail", () => {
  assert.deepEqual(
    buildAdminOrgTabs({
      plants: 2,
      users: 1,
      hardware: 5,
      includeNotificationUsage: true,
      notificationUsage: 12,
    }),
    [
      { key: "plants", label: "Plants", count: 2 },
      { key: "users", label: "Org Admins", count: 1 },
      { key: "hardware", label: "Hardware", count: 5 },
      { key: "notification_usage", label: "Notification Usage", count: 12 },
    ],
  );
});

test("buildInventoryRows resolves active device assignment and plant filter", () => {
  const rows = buildInventoryRows({
    hardwareUnits: [
      {
        id: 1,
        hardware_unit_id: "HW-ESP-001",
        tenant_id: "ORG-1",
        plant_id: "PLANT-1",
        unit_type: "esp32",
        unit_name: "ESP32 Main",
        manufacturer: "Espressif",
        model: "WROOM",
        serial_number: "SN-1",
        status: "available",
        created_at: "2026-04-07T00:00:00Z",
        updated_at: "2026-04-07T00:00:00Z",
      },
      {
        id: 2,
        hardware_unit_id: "HW-CT-001",
        tenant_id: "ORG-1",
        plant_id: "PLANT-2",
        unit_type: "ct_sensor",
        unit_name: "CT1",
        manufacturer: null,
        model: null,
        serial_number: null,
        status: "available",
        created_at: "2026-04-07T00:00:00Z",
        updated_at: "2026-04-07T00:00:00Z",
      },
    ],
    devices: [
      {
        id: "AD00000001",
        name: "Compressor 01",
        type: "compressor",
        device_id_class: "active",
        plant_id: "PLANT-1",
        data_source_type: "metered",
        status: "active",
        runtime_status: "running",
        first_telemetry_timestamp: null,
        last_seen_timestamp: null,
        location: "",
      },
    ],
    plants: [
      { id: "PLANT-1", tenant_id: "ORG-1", name: "Plant One", location: null, timezone: "UTC", is_active: true, created_at: "2026-04-07T00:00:00Z" },
      { id: "PLANT-2", tenant_id: "ORG-1", name: "Plant Two", location: null, timezone: "UTC", is_active: true, created_at: "2026-04-07T00:00:00Z" },
    ],
    installations: [
      {
        id: 11,
        tenant_id: "ORG-1",
        plant_id: "PLANT-1",
        device_id: "AD00000001",
        hardware_unit_id: "HW-ESP-001",
        installation_role: "controller",
        commissioned_at: "2026-04-07T09:00:00Z",
        decommissioned_at: null,
        is_active: true,
        notes: null,
        created_at: "2026-04-07T09:00:00Z",
        updated_at: "2026-04-07T09:00:00Z",
      },
    ],
    plantFilter: "PLANT-1",
  });

  assert.equal(rows.length, 1);
  assert.equal(rows[0]?.plantName, "Plant One");
  assert.equal(rows[0]?.unitTypeLabel, "ESP32");
  assert.equal(rows[0]?.statusLabel, "In Inventory");
  assert.equal(rows[0]?.hardwareUnit.unit_name, "ESP32 Main");
  assert.equal(rows[0]?.currentDevice?.id, "AD00000001");
  assert.equal(rows[0]?.currentInstallationRoleLabel, "Controller");
});

test("buildInstallableDeviceOptions keeps generated device ids visible in selectors", () => {
  const options = buildInstallableDeviceOptions(
    [
      {
        id: "AD00000001",
        name: "Compressor 01",
        type: "compressor",
        device_id_class: "active",
        plant_id: "PLANT-1",
        data_source_type: "metered",
        status: "active",
        runtime_status: "running",
        first_telemetry_timestamp: null,
        last_seen_timestamp: null,
        location: "",
      },
      {
        id: "TD00000001",
        name: "Test Rig",
        type: "compressor",
        device_id_class: "test",
        plant_id: "PLANT-2",
        data_source_type: "metered",
        status: "active",
        runtime_status: "running",
        first_telemetry_timestamp: null,
        last_seen_timestamp: null,
        location: "",
      },
    ],
    "PLANT-1",
  );

  assert.deepEqual(options, [{ value: "AD00000001", label: "AD00000001 · Compressor 01" }]);
});

test("controlled hardware values expose readable labels and preserve backend values", () => {
  assert.equal(getHardwareUnitTypeLabel("temperature_sensor"), "Temperature Sensor");
  assert.equal(getHardwareUnitStatusLabel("available"), "In Inventory");
  assert.equal(isAllowedHardwareUnitType("esp32"), true);
  assert.equal(isAllowedHardwareUnitType("sensor 1"), false);
  assert.equal(getInstallationRoleLabel("main_meter"), "Main Meter");
  assert.equal(HARDWARE_UNIT_TYPE_OPTIONS.find((option) => option.label === "ESP32")?.value, "esp32");
  assert.equal(INSTALLATION_ROLE_OPTIONS.find((option) => option.label === "CT4")?.value, "ct4");
});

test("buildHardwareUnitUpdatePayload only sends changed fields so legacy saved types do not block status-only edits", () => {
  const payload = buildHardwareUnitUpdatePayload(
    {
      id: 1,
      hardware_unit_id: "HWU00000005",
      tenant_id: "ORG-1",
      plant_id: "PLANT-2",
      unit_type: "sensor 1",
      unit_name: "t3",
      manufacturer: "g64",
      model: "g6",
      serial_number: "g56",
      status: "retired",
      created_at: "2026-04-07T00:00:00Z",
      updated_at: "2026-04-07T00:00:00Z",
    },
    {
      plant_id: "PLANT-2",
      unit_type: "sensor 1",
      unit_name: "t3",
      manufacturer: "g64",
      model: "g6",
      serial_number: "g56",
      status: "available",
    },
  );

  assert.deepEqual(payload, { status: "available" });
});

test("plant names resolve to readable labels in hardware views", () => {
  const plantName = getPlantName(
    [
      { id: "PLANT-1", tenant_id: "ORG-1", name: "Plant One", location: null, timezone: "UTC", is_active: true, created_at: "2026-04-07T00:00:00Z" },
    ],
    "PLANT-1",
  );

  assert.equal(plantName, "Plant One");
});

test("flattenDeviceHistory removes duplicates and filterInstallationHistory respects state and audit filters", () => {
  const flattened = flattenDeviceHistory({
    AD00000001: [
      {
        id: 11,
        tenant_id: "ORG-1",
        plant_id: "PLANT-1",
        device_id: "AD00000001",
        hardware_unit_id: "HW-ESP-001",
        installation_role: "controller",
        commissioned_at: "2026-04-07T09:00:00Z",
        decommissioned_at: "2026-04-08T09:00:00Z",
        is_active: false,
        notes: "replaced",
        created_at: "2026-04-07T09:00:00Z",
        updated_at: "2026-04-08T09:00:00Z",
      },
    ],
    AD00000002: [
      {
        id: 12,
        tenant_id: "ORG-1",
        plant_id: "PLANT-2",
        device_id: "AD00000002",
        hardware_unit_id: "HW-CT-001",
        installation_role: "current_sensor",
        commissioned_at: "2026-04-09T09:00:00Z",
        decommissioned_at: null,
        is_active: true,
        notes: null,
        created_at: "2026-04-09T09:00:00Z",
        updated_at: "2026-04-09T09:00:00Z",
      },
      {
        id: 11,
        tenant_id: "ORG-1",
        plant_id: "PLANT-1",
        device_id: "AD00000001",
        hardware_unit_id: "HW-ESP-001",
        installation_role: "controller",
        commissioned_at: "2026-04-07T09:00:00Z",
        decommissioned_at: "2026-04-08T09:00:00Z",
        is_active: false,
        notes: "replaced",
        created_at: "2026-04-07T09:00:00Z",
        updated_at: "2026-04-08T09:00:00Z",
      },
    ],
  });

  assert.equal(flattened.length, 2);
  assert.equal(flattened[0]?.id, 12);

  const activeOnly = filterInstallationHistory(flattened, {
    plantId: "PLANT-2",
    state: "active",
  });
  assert.deepEqual(activeOnly.map((item) => item.id), [12]);

  const decommissionedByHardware = filterInstallationHistory(flattened, {
    hardwareUnitId: "HW-ESP-001",
    state: "decommissioned",
  });
  assert.deepEqual(decommissionedByHardware.map((item) => item.id), [11]);
});
