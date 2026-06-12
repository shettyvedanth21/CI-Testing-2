import type { PlantProfile } from "./authApi.ts";
import type { Device } from "./deviceApi.ts";
import { areAllSelectableDevicesSelected, normalizeSelectedDeviceIds } from "./deviceSelection.ts";

export type DeviceScopeMode = "all" | "plant" | "devices";

export interface DeviceScopeSelection {
  mode: DeviceScopeMode;
  plantId: string | null;
  deviceIds: string[];
}

export interface PlantScopeOption {
  id: string;
  name: string;
  deviceCount: number;
  deviceIds: string[];
  label: string;
}

export interface DeviceScopeOption {
  id: string;
  name: string;
  label: string;
  description: string;
  plantId: string | null;
  plantName: string;
}

export interface DeviceScopeCatalog {
  plantOptions: PlantScopeOption[];
  deviceOptions: DeviceScopeOption[];
  allDeviceIds: string[];
}

const UNKNOWN_PLANT_LABEL = "Unassigned Plant";

function pluralizeDevices(count: number): string {
  return `${count} device${count === 1 ? "" : "s"}`;
}

function sortByName<T extends { name: string }>(items: T[]): T[] {
  return [...items].sort((left, right) => left.name.localeCompare(right.name));
}

export function buildDeviceScopeCatalog(
  devices: Device[],
  plants: PlantProfile[],
): DeviceScopeCatalog {
  const sortedDevices = [...devices].sort((left, right) => {
    const nameCompare = (left.name || left.id).localeCompare(right.name || right.id);
    if (nameCompare !== 0) {
      return nameCompare;
    }
    return left.id.localeCompare(right.id);
  });
  const plantNameById = new Map(plants.map((plant) => [plant.id, plant.name]));
  const duplicateNameCounts = new Map<string, number>();

  for (const device of sortedDevices) {
    const key = device.name.trim().toLowerCase() || device.id.toLowerCase();
    duplicateNameCounts.set(key, (duplicateNameCounts.get(key) ?? 0) + 1);
  }

  const deviceIdsByPlant = new Map<string, string[]>();
  for (const device of sortedDevices) {
    if (!device.plant_id) {
      continue;
    }
    const rows = deviceIdsByPlant.get(device.plant_id) ?? [];
    rows.push(device.id);
    deviceIdsByPlant.set(device.plant_id, rows);
  }

  const plantOptions = sortByName(plants).map((plant) => {
    const deviceIds = deviceIdsByPlant.get(plant.id) ?? [];
    return {
      id: plant.id,
      name: plant.name,
      deviceCount: deviceIds.length,
      deviceIds,
      label: `${plant.name} · ${pluralizeDevices(deviceIds.length)}`,
    };
  });

  const deviceOptions = sortedDevices.map((device) => {
    const duplicateKey = device.name.trim().toLowerCase() || device.id.toLowerCase();
    const plantName = device.plant_id ? (plantNameById.get(device.plant_id) ?? UNKNOWN_PLANT_LABEL) : UNKNOWN_PLANT_LABEL;
    const label =
      (duplicateNameCounts.get(duplicateKey) ?? 0) > 1 || !device.name.trim()
        ? `${device.name || device.id} (${device.id})`
        : device.name;

    return {
      id: device.id,
      name: device.name,
      label,
      description: `${plantName} · ${device.id}`,
      plantId: device.plant_id ?? null,
      plantName,
    };
  });

  return {
    plantOptions,
    deviceOptions,
    allDeviceIds: sortedDevices.map((device) => device.id),
  };
}

export function normalizeDeviceScopeSelection(
  selection: DeviceScopeSelection,
  catalog: DeviceScopeCatalog,
): DeviceScopeSelection {
  if (selection.mode === "all") {
    return {
      mode: "all",
      plantId: null,
      deviceIds: [],
    };
  }

  if (selection.mode === "plant") {
    const validPlantId = catalog.plantOptions.some((plant) => plant.id === selection.plantId)
      ? selection.plantId
      : (catalog.plantOptions[0]?.id ?? null);
    return {
      mode: "plant",
      plantId: validPlantId,
      deviceIds: [],
    };
  }

  return {
    mode: "devices",
    plantId: null,
    deviceIds: normalizeSelectedDeviceIds(selection.deviceIds, catalog.allDeviceIds),
  };
}

export function resolveDeviceIdsForSelection(
  selection: DeviceScopeSelection,
  catalog: DeviceScopeCatalog,
): string[] {
  const normalized = normalizeDeviceScopeSelection(selection, catalog);

  if (normalized.mode === "all") {
    return catalog.allDeviceIds;
  }

  if (normalized.mode === "plant") {
    return catalog.plantOptions.find((plant) => plant.id === normalized.plantId)?.deviceIds ?? [];
  }

  return normalized.deviceIds;
}

export function getDeviceScopeSummary(
  selection: DeviceScopeSelection,
  catalog: DeviceScopeCatalog,
): string {
  const normalized = normalizeDeviceScopeSelection(selection, catalog);
  const selectedDeviceIds = resolveDeviceIdsForSelection(normalized, catalog);

  if (normalized.mode === "all") {
    return `All Machines · ${pluralizeDevices(selectedDeviceIds.length)}`;
  }

  if (normalized.mode === "plant") {
    const plant = catalog.plantOptions.find((option) => option.id === normalized.plantId);
    if (!plant) {
      return "Select a plant";
    }
    return `${plant.name} · ${pluralizeDevices(selectedDeviceIds.length)}`;
  }

  return `Selected Machines · ${pluralizeDevices(selectedDeviceIds.length)}`;
}

export function hasCompleteDeviceSelection(
  selection: DeviceScopeSelection,
  catalog: DeviceScopeCatalog,
): boolean {
  return areAllSelectableDevicesSelected(
    resolveDeviceIdsForSelection(selection, catalog),
    catalog.allDeviceIds,
  );
}
