import { buildServiceUrl, readJson, rewriteLocalhostUrl } from "./base";

export type WasteJob = {
  jobId: string;
  jobName: string | null;
  status: string;
  errorCode: string | null;
  errorMessage: string | null;
  createdAt: string | null;
  completedAt: string | null;
  progressPct: number;
};

export type WasteStatus = {
  jobId: string;
  status: string;
  progressPct: number;
  stage: string | null;
  errorCode: string | null;
  errorMessage: string | null;
};

export type WasteRunPayload = {
  job_name?: string;
  scope: "all" | "selected";
  device_ids?: string[] | null;
  start_date: string;
  end_date: string;
  granularity: "daily" | "weekly" | "monthly";
};

type WasteHistoryResponse = {
  items?: Array<{
    job_id: string;
    job_name?: string | null;
    status: string;
    error_code?: string | null;
    error_message?: string | null;
    created_at?: string | null;
    completed_at?: string | null;
    progress_pct: number;
  }>;
};

type WasteRunResponse = {
  job_id: string;
  status: string;
  estimated_completion_seconds: number;
};

type RawWasteStatus = {
  job_id: string;
  status: string;
  progress_pct: number;
  stage?: string | null;
  error_code?: string | null;
  error_message?: string | null;
};

type WasteDownloadResponse = {
  download_url: string;
};

const wasteBaseUrl = buildServiceUrl(8087, "/api/v1/waste");

export async function getWasteJobs(): Promise<WasteJob[] | null> {
  const payload = await readJson<WasteHistoryResponse>(`${wasteBaseUrl}/analysis/history?limit=20&offset=0`);
  if (!payload) {
    return null;
  }

  return (payload.items ?? []).map((item) => ({
    jobId: item.job_id,
    jobName: item.job_name ?? null,
    status: item.status,
    errorCode: item.error_code ?? null,
    errorMessage: item.error_message ?? null,
    createdAt: item.created_at ?? null,
    completedAt: item.completed_at ?? null,
    progressPct: item.progress_pct ?? 0,
  }));
}

export async function runWasteAnalysis(payload: WasteRunPayload): Promise<WasteRunResponse | null> {
  return readJson<WasteRunResponse>(`${wasteBaseUrl}/analysis/run`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function getWasteStatus(jobId: string): Promise<WasteStatus | null> {
  const payload = await readJson<RawWasteStatus>(`${wasteBaseUrl}/analysis/${jobId}/status`);
  if (!payload) {
    return null;
  }

  return {
    jobId: payload.job_id,
    status: payload.status,
    progressPct: payload.progress_pct ?? 0,
    stage: payload.stage ?? null,
    errorCode: payload.error_code ?? null,
    errorMessage: payload.error_message ?? null,
  };
}

export async function getWasteResult(jobId: string): Promise<unknown | null> {
  return readJson<unknown>(`${wasteBaseUrl}/analysis/${jobId}/result`);
}

export async function getWasteDownloadUrl(jobId: string): Promise<string | null> {
  const payload = await readJson<WasteDownloadResponse>(`${wasteBaseUrl}/analysis/${jobId}/download`);
  return payload?.download_url ? rewriteLocalhostUrl(payload.download_url) : null;
}
