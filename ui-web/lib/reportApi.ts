import { REPORT_SERVICE_BASE } from "./api";
import { apiFetch } from "./apiFetch";
import { readResponseError } from "./responseError";
import type { TelemetryCoverageResult } from "./telemetryCoverage";

export class ReportApiError extends Error {
  status: number;
  body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ReportApiError";
    this.status = status;
    this.body = body;
  }
}

async function readReportApiError(res: Response): Promise<string> {
  return readResponseError(res);
}

async function readReportApiBody(res: Response): Promise<unknown> {
  const contentType = res.headers.get("content-type") ?? "";
  try {
    if (contentType.includes("application/json")) {
      return await res.json();
    }
    return await res.text();
  } catch {
    return null;
  }
}

async function requestReportJson<T = unknown>(url: string, options?: RequestInit): Promise<T> {
  const res = await apiFetch(url, options);
  const body = await readReportApiBody(res);
  if (!res.ok) {
    const candidate = body as Record<string, unknown> | string | null;
    const message =
      (typeof candidate === "object" &&
        candidate !== null &&
        (candidate.message ??
          (candidate.error as Record<string, unknown> | undefined)?.message ??
          (candidate.detail as Record<string, unknown> | undefined)?.message)) ||
      (typeof candidate === "string" ? candidate : null) ||
      `HTTP ${res.status}`;
    throw new ReportApiError(String(message), res.status, body);
  }
  return body as T;
}

function withTenantQuery(url: string, tenantId?: string): string {
  if (!tenantId) {
    return url;
  }

  const parsed = new URL(url, "http://localhost");
  parsed.searchParams.set("tenant_id", tenantId);
  return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}

export interface ConsumptionReportParams {
  tenant_id?: string;
  device_id: string;
  start_date: string;
  end_date: string;
  report_name?: string;
}

export interface ComparisonReportParams {
  tenant_id: string;
  comparison_type: "machine_vs_machine" | "period_vs_period";
  machine_a_id?: string;
  machine_b_id?: string;
  start_date?: string;
  end_date?: string;
  device_id?: string;
  period_a_start?: string;
  period_a_end?: string;
  period_b_start?: string;
  period_b_end?: string;
}

export interface ReportStatus {
  report_id: string;
  status: "pending" | "running" | "completed" | "failed";
  backend_status?: "pending" | "processing" | "completed" | "failed";
  progress: number;
  phase?: string | null;
  phase_label?: string | null;
  phase_progress?: number | null;
  queue_position?: number | null;
  estimated_wait_seconds?: number | null;
  estimated_completion_seconds?: number | null;
  estimate_quality?: "low" | "medium" | "high" | null;
  result_ready?: boolean;
  artifact_ready?: boolean;
  download_ready?: boolean;
  result_url?: string | null;
  download_url?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_code?: string;
  error_message?: string;
  coverage_result?: TelemetryCoverageResult | null;
}

export interface ReportHistoryItem {
  report_id: string;
  status: string;
  backend_status?: string;
  report_type: string;
  progress?: number | null;
  phase?: string | null;
  phase_label?: string | null;
  phase_progress?: number | null;
  queue_position?: number | null;
  estimated_wait_seconds?: number | null;
  estimated_completion_seconds?: number | null;
  estimate_quality?: "low" | "medium" | "high" | null;
  result_ready?: boolean;
  artifact_ready?: boolean;
  download_ready?: boolean;
  result_url?: string | null;
  download_url?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  coverage_result?: TelemetryCoverageResult | null;
  created_at: string;
  started_at?: string | null;
  completed_at: string | null;
}

export interface TariffData {
  tenant_id: string;
  energy_rate_per_kwh: number;
  demand_charge_per_kw?: number;
  reactive_penalty_rate?: number;
  fixed_monthly_charge?: number;
  power_factor_threshold?: number;
  currency?: string;
}

