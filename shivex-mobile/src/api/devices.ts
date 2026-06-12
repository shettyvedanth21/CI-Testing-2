import { mobileFetch } from "./authApi";
import { API_CONFIG } from "../constants/api";

export type DeviceStatus = "RUNNING" | "IDLE" | "STOPPED" | "OFFLINE";

export type DeviceRecord = {
  id: string;
  name: string;
  status: DeviceStatus;
  current: number | null;
  voltage: number | null;
  power: number | null;
  powerFactor: number | null;
  energy: number | null;
  healthScore: number | null;
  lastSeen: string | null;
  type?: string | null;
  location?: string | null;
};

export type TelemetryPoint = {
  timestamp: string;
  current: number | null;
  voltage: number | null;
  power: number | null;
  powerFactor: number | null;
  energy: number | null;
};

type ApiEnvelope<T> = {
  success?: boolean;
  data?: T;
  total?: number;
  page?: number;
  page_size?: number;
  total_pages?: number;
};

type DeviceApiRecord = {
  device_id?: string;
  device_name?: string;
  runtime_status?: string;
  status?: string;
  current?: number;
  voltage?: number;
  power?: number;
  power_factor?: number;
  energy?: number;
  health_score?: number;
  last_seen_timestamp?: string;
  device_type?: string;
  location?: string;
};

type TelemetryItem = {
  timestamp?: string;
  current?: number;
  voltage?: number;
  power?: number;
  power_factor?: number;
  energy?: number;
};

type DashboardBootstrap = {
  device?: DeviceApiRecord;
  telemetry?: TelemetryItem[];
  telemetry_business?: TelemetryItem | null;
  health_score?:
    | number
    | {
        health_score?: number;
      }
    | null;
};

const deviceServiceBase = `${API_CONFIG.DEVICE_SERVICE}/api/v1/devices`;
const dataServiceBase = `${API_CONFIG.DATA_SERVICE}/api/v1/data`;

