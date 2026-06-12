import { buildServiceUrl, getBaseHost, readJson } from "./base";

export type ReportHistoryItem = {
  reportId: string;
  status: string;
  reportType: string;
  createdAt: string | null;
  completedAt: string | null;
};

export type ReportStatusResponse = {
  reportId: string;
  status: string;
  progress: number;
  errorCode?: string | null;
  errorMessage?: string | null;
};

export type EnergyReportPayload = {
  tenant_id?: string;
  device_id: string;
  start_date: string;
  end_date: string;
  report_name?: string;
};

export type ComparisonReportPayload = {
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
};

type RawHistoryResponse = {
  reports?: Array<{
    report_id: string;
    status: string;
    report_type: string;
    created_at: string | null;
    completed_at: string | null;
  }>;
};

type RawReportStatus = {
  report_id: string;
  status: string;
  progress: number;
  error_code?: string | null;
  error_message?: string | null;
};

type RawCreateResponse = {
  report_id: string;
  status: string;
};

const DEFAULT_TENANT_ID = "SH00000001";
const reportsBaseUrl = buildServiceUrl(8085, "/api/reports");

function toTenantId(tenantId?: string) {
  return tenantId ?? DEFAULT_TENANT_ID;
}

export async function getReportsList(tenantId?: string): Promise<ReportHistoryItem[] | null> {
  const query = new URLSearchParams({
    tenant_id: toTenantId(tenantId),
    limit: "20",
    offset: "0",
  });

  const payload = await readJson<RawHistoryResponse>(`${reportsBaseUrl}/history?${query.toString()}`);
  if (!payload) {
    return null;
  }

  return (payload.reports ?? []).map((item) => ({
    reportId: item.report_id,
    status: item.status,
    reportType: item.report_type,
    createdAt: item.created_at,
    completedAt: item.completed_at,
  }));
}

export async function generateEnergyReport(payload: EnergyReportPayload): Promise<RawCreateResponse | null> {
  return readJson<RawCreateResponse>(`${reportsBaseUrl}/energy/consumption`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ...payload,
      tenant_id: toTenantId(payload.tenant_id),
    }),
  });
}

export async function generateComparisonReport(
  payload: ComparisonReportPayload
): Promise<RawCreateResponse | null> {
  return readJson<RawCreateResponse>(`${reportsBaseUrl}/energy/comparison`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ...payload,
      tenant_id: toTenantId(payload.tenant_id),
    }),
  });
}

export async function getReportStatus(
  reportId: string,
  tenantId?: string
): Promise<ReportStatusResponse | null> {
  const query = new URLSearchParams({ tenant_id: toTenantId(tenantId) });
  const payload = await readJson<RawReportStatus>(
    `${reportsBaseUrl}/${reportId}/status?${query.toString()}`
  );

  if (!payload) {
    return null;
  }

  return {
    reportId: payload.report_id,
    status: payload.status,
    progress: payload.progress ?? 0,
    errorCode: payload.error_code ?? null,
    errorMessage: payload.error_message ?? null,
  };
}

export async function getReportResult(reportId: string, tenantId?: string): Promise<unknown | null> {
  const query = new URLSearchParams({ tenant_id: toTenantId(tenantId) });
  return readJson<unknown>(`${reportsBaseUrl}/${reportId}/result?${query.toString()}`);
}

export function getReportDownloadUrl(reportId: string, tenantId?: string) {
  const query = new URLSearchParams({ tenant_id: toTenantId(tenantId) });
  return `${reportsBaseUrl}/${reportId}/download?${query.toString()}`;
}

export function getHealthUrl() {
  return `${getBaseHost()}:8085/health`;
}
