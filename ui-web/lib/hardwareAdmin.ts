import type { Device } from "./deviceApi";
import type { PlantProfile } from "./authApi";
import type { DeviceHardwareInstallation, HardwareUnit, HardwareUnitUpdateInput } from "./hardwareApi";

export const HARDWARE_UNIT_TYPE_OPTIONS = [
  { value: "energy_meter", label: "Energy Meter" },
  { value: "ct_sensor", label: "CT Sensor" },
  { value: "esp32", label: "ESP32" },
  { value: "oil_sensor", label: "Oil Sensor" },
  { value: "temperature_sensor", label: "Temperature Sensor" },
  { value: "vibration_sensor", label: "Vibration Sensor" },
  { value: "motor_sensor", label: "Motor Sensor" },
] as const;

export const INSTALLATION_ROLE_OPTIONS = [
  { value: "main_meter", label: "Main Meter" },
  { value: "ct1", label: "CT1" },
  { value: "ct2", label: "CT2" },
  { value: "ct3", label: "CT3" },
  { value: "ct4", label: "CT4" },
  { value: "controller", label: "Controller" },
  { value: "oil_sensor", label: "Oil Sensor" },
  { value: "temperature_sensor", label: "Temperature Sensor" },
  { value: "vibration_sensor", label: "Vibration Sensor" },
  { value: "motor_sensor", label: "Motor Sensor" },
] as const;

export type AdminOrgTabKey = "plants" | "users" | "hardware" | "notification_usage";

export interface AdminOrgTab {
  key: AdminOrgTabKey;
  label: string;
  count: number;
}

export interface InventoryRow {
  hardwareUnit: HardwareUnit;
  plantName: string;
  unitTypeLabel: string;
  statusLabel: string;
  currentInstallation: DeviceHardwareInstallation | null;
  currentDevice: Device | null;
  currentInstallationRoleLabel: string | null;
}

const HARDWARE_UNIT_TYPE_LABELS = new Map<string, string>(
  HARDWARE_UNIT_TYPE_OPTIONS.map((option) => [option.value, option.label]),
);
const INSTALLATION_ROLE_LABELS = new Map<string, string>(
  INSTALLATION_ROLE_OPTIONS.map((option) => [option.value, option.label]),
);
const HARDWARE_STATUS_LABELS = new Map<string, string>([
  ["available", "In Inventory"],
  ["retired", "Retired"],
]);

export function getHardwareUnitTypeLabel(unitType: string): string {
  return HARDWARE_UNIT_TYPE_LABELS.get(unitType) ?? unitType.replaceAll("_", " ");
}

export function isAllowedHardwareUnitType(unitType: string): boolean {
  return HARDWARE_UNIT_TYPE_LABELS.has(unitType);
}

export function getInstallationRoleLabel(installationRole: string): string {
  return INSTALLATION_ROLE_LABELS.get(installationRole) ?? installationRole.replaceAll("_", " ");
}

export function getHardwareUnitStatusLabel(status: string): string {
  return HARDWARE_STATUS_LABELS.get(status) ?? status.replaceAll("_", " ");
}

export function getPlantName(plants: PlantProfile[], plantId: string): string {
  return plants.find((plant) => plant.id === plantId)?.name ?? plantId;
}

export function buildAdminOrgTabs(counts: {
  plants: number;
  users: number;
  hardware: number;
  notificationUsage?: number;
  includeNotificationUsage?: boolean;
}): AdminOrgTab[] {
  const tabs: AdminOrgTab[] = [
    { key: "plants", label: "Plants", count: counts.plants },
    { key: "users", label: "Org Admins", count: counts.users },
    { key: "hardware", label: "Hardware", count: counts.hardware },
  ];
  if (counts.includeNotificationUsage) {
    tabs.push({
      key: "notification_usage",
      label: "Notification Usage",
      count: counts.notificationUsage ?? 0,
    });
  }
  return tabs;
}

export function flattenDeviceHistory(
  historyByDeviceId: Record<string, DeviceHardwareInstallation[]>,
): DeviceHardwareInstallation[] {
  const seen = new Map<number, DeviceHardwareInstallation>();
  for (const rows of Object.values(historyByDeviceId)) {
    for (const row of rows) {
      seen.set(row.id, row);
    }
  }
  return Array.from(seen.values()).sort((left, right) =>
    right.commissioned_at.localeCompare(left.commissioned_at),
  );
}

