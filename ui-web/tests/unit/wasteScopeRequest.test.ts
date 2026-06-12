import test from "node:test";
import assert from "node:assert/strict";

import type { PlantProfile } from "../../lib/authApi.ts";
import type { Device } from "../../lib/deviceApi.ts";
import { buildDeviceScopeCatalog } from "../../lib/deviceScopeSelection.ts";
import { resolveVisiblePlants } from "../../lib/orgScope.ts";
import { resolvePresetRange } from "../../lib/reportDateRange.ts";
import { buildWasteRunParams } from "../../lib/wasteScopeRequest.ts";

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
];

const devices: Device[] = [
  {
    id: "A1",
    name: "Boiler",
    type: "boiler",
    plant_id: "plant-a",
    status: "active",
    runtime_status: "running",
    first_telemetry_timestamp: null,
    last_seen_timestamp: null,
    location: "",
  },
  {
    id: "A2",
    name: "Boiler",
    type: "boiler",
    plant_id: "plant-a",
    status: "active",
    runtime_status: "running",
    first_telemetry_timestamp: null,
    last_seen_timestamp: null,
    location: "",
  },
  {
    id: "01KNH6PVQW023A92HTYTFB9F7X",
    name: "Compressor Line A",
    type: "compressor",
    plant_id: "plant-b",
    status: "active",
    runtime_status: "running",
    first_telemetry_timestamp: null,
    last_seen_timestamp: null,
    location: "",
  },
];

const baseForm = {
  job_name: "Waste Validation",
  start_date: "2026-04-01",
  end_date: "2026-04-06",
  granularity: "daily" as const,
};

test("waste analysis all-machines scope preserves backend all-scope contract", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  const params = buildWasteRunParams(baseForm, { mode: "all", plantId: null, deviceIds: [] }, catalog);

  assert.equal(params.scope, "all");
  assert.equal(params.device_ids, null);
});

test("waste analysis plant scope resolves to selected device ids", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  const params = buildWasteRunParams(baseForm, { mode: "plant", plantId: "plant-a", deviceIds: [] }, catalog);

  assert.equal(params.scope, "selected");
  assert.deepEqual(params.device_ids, ["A1", "A2"]);
});

test("waste analysis machine scope resolves explicit device ids only", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  const params = buildWasteRunParams(
    baseForm,
    { mode: "devices", plantId: null, deviceIds: ["A2", "01KNH6PVQW023A92HTYTFB9F7X"] },
    catalog,
  );

  assert.equal(params.scope, "selected");
  assert.deepEqual(params.device_ids, ["A2", "01KNH6PVQW023A92HTYTFB9F7X"]);
});

test("plant-scoped roles only resolve accessible plant devices for waste analysis", () => {
  const me = {
    user: { role: "plant_manager" },
    plant_ids: ["plant-b"],
  } as { user: { role: string }; plant_ids: string[] };
  const visiblePlants = resolveVisiblePlants(me as never, plants);
  const visiblePlantIds = new Set(visiblePlants.map((plant) => plant.id));
  const visibleDevices = devices.filter((device) => device.plant_id && visiblePlantIds.has(device.plant_id));
  const catalog = buildDeviceScopeCatalog(visibleDevices, visiblePlants);

  const params = buildWasteRunParams(baseForm, { mode: "all", plantId: null, deviceIds: [] }, catalog);

  assert.equal(params.scope, "all");
  assert.equal(params.device_ids, null);
  assert.deepEqual(catalog.allDeviceIds, ["01KNH6PVQW023A92HTYTFB9F7X"]);
});

test("waste run params preserve selected shared date-range values", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);
  const selectedRange = resolvePresetRange(7, 0, new Date("2026-04-16T00:00:00Z"));

  const params = buildWasteRunParams(
    {
      ...baseForm,
      start_date: selectedRange.start,
      end_date: selectedRange.end,
    },
    { mode: "all", plantId: null, deviceIds: [] },
    catalog,
  );

  assert.equal(params.start_date, "2026-04-10");
  assert.equal(params.end_date, "2026-04-16");
});