function normalizeStatus(value?: string | null): DeviceStatus {
  const normalized = (value ?? "").toUpperCase();

  if (normalized === "RUNNING") {
    return "RUNNING";
  }

  if (normalized === "IDLE") {
    return "IDLE";
  }

  if (normalized === "OFFLINE") {
    return "OFFLINE";
  }

  return "STOPPED";
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeDevice(record?: DeviceApiRecord | null): DeviceRecord {
  return {
    id: record?.device_id ?? "unknown-device",
    name: record?.device_name ?? record?.device_id ?? "Unknown machine",
    status: normalizeStatus(record?.runtime_status ?? record?.status),
    current: asNumber(record?.current),
    voltage: asNumber(record?.voltage),
    power: asNumber(record?.power),
    powerFactor: asNumber(record?.power_factor),
    energy: asNumber(record?.energy),
    healthScore: asNumber(record?.health_score),
    lastSeen: record?.last_seen_timestamp ?? null,
    type: record?.device_type ?? null,
    location: record?.location ?? null,
  };
}

function normalizeTelemetryItem(item?: TelemetryItem | null): TelemetryPoint {
  return {
    timestamp: item?.timestamp ?? new Date().toISOString(),
    current: asNumber(item?.current),
    voltage: asNumber(item?.voltage),
    power: asNumber(item?.power),
    powerFactor: asNumber(item?.power_factor),
    energy: asNumber(item?.energy),
  };
}

function extractHealthScore(value: DashboardBootstrap["health_score"]): number | null {
  if (typeof value === "number") {
    return asNumber(value);
  }

  if (value && typeof value === "object") {
    return asNumber(value.health_score);
  }

  return null;
}

async function readJson<T>(input: string, init?: RequestInit): Promise<T | null> {
  try {
    const response = await mobileFetch(input, init);

    if (!response.ok) {
      console.error("[shivex api]", input, response.status);
      return null;
    }

    return (await response.json()) as T;
  } catch (error) {
    console.error("[shivex api]", error);
    return null;
  }
}

async function getDashboardBootstrap(deviceId: string): Promise<DashboardBootstrap | null> {
  return readJson<DashboardBootstrap>(`${deviceServiceBase}/${deviceId}/dashboard-bootstrap`);
}

function firstTelemetryValue(telemetry?: TelemetryItem[] | null): TelemetryPoint | null {
  if (!telemetry || telemetry.length === 0) {
    return null;
  }

  const item = telemetry.find((entry) =>
    [entry?.current, entry?.voltage, entry?.power, entry?.power_factor, entry?.energy].some(
      (value) => typeof value === "number"
    )
  );

  return normalizeTelemetryItem(item ?? telemetry[0]);
}

function businessTelemetryValue(item?: TelemetryItem | null): TelemetryPoint | null {
  if (!item) {
    return null;
  }
  return normalizeTelemetryItem(item);
}

export async function getDevices(): Promise<DeviceRecord[] | null> {
  const firstPage = await readJson<ApiEnvelope<DeviceApiRecord[]>>(`${deviceServiceBase}`);

  if (!firstPage?.data) {
    return null;
  }

  const pageSize = 100;
  const totalPages = firstPage.total_pages ?? 1;
  const firstDevices = firstPage.data.map((item) => normalizeDevice(item));
  const additionalPages = Array.from({ length: Math.max(totalPages - 1, 0) }, (_, index) => index + 2);

  if (additionalPages.length === 0) {
    return firstDevices;
  }

  const remainingPages = await Promise.all(
    additionalPages.map(async (page) => {
      const payload = await readJson<ApiEnvelope<DeviceApiRecord[]>>(
        `${deviceServiceBase}?page=${page}&page_size=${pageSize}`
      );
      return (payload?.data ?? []).map((item) => normalizeDevice(item));
    })
  );

  const baseDevices = [...firstDevices, ...remainingPages.flat()];
  const enrichedDevices = await Promise.all(
    baseDevices.map(async (device) => {
      const bootstrap = await getDashboardBootstrap(device.id);
      const latestTelemetry =
        businessTelemetryValue(bootstrap?.telemetry_business ?? null) ??
        firstTelemetryValue(bootstrap?.telemetry ?? null);
      const healthScore = extractHealthScore(bootstrap?.health_score) ?? device.healthScore;

      return {
        ...device,
        current: latestTelemetry?.current ?? device.current,
        voltage: latestTelemetry?.voltage ?? device.voltage,
        power: latestTelemetry?.power ?? device.power,
        powerFactor: latestTelemetry?.powerFactor ?? device.powerFactor,
        energy: latestTelemetry?.energy ?? device.energy,
        healthScore,
      };
    })
  );

  return enrichedDevices;
}

export async function getDevice(deviceId: string): Promise<DeviceRecord | null> {
  const payload = await readJson<ApiEnvelope<DeviceApiRecord>>(`${deviceServiceBase}/${deviceId}`);
  if (!payload?.data) {
    return null;
  }

  const device = normalizeDevice(payload.data);
  const bootstrap = await getDashboardBootstrap(deviceId);
  const latestTelemetry =
    businessTelemetryValue(bootstrap?.telemetry_business ?? null) ??
    firstTelemetryValue(bootstrap?.telemetry ?? null);
  const healthScore = extractHealthScore(bootstrap?.health_score) ?? device.healthScore;

  return {
    ...device,
    current: latestTelemetry?.current ?? device.current,
    voltage: latestTelemetry?.voltage ?? device.voltage,
    power: latestTelemetry?.power ?? device.power,
    powerFactor: latestTelemetry?.powerFactor ?? device.powerFactor,
    energy: latestTelemetry?.energy ?? device.energy,
    healthScore,
  };
}

export async function getTelemetry(deviceId: string, hours = 2): Promise<TelemetryPoint[] | null> {
  const endTime = new Date();
  const startTime = new Date(endTime.getTime() - hours * 60 * 60 * 1000);
  const query = new URLSearchParams({
    start_time: startTime.toISOString(),
    end_time: endTime.toISOString(),
    limit: "240",
  });
  const payload = await readJson<ApiEnvelope<{ items?: TelemetryItem[] }>>(
    `${dataServiceBase}/telemetry/${deviceId}?${query.toString()}`
  );
  const items = payload?.data?.items ?? [];
  return items.map((item) => normalizeTelemetryItem(item));
}

export async function getLatestTelemetry(deviceId: string): Promise<TelemetryPoint | null> {
  const payload = await readJson<ApiEnvelope<{ item?: TelemetryItem }>>(
    `${dataServiceBase}/telemetry/${deviceId}/latest`
  );
  return payload?.data?.item ? normalizeTelemetryItem(payload.data.item) : null;
}

export async function getHealthScore(deviceId: string): Promise<number | null> {
  const payload = await getDashboardBootstrap(deviceId);
  return extractHealthScore(payload?.health_score ?? null);
}
