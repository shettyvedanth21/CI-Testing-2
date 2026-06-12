import { DEVICE_SERVICE_BASE } from "./api";
import { apiFetch } from "./apiFetch";
import { readResponseError } from "./responseError";

export interface HardwareUnit {
  id: number;
  hardware_unit_id: string;
  tenant_id: string;
  plant_id: string;
  unit_type: string;
  unit_name: string;
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  status: "available" | "retired";
  created_at: string;
  updated_at: string;
}

export interface DeviceHardwareInstallation {
  id: number;
  tenant_id: string;
  plant_id: string;
  device_id: string;
  hardware_unit_id: string;
  installation_role: string;
  commissioned_at: string;
  decommissioned_at: string | null;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeviceHardwareMapping {
  device_id: string;
  plant_id: string;
  plant_name: string;
  installation_role: string;
  installation_role_label: string;
  hardware_unit_id: string;
  hardware_type: string;
  hardware_type_label: string;
  hardware_name: string;
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  status: string;
  is_active: boolean;
}

export interface HardwareUnitCreateInput {
  plant_id: string;
  unit_type: string;
  unit_name: string;
  manufacturer?: string;
  model?: string;
  serial_number?: string;
  status?: "available" | "retired";
}

export interface HardwareUnitUpdateInput {
  plant_id?: string;
  unit_type?: string;
  unit_name?: string;
  manufacturer?: string;
  model?: string;
  serial_number?: string;
  status?: "available" | "retired";
}

export interface InstallHardwareInput {
  hardware_unit_id: string;
  installation_role: string;
  commissioned_at?: string | null;
  notes?: string;
}

export interface DecommissionInstallationInput {
  decommissioned_at?: string | null;
  notes?: string;
}

type ApiListResponse<T> = {
  success: boolean;
  data: T[];
  total: number;
};

type ApiSingleResponse<T> = {
  success: boolean;
  data: T;
};

function compactPayload(payload: object): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== undefined),
  );
}

async function assertOk(response: Response): Promise<void> {
  if (!response.ok) {
    throw new Error(await readResponseError(response));
  }
}

export async function listHardwareUnits(filters: {
  plantId?: string | null;
  status?: string | null;
} = {}): Promise<HardwareUnit[]> {
  const params = new URLSearchParams();
  if (filters.plantId) {
    params.set("plant_id", filters.plantId);
  }
  if (filters.status) {
    params.set("status", filters.status);
  }
  const query = params.toString();
  const response = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/hardware-units/list${query ? `?${query}` : ""}`,
    { cache: "no-store" },
  );
  await assertOk(response);
  const payload = (await response.json()) as ApiListResponse<HardwareUnit>;
  return payload.data ?? [];
}

export async function createHardwareUnit(input: HardwareUnitCreateInput): Promise<HardwareUnit> {
  const response = await apiFetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/hardware-units`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(compactPayload(input)),
  });
  await assertOk(response);
  const payload = (await response.json()) as ApiSingleResponse<HardwareUnit>;
  return payload.data;
}

export async function updateHardwareUnit(
  hardwareUnitId: string,
  input: HardwareUnitUpdateInput,
): Promise<HardwareUnit> {
  const response = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/hardware-units/${encodeURIComponent(hardwareUnitId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(compactPayload(input)),
    },
  );
  await assertOk(response);
  const payload = (await response.json()) as ApiSingleResponse<HardwareUnit>;
  return payload.data;
}

export async function installHardwareOnDevice(
  deviceId: string,
  input: InstallHardwareInput,
): Promise<DeviceHardwareInstallation> {
  const response = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${encodeURIComponent(deviceId)}/hardware-installations`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(compactPayload(input)),
    },
  );
  await assertOk(response);
  const payload = (await response.json()) as ApiSingleResponse<DeviceHardwareInstallation>;
  return payload.data;
}

export async function decommissionHardwareInstallation(
  installationId: number,
  input: DecommissionInstallationInput,
): Promise<DeviceHardwareInstallation> {
  const response = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/hardware-installations/${installationId}/decommission`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(compactPayload(input)),
    },
  );
  await assertOk(response);
  const payload = (await response.json()) as ApiSingleResponse<DeviceHardwareInstallation>;
  return payload.data;
}

export async function getDeviceInstallationHistory(
  deviceId: string,
): Promise<DeviceHardwareInstallation[]> {
  const response = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${encodeURIComponent(deviceId)}/hardware-installations/history`,
    { cache: "no-store" },
  );
  await assertOk(response);
  const payload = (await response.json()) as ApiListResponse<DeviceHardwareInstallation>;
  return payload.data ?? [];
}

export async function getCurrentDeviceInstallations(
  deviceId: string,
): Promise<DeviceHardwareInstallation[]> {
  const response = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${encodeURIComponent(deviceId)}/hardware-installations/current`,
    { cache: "no-store" },
  );
  await assertOk(response);
  const payload = (await response.json()) as ApiListResponse<DeviceHardwareInstallation>;
  return payload.data ?? [];
}

export async function listHardwareInstallationHistory(filters: {
  plantId?: string | null;
  deviceId?: string | null;
  hardwareUnitId?: string | null;
  state?: "active" | "decommissioned" | null;
} = {}): Promise<DeviceHardwareInstallation[]> {
  const params = new URLSearchParams();
  if (filters.plantId) {
    params.set("plant_id", filters.plantId);
  }
  if (filters.deviceId) {
    params.set("device_id", filters.deviceId);
  }
  if (filters.hardwareUnitId) {
    params.set("hardware_unit_id", filters.hardwareUnitId);
  }
  if (filters.state) {
    params.set("state", filters.state);
  }
  const query = params.toString();
  const response = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/hardware-installations/history${query ? `?${query}` : ""}`,
    { cache: "no-store" },
  );
  await assertOk(response);
  const payload = (await response.json()) as ApiListResponse<DeviceHardwareInstallation>;
  return payload.data ?? [];
}

export async function listHardwareMappings(filters: {
  plantId?: string | null;
  deviceId?: string | null;
} = {}): Promise<DeviceHardwareMapping[]> {
  const params = new URLSearchParams();
  if (filters.plantId) {
    params.set("plant_id", filters.plantId);
  }
  if (filters.deviceId) {
    params.set("device_id", filters.deviceId);
  }
  const query = params.toString();
  const response = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/hardware-mappings${query ? `?${query}` : ""}`,
    { cache: "no-store" },
  );
  await assertOk(response);
  const payload = (await response.json()) as ApiListResponse<DeviceHardwareMapping>;
  return payload.data ?? [];
}
