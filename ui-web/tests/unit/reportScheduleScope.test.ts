import test from "node:test";
import assert from "node:assert/strict";

import type { PlantProfile } from "../../lib/authApi.ts";
import type { Device } from "../../lib/deviceApi.ts";
import { buildDeviceScopeCatalog } from "../../lib/deviceScopeSelection.ts";
import { resolveVisiblePlants } from "../../lib/orgScope.ts";
import { buildReportScheduleParams } from "../../lib/reportScheduleScope.ts";

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
  report_type: "consumption" as const,
  frequency: "daily" as const,
  group_by: "daily" as const,
};

test("main reports schedule payload resolves all-machine scope", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  const params = buildReportScheduleParams(baseForm, { mode: "all", plantId: null, deviceIds: [] }, catalog);

  assert.deepEqual(params.params_template.device_ids, ["A1", "A2", "01KNH6PVQW023A92HTYTFB9F7X"]);
});

test("main reports schedule payload resolves plant scope to all accessible devices in that plant", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  const params = buildReportScheduleParams(baseForm, { mode: "plant", plantId: "plant-a", deviceIds: [] }, catalog);

  assert.deepEqual(params.params_template.device_ids, ["A1", "A2"]);
});

test("main reports schedule payload resolves explicit machine selection only", () => {
  const catalog = buildDeviceScopeCatalog(devices, plants);

  const params = buildReportScheduleParams(
    baseForm,
    { mode: "devices", plantId: null, deviceIds: ["A2", "01KNH6PVQW023A92HTYTFB9F7X"] },
    catalog,
  );

  assert.deepEqual(params.params_template.device_ids, ["A2", "01KNH6PVQW023A92HTYTFB9F7X"]);
});

test("plant managers only resolve schedule payloads from assigned plants", () => {
  const me = {
    user: { role: "plant_manager" },
    plant_ids: ["plant-b"],
  } as { user: { role: string }; plant_ids: string[] };
  const visiblePlants = resolveVisiblePlants(me as never, plants);
  const visiblePlantIds = new Set(visiblePlants.map((plant) => plant.id));
  const visibleDevices = devices.filter((device) => device.plant_id && visiblePlantIds.has(device.plant_id));
  const catalog = buildDeviceScopeCatalog(visibleDevices, visiblePlants);

  const params = buildReportScheduleParams(baseForm, { mode: "all", plantId: null, deviceIds: [] }, catalog);

  assert.deepEqual(params.params_template.device_ids, ["01KNH6PVQW023A92HTYTFB9F7X"]);
});
