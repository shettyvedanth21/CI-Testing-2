import { apiFetch } from "./apiFetch.ts";
import { readResponseError } from "./responseError.ts";

const RULE_ENGINE_SERVICE_BASE = "/backend/rule-engine";

export type NotificationUsageChannel = "email" | "sms" | "whatsapp";
export type NotificationUsageStatus =
  | "queued"
  | "attempted"
  | "provider_accepted"
  | "delivered"
  | "failed"
  | "skipped";

export interface NotificationUsageCounts {
  attempted_count: number;
  accepted_count: number;
  delivered_count: number;
  failed_count: number;
  skipped_count: number;
  billable_count: number;
}

export interface NotificationUsageSummaryResponse {
  success: boolean;
  tenant_id: string;
  month: string;
  totals: NotificationUsageCounts;
  by_channel: Record<NotificationUsageChannel, NotificationUsageCounts>;
  first_attempt_at: string | null;
  last_attempt_at: string | null;
}

export interface NotificationUsageLogRow {
  id: string;
  tenant_id: string | null;
  attempted_at: string;
  channel: NotificationUsageChannel;
  status: NotificationUsageStatus;
  event_type: string;
  recipient_masked: string;
  provider_name: string;
  provider_message_id: string | null;
  rule_id: string | null;
  device_id: string | null;
  billable_units: number;
  failure_code: string | null;
  failure_message: string | null;
  accepted_at: string | null;
  delivered_at: string | null;
  failed_at: string | null;
  metadata_json: Record<string, unknown> | null;
}

export interface NotificationUsageLogsResponse {
  success: boolean;
  tenant_id: string;
  month: string;
  page: number;
  page_size: number;
  total: number;
  data: NotificationUsageLogRow[];
}

export interface NotificationUsageFilters {
  channel?: NotificationUsageChannel | "";
  status?: NotificationUsageStatus | "";
  ruleId?: string;
  deviceId?: string;
  search?: string;
  page?: number;
  pageSize?: number;
}

export interface NotificationSummaryCard {
  label: string;
  value: string;
  tone: "success" | "warning" | "danger" | "info" | "neutral";
}

function assertOk(response: Response): Promise<void> {
  if (response.ok) {
    return Promise.resolve();
  }
  return readResponseError(response).then((message) => Promise.reject(new Error(message)));
}

function asMonthLocal(date: Date): string {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  return `${year}-${month}`;
}

export function getCurrentMonthLocal(now = new Date()): string {
  return asMonthLocal(now);
}

export function buildNotificationUsageQuery(
  month: string,
  filters: NotificationUsageFilters = {},
): URLSearchParams {
  const params = new URLSearchParams();
  params.set("month", month);
  if (filters.channel) {
    params.set("channel", filters.channel);
  }
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.ruleId?.trim()) {
    params.set("rule_id", filters.ruleId.trim());
  }
  if (filters.deviceId?.trim()) {
    params.set("device_id", filters.deviceId.trim());
  }
  if (filters.search?.trim()) {
    params.set("search", filters.search.trim());
  }
  if (typeof filters.page === "number" && Number.isFinite(filters.page)) {
    params.set("page", `${Math.max(1, Math.floor(filters.page))}`);
  }
  if (typeof filters.pageSize === "number" && Number.isFinite(filters.pageSize)) {
    params.set("page_size", `${Math.min(500, Math.max(1, Math.floor(filters.pageSize)))}`);
  }
  return params;
}

export function buildNotificationUsageExportPath(
  tenantId: string,
  month: string,
  filters: NotificationUsageFilters = {},
): string {
  const params = buildNotificationUsageQuery(month, filters);
  return `${RULE_ENGINE_SERVICE_BASE}/api/v1/admin/notification-usage/${encodeURIComponent(tenantId)}/export.csv?${params.toString()}`;
}

export async function getNotificationUsageSummary(
  tenantId: string,
  month: string,
  filters: Omit<NotificationUsageFilters, "page" | "pageSize"> = {},
): Promise<NotificationUsageSummaryResponse> {
  const params = buildNotificationUsageQuery(month, filters);
  const response = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/admin/notification-usage/${encodeURIComponent(tenantId)}/summary?${params.toString()}`,
    { cache: "no-store" },
  );
  await assertOk(response);
  return response.json() as Promise<NotificationUsageSummaryResponse>;
}

export async function getNotificationUsageLogs(
  tenantId: string,
  month: string,
  filters: NotificationUsageFilters = {},
): Promise<NotificationUsageLogsResponse> {
  const params = buildNotificationUsageQuery(month, filters);
  const response = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/admin/notification-usage/${encodeURIComponent(tenantId)}/logs?${params.toString()}`,
    { cache: "no-store" },
  );
  await assertOk(response);
  return response.json() as Promise<NotificationUsageLogsResponse>;
}

export async function downloadNotificationUsageCsv(
  tenantId: string,
  month: string,
  filters: NotificationUsageFilters = {},
): Promise<void> {
  const url = buildNotificationUsageExportPath(tenantId, month, filters);
  const response = await apiFetch(url, { cache: "no-store" });
  await assertOk(response);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const suffix = [tenantId, month, filters.channel || undefined, filters.status || undefined].filter(Boolean).join("_");
  const filename = `notification_usage_${suffix}.csv`;
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(objectUrl);
}

export function buildSummaryCards(summary: NotificationUsageSummaryResponse): NotificationSummaryCard[] {
  const email = summary.by_channel.email?.billable_count ?? 0;
  const sms = summary.by_channel.sms?.billable_count ?? 0;
  const whatsapp = summary.by_channel.whatsapp?.billable_count ?? 0;
  return [
    { label: "SMS Billable", value: `${sms}`, tone: "info" },
    { label: "WhatsApp Billable", value: `${whatsapp}`, tone: "info" },
    { label: "Email Billable", value: `${email}`, tone: "info" },
    { label: "Total Billable", value: `${summary.totals.billable_count}`, tone: "success" },
    { label: "Failed", value: `${summary.totals.failed_count}`, tone: summary.totals.failed_count > 0 ? "danger" : "neutral" },
    { label: "Attempted", value: `${summary.totals.attempted_count}`, tone: "neutral" },
    { label: "Delivered", value: `${summary.totals.delivered_count}`, tone: "success" },
    { label: "Accepted", value: `${summary.totals.accepted_count}`, tone: "warning" },
  ];
}

export function buildNotificationUsageRequestKey(
  tenantId: string,
  month: string,
  filters: NotificationUsageFilters,
): string {
  return `${tenantId}:${buildNotificationUsageQuery(month, filters).toString()}`;
}

export function getNotificationUsageErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  return "Failed to load notification usage data.";
}

export function shouldShowNotificationUsageEmptyState(payload: NotificationUsageLogsResponse | null): boolean {
  return Boolean(payload && (payload.data?.length ?? 0) === 0);
}

export function formatNotificationFailureReason(code: string | null, message: string | null): string {
  const segments = [code, message].filter(Boolean);
  if (!segments.length) {
    return "—";
  }
  const combined = segments.join(": ");
  return combined.length > 100 ? `${combined.slice(0, 100)}…` : combined;
}
