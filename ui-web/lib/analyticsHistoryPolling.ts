"use client";

import type { AnalyticsJobListItem } from "./analyticsApi";

export const ACTIVE_RUNNING_STATUS_POLL_MS = 3_000;
export const ACTIVE_PENDING_STATUS_POLL_MS = 4_000;
export const ACTIVE_HIDDEN_STATUS_POLL_MS = 8_000;
export const HISTORY_REFRESH_WITH_ACTIVE_JOB_MS = 15_000;
export const HISTORY_REFRESH_SINGLE_LIVE_JOB_MS = 10_000;
export const HISTORY_REFRESH_MULTI_LIVE_JOB_MS = 8_000;
export const HISTORY_REFRESH_HIDDEN_MS = 20_000;

function isLiveStatus(status: string | null | undefined): boolean {
  return status === "pending" || status === "running";
}

export function countLiveAnalyticsJobs(jobs: AnalyticsJobListItem[]): number {
  return jobs.filter((job) => isLiveStatus(job.status)).length;
}

export function getAnalyticsStatusPollMs(
  status: AnalyticsJobListItem | null,
  isDocumentHidden: boolean,
): number | null {
  if (!isLiveStatus(status?.status)) {
    return null;
  }
  if (isDocumentHidden) {
    return ACTIVE_HIDDEN_STATUS_POLL_MS;
  }
  return status?.status === "running" ? ACTIVE_RUNNING_STATUS_POLL_MS : ACTIVE_PENDING_STATUS_POLL_MS;
}

export function getAnalyticsHistoryRefreshMs(params: {
  liveHistoryJobCount: number;
  activeJobStatus: AnalyticsJobListItem | null;
  isDocumentHidden: boolean;
}): number | null {
  const { liveHistoryJobCount, activeJobStatus, isDocumentHidden } = params;
  if (liveHistoryJobCount <= 0) {
    return null;
  }
  if (isDocumentHidden) {
    return HISTORY_REFRESH_HIDDEN_MS;
  }
  if (isLiveStatus(activeJobStatus?.status)) {
    return HISTORY_REFRESH_WITH_ACTIVE_JOB_MS;
  }
  return liveHistoryJobCount > 1 ? HISTORY_REFRESH_MULTI_LIVE_JOB_MS : HISTORY_REFRESH_SINGLE_LIVE_JOB_MS;
}

export function mergeHistoryJobStatus(
  jobs: AnalyticsJobListItem[],
  updatedJob: AnalyticsJobListItem,
): AnalyticsJobListItem[] {
  const targetIndex = jobs.findIndex((job) => job.job_id === updatedJob.job_id);
  if (targetIndex < 0) {
    return jobs;
  }

  const nextJobs = jobs.slice();
  nextJobs[targetIndex] = {
    ...nextJobs[targetIndex],
    ...updatedJob,
  };
  return nextJobs;
}

export function resolveSelectedAnalyticsJobId(
  jobs: AnalyticsJobListItem[],
  currentSelectedJobId: string | null,
  protectedJobId?: string | null,
): string | null {
  if (jobs.length === 0) {
    return currentSelectedJobId && currentSelectedJobId === protectedJobId ? currentSelectedJobId : null;
  }

  if (currentSelectedJobId && jobs.some((job) => job.job_id === currentSelectedJobId)) {
    return currentSelectedJobId;
  }

  if (currentSelectedJobId && currentSelectedJobId === protectedJobId) {
    return currentSelectedJobId;
  }

  return jobs[0]?.job_id ?? null;
}
