import { DATA_SERVICE_BASE, RULE_ENGINE_SERVICE_BASE, DEVICE_SERVICE_BASE } from "./api";
import { apiFetch } from "./apiFetch";
import { readResponseError } from "./responseError";
import { isRuleSelectableMetric } from "./telemetryContract.ts";

async function readRuleDataApiError(res: Response): Promise<string> {
  return readResponseError(res);
}

/* ---------- telemetry ---------- */

export interface TelemetryPoint {
  timestamp: string;
  [key: string]: number | string | undefined;
}

export class TelemetryHistoryUnavailableError extends Error {
  status: number;
  code: string;
  retryable: boolean;
  source: string | null;

  constructor({
    message,
    status,
    code,
    retryable,
    source,
  }: {
    message: string;
    status: number;
    code: string;
    retryable: boolean;
    source?: string | null;
  }) {
    super(message);
    this.name = "TelemetryHistoryUnavailableError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.source = source ?? null;
  }
}

export function isTelemetryHistoryUnavailableError(error: unknown): error is TelemetryHistoryUnavailableError {
  return error instanceof TelemetryHistoryUnavailableError;
}

async function buildTelemetryHistoryError(res: Response): Promise<TelemetryHistoryUnavailableError> {
  let message = `HTTP ${res.status}`;
  let code = "TELEMETRY_HISTORY_UNAVAILABLE";
  let retryable = res.status >= 500;
  let source: string | null = null;

  try {
    const json = await res.json();
    const detail = json?.detail ?? json;
    const error = detail?.error ?? {};
    message = error.message ?? detail?.message ?? message;
    code = error.code ?? detail?.code ?? code;
    retryable = Boolean(error.retryable ?? retryable);
    source = error.source ?? detail?.source ?? null;
  } catch {
    const text = await res.text();
    if (text) {
      message = `HTTP ${res.status} - ${text}`;
    }
  }

  return new TelemetryHistoryUnavailableError({
    message,
    status: res.status,
    code,
    retryable,
    source,
  });
}

export async function getTelemetry(
  deviceId: string,
  params?: Record<string, string>
): Promise<TelemetryPoint[]> {

  const query = new URLSearchParams(params || {}).toString();

  const url =
    `${DATA_SERVICE_BASE}/api/v1/data/telemetry/${deviceId}` +
    (query ? `?${query}` : "");

  const res = await apiFetch(url);

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }

  const json = await res.json();

  // ✅ Permanent, shape-safe normalization

  // New API shape:
  // { success, data: { items: [...] } }
  if (json?.data?.items && Array.isArray(json.data.items)) {
    return json.data.items;
  }

  // Older / alternate shapes
  if (Array.isArray(json?.data)) {
    return json.data;
  }

  if (Array.isArray(json)) {
    return json;
  }

  return [];
}

export async function getTelemetryHistory(
  deviceId: string,
  params?: Record<string, string>,
): Promise<TelemetryPoint[]> {
  const query = new URLSearchParams(params || {}).toString();
  const url =
    `${DATA_SERVICE_BASE}/api/v1/data/telemetry/${deviceId}` +
    (query ? `?${query}` : "");
  const res = await apiFetch(url, { cache: "no-store" });

  if (!res.ok) {
    throw await buildTelemetryHistoryError(res);
  }

  const json = await res.json();
  if (json?.data?.items && Array.isArray(json.data.items)) {
    return json.data.items;
  }
  if (Array.isArray(json?.data)) {
    return json.data;
  }
  if (Array.isArray(json)) {
    return json;
  }
  return [];
}

export async function getLatestTelemetry(deviceId: string): Promise<TelemetryPoint | null> {
  const res = await apiFetch(`${DATA_SERVICE_BASE}/api/v1/data/telemetry/${deviceId}/latest`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }
  const json = await res.json();
  return json?.data?.item ?? null;
}

