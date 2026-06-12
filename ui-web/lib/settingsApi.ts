import { DEVICE_SERVICE_BASE } from "./api";
import { apiFetch } from "./apiFetch";
const SETTINGS_BASE = "/backend/reporting/api/v1/settings";

export type CurrencyCode = "INR" | "USD" | "EUR";

export interface TariffConfigResponse {
  id?: string | null;
  rate: number | null;
  currency: CurrencyCode;
  updated_at: string | null;
  updated_by?: string | null;
  effective_from?: string | null;
  is_active?: boolean;
}

export interface TariffHistoryEntry {
  id: string;
  rate: number;
  currency: CurrencyCode;
  updated_at: string;
  effective_from: string;
  updated_by: string | null;
  is_active: boolean;
}

export interface SiteWasteConfigResponse {
  tenant_id?: string | null;
  default_unoccupied_weekday_start_time: string | null;
  default_unoccupied_weekday_end_time: string | null;
  default_unoccupied_weekend_start_time: string | null;
  default_unoccupied_weekend_end_time: string | null;
  timezone: string;
  configured: boolean;
}

export async function getTariffConfig(): Promise<TariffConfigResponse> {
  const res = await apiFetch(`${SETTINGS_BASE}/tariff`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function saveTariffConfig(payload: {
  rate: number;
  currency: CurrencyCode;
  updated_by?: string;
}): Promise<TariffConfigResponse> {
  const res = await apiFetch(`${SETTINGS_BASE}/tariff`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error?.message || error?.detail?.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getTariffHistory(): Promise<{ versions: TariffHistoryEntry[] }> {
  const res = await apiFetch(`${SETTINGS_BASE}/tariff/history`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

export async function activateTariffVersion(versionId: string): Promise<TariffConfigResponse> {
  const res = await apiFetch(`${SETTINGS_BASE}/tariff/history/${encodeURIComponent(versionId)}/activate`, {
    method: "PATCH",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error?.message || error?.detail?.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getSiteWasteConfig(): Promise<SiteWasteConfigResponse> {
  const res = await apiFetch(`${DEVICE_SERVICE_BASE}/api/v1/settings/waste-config`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function saveSiteWasteConfig(payload: {
  default_unoccupied_weekday_start_time: string;
  default_unoccupied_weekday_end_time: string;
  default_unoccupied_weekend_start_time: string;
  default_unoccupied_weekend_end_time: string;
  timezone?: string;
  updated_by?: string;
  tenant_id?: string;
}): Promise<SiteWasteConfigResponse> {
  const res = await apiFetch(`${DEVICE_SERVICE_BASE}/api/v1/settings/waste-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error?.message || error?.detail?.message || `HTTP ${res.status}`);
  }
  return res.json();
}
