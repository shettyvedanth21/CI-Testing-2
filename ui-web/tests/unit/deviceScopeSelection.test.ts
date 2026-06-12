import test from "node:test";
import assert from "node:assert/strict";

import type { PlantProfile } from "../../lib/authApi.ts";
import type { Device } from "../../lib/deviceApi.ts";
import {
  buildDeviceScopeCatalog,
  getDeviceScopeSummary,
  normalizeDeviceScopeSelection,
  resolveDeviceIdsForSelection,
} from "../../lib/deviceScopeSelection.ts";
import { resolveVisiblePlants } from "../../lib/orgScope.ts";

const plants: PlantProfile[] = [
  {
    id: "plant-a",
    tenant_id: "SH00000001",
    name: "Plant A",
    location: null,
    timezone: "Asia/Kolkata",
    is_active: true,
    created_at: "2026-04-06T00:00:00Z",
  },
  {
    id: "plant-b",
    tenant_id: "SH00000001",
    name: "Plant B",
    location: null,
    timezone: "Asia/Kolkata",
    is_active: true,
    created_at: "2026-04-06T00:00:00Z",
  },
  {
    id: "plant-c",
    tenant_id: "SH00000001",
    name: "Plant C",
    location: null,
    timezone: "Asia/Kolkata",
    is_active: true,
    created_at: "2026-04-06T00:00:00Z",
  },
];

const devices: Device[] = [
  {
    id: "A1",
    name: "Plant A",
    type: "compressor",
    plant_id: "plant-a",
    status: "active",
    runtime_status: "running",
    first_telemetry_timestamp: null,
    last_seen_timestamp: null,
    location: "",
  },
  {
    id: "A2",
    name: "Plant A",
    type: "compressor",
    plant_id: "plant-a",
    status: "active",
    runtime_status: "running",
    first_telemetry_timestamp: null,
    last_seen_timestamp: null,
    location: "",
  },
  {
    id: "01KNH6PVQW023A92HTYTFB9F7X",
    name: "Validation Device",
    type: "compressor",
    plant_id: "plant-b",
    status: "active",
    runtime_status: "running",
    first_telemetry_timestamp: null,
    last_seen_timestamp: null,
    location: "",
  },
];

test("org admin scope catalog shows all plants with correct device counts", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  assert.deepEqual(
    catalog.plantOptions.map((plant) => [plant.name, plant.deviceCount]),
    [
      ["Plant A", 2],
      ["Plant B", 1],
      ["Plant C", 0],
    ],
  );
  assert.deepEqual(resolveDeviceIdsForSelection({ mode: "all", plantId: null, deviceIds: [] }, catalog), [
    "A1",
    "A2",
    "01KNH6PVQW023A92HTYTFB9F7X",
  ]);
});

test("plant-scoped roles only see assigned plants and devices", () => {
  const me = {
    user: { role: "plant_manager" },
    plant_ids: ["plant-a", "plant-b"],
  } as { user: { role: string }; plant_ids: string[] };

  const visiblePlants = resolveVisiblePlants(me as never, plants);
  const catalog = buildDeviceScopeCatalog(devices, visiblePlants);

  assert.deepEqual(catalog.plantOptions.map((plant) => plant.name), ["Plant A", "Plant B"]);
  assert.deepEqual(catalog.deviceOptions.map((device) => device.id), [
    "A1",
    "A2",
    "01KNH6PVQW023A92HTYTFB9F7X",
  ]);
});

test("duplicate names are visually distinguishable and generated ids are surfaced", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  const duplicateOption = catalog.deviceOptions.find((device) => device.id === "A1");
  const generatedOption = catalog.deviceOptions.find((device) => device.id === "01KNH6PVQW023A92HTYTFB9F7X");

  assert.match(duplicateOption?.label ?? "", /Plant A \(A1\)/);
  assert.match(duplicateOption?.description ?? "", /Plant A · A1/);
  assert.match(generatedOption?.description ?? "", /Plant B · 01KNH6PVQW023A92HTYTFB9F7X/);
});

test("scope selection resolves all, plant, and machine modes consistently", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  assert.deepEqual(
    resolveDeviceIdsForSelection({ mode: "all", plantId: null, deviceIds: [] }, catalog),
    ["A1", "A2", "01KNH6PVQW023A92HTYTFB9F7X"],
  );
  assert.deepEqual(
    resolveDeviceIdsForSelection({ mode: "plant", plantId: "plant-a", deviceIds: [] }, catalog),
    ["A1", "A2"],
  );
  assert.deepEqual(
    resolveDeviceIdsForSelection({ mode: "devices", plantId: null, deviceIds: ["A2", "01KNH6PVQW023A92HTYTFB9F7X"] }, catalog),
    ["A2", "01KNH6PVQW023A92HTYTFB9F7X"],
  );
  assert.equal(
    getDeviceScopeSummary({ mode: "plant", plantId: "plant-b", deviceIds: [] }, catalog),
    "Plant B · 1 device",
  );
});

test("invalid plant and stale device selections are normalized safely", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  assert.deepEqual(
    normalizeDeviceScopeSelection({ mode: "plant", plantId: "missing", deviceIds: [] }, catalog),
    { mode: "plant", plantId: "plant-a", deviceIds: [] },
  );
  assert.deepEqual(
    normalizeDeviceScopeSelection({ mode: "devices", plantId: null, deviceIds: ["A1", "ghost"] }, catalog),
    { mode: "devices", plantId: null, deviceIds: ["A1"] },
  );
});