export function buildInventoryRows(input: {
  hardwareUnits: HardwareUnit[];
  devices: Device[];
  plants: PlantProfile[];
  installations: DeviceHardwareInstallation[];
  plantFilter?: string | null;
}): InventoryRow[] {
  const plantNameById = new Map(input.plants.map((plant) => [plant.id, plant.name]));
  const deviceById = new Map(input.devices.map((device) => [device.id, device]));
  const activeInstallationByUnitId = new Map(
    input.installations
      .filter((installation) => installation.is_active)
      .map((installation) => [installation.hardware_unit_id, installation]),
  );

  return input.hardwareUnits
    .filter((hardwareUnit) => !input.plantFilter || hardwareUnit.plant_id === input.plantFilter)
    .map((hardwareUnit) => {
      const currentInstallation = activeInstallationByUnitId.get(hardwareUnit.hardware_unit_id) ?? null;
      return {
        hardwareUnit,
        plantName: plantNameById.get(hardwareUnit.plant_id) ?? hardwareUnit.plant_id,
        unitTypeLabel: getHardwareUnitTypeLabel(hardwareUnit.unit_type),
        statusLabel: getHardwareUnitStatusLabel(hardwareUnit.status),
        currentInstallation,
        currentDevice: currentInstallation ? deviceById.get(currentInstallation.device_id) ?? null : null,
        currentInstallationRoleLabel: currentInstallation
          ? getInstallationRoleLabel(currentInstallation.installation_role)
          : null,
      };
    })
    .sort((left, right) => left.hardwareUnit.hardware_unit_id.localeCompare(right.hardwareUnit.hardware_unit_id));
}

export function buildInstallableDeviceOptions(
  devices: Device[],
  plantId: string | null,
): Array<{ value: string; label: string }> {
  return devices
    .filter((device) => !plantId || device.plant_id === plantId)
    .sort((left, right) => left.id.localeCompare(right.id))
    .map((device) => ({
      value: device.id,
      label: `${device.id} · ${device.name}`,
    }));
}

export function buildHardwareUnitUpdatePayload(
  current: HardwareUnit,
  next: {
    plant_id: string;
    unit_type: string;
    unit_name: string;
    manufacturer?: string;
    model?: string;
    serial_number?: string;
    status: "available" | "retired";
  },
): HardwareUnitUpdateInput {
  const payload: HardwareUnitUpdateInput = {};
  const normalizeOptional = (value: string | null | undefined): string | undefined => {
    const trimmed = value?.trim();
    return trimmed ? trimmed : undefined;
  };

  if (current.plant_id !== next.plant_id) {
    payload.plant_id = next.plant_id;
  }
  if (current.unit_type !== next.unit_type) {
    payload.unit_type = next.unit_type;
  }
  if (current.unit_name !== next.unit_name) {
    payload.unit_name = next.unit_name;
  }
  if (normalizeOptional(current.manufacturer) !== normalizeOptional(next.manufacturer)) {
    payload.manufacturer = normalizeOptional(next.manufacturer);
  }
  if (normalizeOptional(current.model) !== normalizeOptional(next.model)) {
    payload.model = normalizeOptional(next.model);
  }
  if (normalizeOptional(current.serial_number) !== normalizeOptional(next.serial_number)) {
    payload.serial_number = normalizeOptional(next.serial_number);
  }
  if (current.status !== next.status) {
    payload.status = next.status;
  }

  return payload;
}

export function filterInstallationHistory(
  installations: DeviceHardwareInstallation[],
  filters: {
    plantId?: string | null;
    deviceId?: string | null;
    hardwareUnitId?: string | null;
    state?: "all" | "active" | "decommissioned";
  },
): DeviceHardwareInstallation[] {
  return installations.filter((installation) => {
    if (filters.plantId && installation.plant_id !== filters.plantId) {
      return false;
    }
    if (filters.deviceId && installation.device_id !== filters.deviceId) {
      return false;
    }
    if (filters.hardwareUnitId && installation.hardware_unit_id !== filters.hardwareUnitId) {
      return false;
    }
    if (filters.state === "active" && !installation.is_active) {
      return false;
    }
    if (filters.state === "decommissioned" && installation.is_active) {
      return false;
    }
    return true;
  });
}
