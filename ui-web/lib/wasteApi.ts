import { WASTE_SERVICE_BASE } from "./api";
import { apiFetch } from "./apiFetch";
import { extractFilename } from "./downloadFilename";
import { readResponseError } from "./responseError";
import type { TelemetryCoverageResult } from "./telemetryCoverage";

export class WasteApiError extends Error {
  status: number;
  body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "WasteApiError";
    this.status = status;
    this.body = body;
  }
}

export type WasteScope = "all" | "selected";
export type WasteGranularity = "daily" | "weekly" | "monthly";
export type WasteJobStatusValue = "pending" | "running" | "completed" | "failed";

export interface WasteRunParams {
  job_name?: string;
  scope: WasteScope;
  device_ids?: string[] | null;
  start_date: string;
  end_date: string;
  granularity: WasteGranularity;
}

export interface WasteJobSummary {
  job_id: string;
  job_name?: string;
  status: WasteJobStatusValue;
  backend_status?: string;
  estimated_completion_seconds?: number | null;
  progress_pct: number;
  stage?: string | null;
  phase?: string | null;
  phase_label?: string | null;
  phase_progress?: number | null;
  result_ready?: boolean;
  artifact_ready?: boolean;
  download_ready?: boolean;
  result_url?: string | null;
  download_url?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  scope?: WasteScope | null;
  start_date?: string | null;
  end_date?: string | null;
  granularity?: WasteGranularity | null;
  requested_device_count?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  coverage_result?: TelemetryCoverageResult | null;
}

export interface WasteRunResponse extends WasteJobSummary {
  estimated_completion_seconds: number;
}

export type WasteStatus = WasteJobSummary;

export type WasteHistoryItem = WasteJobSummary;

export interface WasteHistoryResponse {
  items: WasteHistoryItem[];
}

export interface WasteDownloadResponse {
  job_id: string;
  status: WasteJobStatusValue;
  download_url: string;
  expires_in_seconds: number;
  result_ready?: boolean;
  artifact_ready?: boolean;
  download_ready?: boolean;
}

async function readWasteApiBody(res: Response): Promise<unknown> {
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

async function requestWasteJson<T = unknown>(url: string, options?: RequestInit): Promise<T> {
  const res = await apiFetch(url, options);
  const body = await readWasteApiBody(res);
  if (!res.ok) {
    const message = await readResponseError(res).catch(() => {
      const candidate = body as Record<string, unknown> | string | null;
      return (
        (typeof candidate === "object" &&
          candidate !== null &&
          (candidate.message ??
            (candidate.error as Record<string, unknown> | undefined)?.message ??
            (candidate.detail as Record<string, unknown> | undefined)?.message)) ||
        (typeof candidate === "string" ? candidate : null) ||
        `HTTP ${res.status}`
      );
    });
    throw new WasteApiError(String(message), res.status, body);
  }
  return body as T;
}

export async function runWasteAnalysis(params: WasteRunParams): Promise<WasteRunResponse> {
  return requestWasteJson<WasteRunResponse>(`${WASTE_SERVICE_BASE}/analysis/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export async function getWasteStatus(jobId: string): Promise<WasteStatus> {
  return requestWasteJson<WasteStatus>(`${WASTE_SERVICE_BASE}/analysis/${jobId}/status`);
}

export async function getWasteResult(jobId: string): Promise<unknown> {
  return requestWasteJson<unknown>(`${WASTE_SERVICE_BASE}/analysis/${jobId}/result`);
}

export async function getWasteDownload(jobId: string): Promise<WasteDownloadResponse> {
  return requestWasteJson<WasteDownloadResponse>(`${WASTE_SERVICE_BASE}/analysis/${jobId}/download`);
}

export async function downloadWastePdf(jobId: string): Promise<{ blob: Blob; filename: string }> {
  const { download_url } = await getWasteDownload(jobId);
  const res = await apiFetch(download_url);
  if (!res.ok) {
    const body = await readWasteApiBody(res);
    const message =
      (typeof body === "string" ? body : null) ||
      `HTTP ${res.status}`;
    throw new WasteApiError(String(message), res.status, body);
  }
  const blob = await res.blob();
  const filename = extractFilename(res.headers.get("Content-Disposition"), `waste_report_${jobId}.pdf`);
  return { blob, filename };
}

export async function getWasteHistory(limit = 20, offset = 0): Promise<WasteHistoryResponse> {
  return requestWasteJson<WasteHistoryResponse>(`${WASTE_SERVICE_BASE}/analysis/history?limit=${limit}&offset=${offset}`);
}
