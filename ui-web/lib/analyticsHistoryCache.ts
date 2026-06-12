"use client";

import type { AnalyticsJobListItem } from "./analyticsApi";

const STORAGE_KEY_PREFIX = "analytics-history:v1";

export interface AnalyticsHistorySnapshot {
  jobs: AnalyticsJobListItem[];
  page: number;
  hasMore: boolean;
  selectedJobId: string | null;
}

function isAnalyticsJobListItem(value: unknown): value is AnalyticsJobListItem {
  return !!value && typeof value === "object" && typeof (value as { job_id?: unknown }).job_id === "string";
}

function normalizeSnapshot(raw: unknown): AnalyticsHistorySnapshot | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  const parsed = raw as {
    jobs?: unknown;
    page?: unknown;
    hasMore?: unknown;
    selectedJobId?: unknown;
  };

  if (!Array.isArray(parsed.jobs) || !parsed.jobs.every(isAnalyticsJobListItem)) {
    return null;
  }

  const page = typeof parsed.page === "number" && Number.isFinite(parsed.page) && parsed.page >= 0
    ? Math.floor(parsed.page)
    : 0;
  const hasMore = parsed.hasMore === true;
  const selectedJobId = typeof parsed.selectedJobId === "string" && parsed.selectedJobId.trim().length > 0
    ? parsed.selectedJobId
    : null;

  return {
    jobs: parsed.jobs,
    page,
    hasMore,
    selectedJobId,
  };
}

export function buildAnalyticsHistoryStorageKey(scopeKey: string): string {
  return `${STORAGE_KEY_PREFIX}:${scopeKey || "default"}`;
}

export function readAnalyticsHistorySnapshot(scopeKey: string): AnalyticsHistorySnapshot | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.sessionStorage.getItem(buildAnalyticsHistoryStorageKey(scopeKey));
    if (!raw) {
      return null;
    }
    return normalizeSnapshot(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function writeAnalyticsHistorySnapshot(scopeKey: string, snapshot: AnalyticsHistorySnapshot): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.setItem(
      buildAnalyticsHistoryStorageKey(scopeKey),
      JSON.stringify(snapshot),
    );
  } catch {
    // Ignore browser storage failures so history rendering still works.
  }
}
