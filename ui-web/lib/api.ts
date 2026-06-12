import { apiFetch } from "./apiFetch";

export const DEVICE_SERVICE_BASE = "/backend/device";
export const DATA_SERVICE_BASE   = "/backend/data";
export const RULE_ENGINE_SERVICE_BASE = "/backend/rule-engine";
export const ANALYTICS_SERVICE_BASE   = "/backend/analytics";
export const DATA_EXPORT_SERVICE_BASE = "/backend/data-export";
export const COPILOT_SERVICE_BASE = "/backend/copilot";
export const REPORT_SERVICE_BASE = "/api/reports";
export const WASTE_SERVICE_BASE = "/api/waste";

const SERVICE_STARTED_AT_HEADER = "x-service-started-at";

let lastObservedBackendSession: string | null = null;
const backendSessionListeners = new Set<(nextSession: string, previousSession: string | null) => void>();

export function getLastObservedBackendSession(): string | null {
  return lastObservedBackendSession;
}

export function subscribeToBackendSessionChanges(
  listener: (nextSession: string, previousSession: string | null) => void,
): () => void {
  backendSessionListeners.add(listener);
  return () => {
    backendSessionListeners.delete(listener);
  };
}

export function readBackendSessionHeader(response: Response): string | null {
  const headerValue = response.headers.get(SERVICE_STARTED_AT_HEADER);
  if (!headerValue) {
    return null;
  }

  const trimmedValue = headerValue.trim();
  return trimmedValue.length > 0 ? trimmedValue : null;
}

export async function fetchWithBackendSession(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await apiFetch(String(input), init);
  const serviceStartedAt = readBackendSessionHeader(response);
  if (serviceStartedAt) {
    const previousSession = lastObservedBackendSession;
    lastObservedBackendSession = serviceStartedAt;
    if (previousSession && previousSession !== serviceStartedAt) {
      backendSessionListeners.forEach((listener) => listener(serviceStartedAt, previousSession));
    }
  }
  return response;
}
