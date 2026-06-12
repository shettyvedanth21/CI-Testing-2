import type { DashboardBootstrapData, DashboardBootstrapSummaryData } from "./deviceApi";

const MACHINE_DETAIL_RETRYABLE_STATUS_CODES = new Set([408, 429, 502, 503, 504]);

function toErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  if (typeof error === "string" && error) {
    return error;
  }
  return fallback;
}

function parseHttpStatus(message: string): number | null {
  const match = /^HTTP\s+(\d{3})$/i.exec(message.trim());
  if (!match) {
    return null;
  }
  return Number(match[1]);
}

export function isRetryableMachineDetailBootstrapError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  if (error.name === "AbortError") {
    return true;
  }
  const message = error.message.toLowerCase();
  if (message.includes("timed out") || message.includes("timeout") || message.includes("fetch failed")) {
    return true;
  }
  const status = parseHttpStatus(error.message);
  return status !== null && MACHINE_DETAIL_RETRYABLE_STATUS_CODES.has(status);
}

export interface MachineDetailBootstrapLoadResult {
  data: DashboardBootstrapData | null;
  fatalError: string | null;
  attempts: number;
}

export async function loadMachineDetailBootstrap({
  loadBootstrap,
  fallbackError = "Failed to fetch machine dashboard",
  maxAttempts = 2,
  retryDelayMs = 750,
  onRetry,
}: {
  loadBootstrap: () => Promise<DashboardBootstrapData>;
  fallbackError?: string;
  maxAttempts?: number;
  retryDelayMs?: number;
  onRetry?: (attempt: number, error: unknown) => void;
}): Promise<MachineDetailBootstrapLoadResult> {
  let attempt = 0;
  let lastError: unknown = null;

  while (attempt < Math.max(1, maxAttempts)) {
    attempt += 1;
    try {
      const data = await loadBootstrap();
      return {
        data,
        fatalError: null,
        attempts: attempt,
      };
    } catch (error) {
      lastError = error;
      const canRetry = attempt < maxAttempts && isRetryableMachineDetailBootstrapError(error);
      if (!canRetry) {
        break;
      }
      onRetry?.(attempt + 1, error);
      await new Promise((resolve) => globalThis.setTimeout(resolve, retryDelayMs));
    }
  }

  return {
    data: null,
    fatalError: toErrorMessage(lastError, fallbackError),
    attempts: attempt,
  };
}

export interface MachineDetailSummaryLoadResult {
  data: DashboardBootstrapSummaryData | null;
  fatalError: string | null;
  attempts: number;
}

export async function loadMachineDetailSummary({
  loadSummary,
  fallbackError = "Failed to fetch machine summary",
  maxAttempts = 2,
  retryDelayMs = 500,
  onRetry,
}: {
  loadSummary: () => Promise<DashboardBootstrapSummaryData>;
  fallbackError?: string;
  maxAttempts?: number;
  retryDelayMs?: number;
  onRetry?: (attempt: number, error: unknown) => void;
}): Promise<MachineDetailSummaryLoadResult> {
  let attempt = 0;
  let lastError: unknown = null;

  while (attempt < Math.max(1, maxAttempts)) {
    attempt += 1;
    try {
      const data = await loadSummary();
      return {
        data,
        fatalError: null,
        attempts: attempt,
      };
    } catch (error) {
      lastError = error;
      const canRetry = attempt < maxAttempts && isRetryableMachineDetailBootstrapError(error);
      if (!canRetry) {
        break;
      }
      onRetry?.(attempt + 1, error);
      await new Promise((resolve) => globalThis.setTimeout(resolve, retryDelayMs));
    }
  }

  return {
    data: null,
    fatalError: toErrorMessage(lastError, fallbackError),
    attempts: attempt,
  };
}