export async function getTelemetryWebsocketTicket(
  deviceId: string,
): Promise<{ ticket: string; expires_in_seconds: number }> {
  const res = await apiFetch(`${DATA_SERVICE_BASE}/api/v1/data/telemetry/${deviceId}/ws-ticket`, {
    method: "POST",
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }
  const json = await res.json();
  const ticket = json?.data?.ticket;
  const expiresIn = json?.data?.expires_in_seconds;
  if (typeof ticket !== "string" || !ticket) {
    throw new Error("Missing WebSocket ticket");
  }
  return {
    ticket,
    expires_in_seconds: typeof expiresIn === "number" ? expiresIn : 0,
  };
}

export async function getLatestTelemetryBatch(deviceIds: string[]): Promise<Record<string, TelemetryPoint | null>> {
  const res = await apiFetch(`${DATA_SERVICE_BASE}/api/v1/data/telemetry/latest-batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_ids: deviceIds }),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }
  const json = await res.json();
  return json?.data?.items ?? {};
}


/* ---------- stats ---------- */

export interface DeviceStats {
  device_id: string;
  [key: string]: number | string;
}

export async function getDeviceStats(deviceId: string): Promise<DeviceStats> {

  const res = await apiFetch(
    `${DATA_SERVICE_BASE}/api/v1/data/stats/${deviceId}`
  );

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }

  const json = await res.json();

  return json.data ?? json;
}


export async function getDeviceFields(deviceId: string): Promise<string[]> {
  try {
    const telemetry = await getTelemetry(deviceId, { limit: "1" });
    if (telemetry.length === 0) return [];
    
    const latest = telemetry[0];
    const fields: string[] = [];
    
    for (const [key, value] of Object.entries(latest)) {
      if (key !== 'device_id' && typeof value === 'number' && isRuleSelectableMetric(key)) {
        fields.push(key);
      }
    }
    
    return fields;
  } catch {
    return [];
  }
}


/* ---------- alerts (rule-engine-service) ---------- */

export interface Alert {
  alertId: string;
  ruleId: string;
  deviceId: string;
  severity: string;
  message: string;
  actualValue: number;
  thresholdValue: number;
  status: string;
  acknowledgedBy: string | null;
  acknowledgedAt: string | null;
  resolvedAt: string | null;
  createdAt: string;
}

export interface ActivityEvent {
  eventId: string;
  tenantId: string | null;
  deviceId: string | null;
  ruleId: string | null;
  alertId: string | null;
  eventType: string;
  title: string;
  message: string;
  metadataJson: Record<string, unknown>;
  isRead: boolean;
  readAt: string | null;
  createdAt: string;
}

export interface ActivitySummary {
  active_alerts: number;
  alerts_triggered: number;
  alerts_cleared: number;
  rules_created: number;
  rules_updated: number;
  rules_deleted: number;
}

export async function getDeviceAlerts(
  deviceId: string,
  params?: {
    page?: number;
    pageSize?: number;
    status?: string;
  }
): Promise<{
  data: Alert[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}> {

  const query = new URLSearchParams({
    device_id: deviceId,
    page: String(params?.page ?? 1),
    page_size: String(params?.pageSize ?? 20),
  });

  if (params?.status) {
    query.append("status", params.status);
  }

  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/alerts?${query.toString()}`
  );

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }

  const json = await res.json();
  type RawAlert = {
    alert_id: string;
    rule_id: string;
    device_id: string;
    severity: string;
    message: string;
    actual_value: number;
    threshold_value: number;
    status: string;
    acknowledged_by: string | null;
    acknowledged_at: string | null;
    resolved_at: string | null;
    created_at: string;
  };

  return {
    data: (json.data as RawAlert[]).map((a) => ({
      alertId: a.alert_id,
      ruleId: a.rule_id,
      deviceId: a.device_id,
      severity: a.severity,
      message: a.message,
      actualValue: a.actual_value,
      thresholdValue: a.threshold_value,
      status: a.status,
      acknowledgedBy: a.acknowledged_by,
      acknowledgedAt: a.acknowledged_at,
      resolvedAt: a.resolved_at,
      createdAt: a.created_at,
    })),
    total: json.total,
    page: json.page,
    pageSize: json.page_size,
    totalPages: json.total_pages,
  };
}