export async function submitConsumptionReport(
  params: ConsumptionReportParams
): Promise<ReportStatus> {
  return requestReportJson<ReportStatus>(`${REPORT_SERVICE_BASE}/energy/consumption`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export async function submitComparisonReport(
  params: ComparisonReportParams
): Promise<ReportStatus> {
  return requestReportJson<ReportStatus>(`${REPORT_SERVICE_BASE}/energy/comparison`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export async function getReportStatus(
  reportId: string,
  tenantId: string
): Promise<ReportStatus> {
  return requestReportJson<ReportStatus>(
    withTenantQuery(`${REPORT_SERVICE_BASE}/${reportId}/status`, tenantId),
  );
}

export async function getReportResult(
  reportId: string,
  tenantId: string
): Promise<unknown> {
  return requestReportJson<unknown>(
    withTenantQuery(`${REPORT_SERVICE_BASE}/${reportId}/result`, tenantId),
  );
}

export async function getReportDownload(
  reportId: string,
  tenantId: string
): Promise<Blob> {
  const res = await apiFetch(withTenantQuery(`${REPORT_SERVICE_BASE}/${reportId}/download`, tenantId));
  if (!res.ok) {
    throw new Error(await readReportApiError(res));
  }
  return res.blob();
}

export async function getReportHistory(
  tenantId: string,
  params?: {
    limit?: number;
    offset?: number;
    report_type?: string;
  }
): Promise<{ reports: ReportHistoryItem[] }> {
  const searchParams = new URLSearchParams();
  if (tenantId) searchParams.set("tenant_id", tenantId);
  if (params?.limit) searchParams.set("limit", params.limit.toString());
  if (params?.offset) searchParams.set("offset", params.offset.toString());
  if (params?.report_type) searchParams.set("report_type", params.report_type);

  const suffix = searchParams.toString();
  const res = await apiFetch(`${REPORT_SERVICE_BASE}/history${suffix ? `?${suffix}` : ""}`);
  if (!res.ok) {
    throw new Error(await readReportApiError(res));
  }
  return res.json();
}

export async function upsertTariff(data: TariffData): Promise<unknown> {
  const res = await apiFetch(`${REPORT_SERVICE_BASE}/tariffs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    throw new Error(await readReportApiError(res));
  }
  return res.json();
}

export async function getTariff(tenantId: string): Promise<TariffData | null> {
  const res = await apiFetch(`${REPORT_SERVICE_BASE}/tariffs/${encodeURIComponent(tenantId)}`);
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(await readReportApiError(res));
  }
  return res.json();
}

export interface ScheduleParams {
  report_type: "consumption" | "comparison";
  frequency: "daily" | "weekly" | "monthly";
  params_template: {
    device_ids: string[];
    group_by?: "daily" | "weekly";
  };
}

export interface ScheduleResponse {
  schedule_id: string;
  tenant_id: string;
  report_type: string;
  frequency: string;
  is_active: boolean;
  next_run_at: string | null;
  created_at: string;
}

export interface ScheduleListItem {
  schedule_id: string;
  tenant_id: string;
  report_type: string;
  frequency: string;
  is_active: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: string | null;
  last_result_url: string | null;
  params_template: {
    device_ids: string[];
    group_by?: "daily" | "weekly";
  };
}

export interface ScheduleUpdateParams {
  report_type?: "consumption" | "comparison";
  frequency?: "daily" | "weekly" | "monthly";
  params_template?: {
    device_ids: string[];
    group_by?: "daily" | "weekly";
  };
}

export async function createSchedule(
  tenantId: string,
  data: ScheduleParams
): Promise<ScheduleResponse> {
  const res = await apiFetch(withTenantQuery(`${REPORT_SERVICE_BASE}/schedules`, tenantId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    throw new Error(await readReportApiError(res));
  }
  return res.json();
}

export async function getSchedules(tenantId: string): Promise<{ schedules: ScheduleListItem[] }> {
  const res = await apiFetch(withTenantQuery(`${REPORT_SERVICE_BASE}/schedules`, tenantId));
  if (!res.ok) {
    throw new Error(await readReportApiError(res));
  }
  return res.json();
}

export async function deleteSchedule(
  scheduleId: string,
  tenantId: string
): Promise<{ message: string }> {
  const res = await apiFetch(withTenantQuery(`${REPORT_SERVICE_BASE}/schedules/${scheduleId}`, tenantId), { method: "DELETE" });
  if (!res.ok) {
    throw new Error(await readReportApiError(res));
  }
  return res.json();
}

export async function updateSchedule(
  scheduleId: string,
  tenantId: string,
  data: ScheduleUpdateParams,
): Promise<ScheduleResponse> {
  const res = await apiFetch(withTenantQuery(`${REPORT_SERVICE_BASE}/schedules/${scheduleId}`, tenantId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    throw new Error(await readReportApiError(res));
  }
  return res.json();
}