export async function getActivityEvents(params?: {
  deviceId?: string;
  eventType?: string;
  page?: number;
  pageSize?: number;
  signal?: AbortSignal;
}): Promise<{
  data: ActivityEvent[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}> {
  const query = new URLSearchParams({
    page: String(params?.page ?? 1),
    page_size: String(params?.pageSize ?? 20),
  });

  if (params?.deviceId) query.append("device_id", params.deviceId);
  if (params?.eventType) query.append("event_type", params.eventType);

  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/alerts/events?${query.toString()}`,
    params?.signal ? { signal: params.signal } : undefined,
  );

  if (!res.ok) {
    throw new Error(await readRuleDataApiError(res));
  }

  const json = await res.json();
  type RawEvent = {
    event_id: string;
    tenant_id: string | null;
    device_id: string | null;
    rule_id: string | null;
    alert_id: string | null;
    event_type: string;
    title: string;
    message: string;
    metadata_json?: Record<string, unknown>;
    is_read: boolean;
    read_at: string | null;
    created_at: string;
  };
  return {
    data: (json.data as RawEvent[]).map((e) => ({
      eventId: e.event_id,
      tenantId: e.tenant_id,
      deviceId: e.device_id,
      ruleId: e.rule_id,
      alertId: e.alert_id,
      eventType: e.event_type,
      title: e.title,
      message: e.message,
      metadataJson: e.metadata_json || {},
      isRead: e.is_read,
      readAt: e.read_at,
      createdAt: e.created_at,
    })),
    total: json.total,
    page: json.page,
    pageSize: json.page_size,
    totalPages: json.total_pages,
  };
}

export async function getActivityUnreadCount(
  deviceId?: string,
  options?: { signal?: AbortSignal },
): Promise<number> {
  const query = new URLSearchParams();
  if (deviceId) query.append("device_id", deviceId);

  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/alerts/events/unread-count?${query.toString()}`,
    options?.signal ? { signal: options.signal } : undefined,
  );
  if (!res.ok) {
    throw new Error(await readRuleDataApiError(res));
  }
  const json = await res.json();
  return json?.data?.count ?? 0;
}

export async function markAllActivityRead(deviceId?: string): Promise<number> {
  const query = new URLSearchParams();
  if (deviceId) query.append("device_id", deviceId);

  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/alerts/events/mark-all-read?${query.toString()}`,
    { method: "PATCH" }
  );
  if (!res.ok) {
    throw new Error(await readRuleDataApiError(res));
  }
  const json = await res.json();
  return json?.data?.updated ?? 0;
}

export async function clearActivityHistory(deviceId?: string): Promise<number> {
  const query = new URLSearchParams();
  if (deviceId) query.append("device_id", deviceId);

  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/alerts/events?${query.toString()}`,
    { method: "DELETE" }
  );
  if (!res.ok) {
    throw new Error(await readRuleDataApiError(res));
  }
  const json = await res.json();
  return json?.data?.deleted ?? 0;
}

export async function getActivitySummary(): Promise<ActivitySummary> {
  const res = await apiFetch(`${RULE_ENGINE_SERVICE_BASE}/api/v1/alerts/events/summary`);
  if (!res.ok) {
    throw new Error(await readRuleDataApiError(res));
  }
  const json = await res.json();
  return {
    active_alerts: json?.data?.active_alerts ?? 0,
    alerts_triggered: json?.data?.alerts_triggered ?? 0,
    alerts_cleared: json?.data?.alerts_cleared ?? 0,
    rules_created: json?.data?.rules_created ?? 0,
    rules_updated: json?.data?.rules_updated ?? 0,
    rules_deleted: json?.data?.rules_deleted ?? 0,
  };
}


/* ---------- alert actions ---------- */

export async function acknowledgeAlert(
  alertId: string,
  acknowledgedBy: string
) {
  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/alerts/${alertId}/acknowledge`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        acknowledged_by: acknowledgedBy,
      }),
    }
  );

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }

  return res.json();
}

export async function resolveAlert(alertId: string) {
  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/alerts/${alertId}/resolve`,
    {
      method: "PATCH",
    }
  );

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }

  return res.json();
}


export async function getAllDevicesProperties(): Promise<{
  devices: Record<string, string[]>;
  all_properties: string[];
}> {
  const res = await apiFetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/properties`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }
  return res.json();
}


export async function getCommonProperties(deviceIds: string[]): Promise<{
  properties: string[];
  device_count: number;
  message: string;
}> {
  const res = await apiFetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/properties/common`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_ids: deviceIds }),
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }
  return res.json();
}


export async function getDeviceProperties(deviceId: string): Promise<string[]> {
  const res = await apiFetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/properties?numeric_only=true`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} - ${text}`);
  }
  const data = await res.json();
  return data.map((p: { property_name: string }) => p.property_name);
}
