export type DeviceRuntimeStatus = "running" | "stopped";
export type DeviceLoadState = "running" | "idle" | "overconsumption" | "unloaded" | "unknown";
export type DeviceOperatingBand = "unloaded" | "idle" | "in_load" | "overconsumption" | "unknown";
export type DeviceOperationalStatus = "unknown" | "stopped" | "idle" | "running" | "overconsumption";
export type StatusMergeSource = "snapshot" | "stream_full" | "stream_partial" | "bootstrap" | "current_state_poll";

export const DEVICE_STATUS_TELEMETRY_TIMEOUT_MS = 60_000;

export const DEVICE_OPERATIONAL_STATUS_ORDER: DeviceOperationalStatus[] = [
  "overconsumption",
  "running",
  "idle",
  "stopped",
  "unknown",
];

export function resolveOperationalStatus({
  runtimeStatus,
  loadState,
  currentBand,
  hasTelemetry,
}: {
  runtimeStatus?: string | null;
  loadState?: string | null;
  currentBand?: string | null;
  hasTelemetry?: boolean;
}): DeviceOperationalStatus {
  const runtime = (runtimeStatus ?? "").trim().toLowerCase();
  const load = (loadState ?? "").trim().toLowerCase();
  const band = (currentBand ?? "").trim().toLowerCase();

  if (runtime !== "running") {
    return hasTelemetry ? "stopped" : "unknown";
  }
  if (load === "overconsumption" || band === "overconsumption") {
    return "overconsumption";
  }
  if (load === "idle" || band === "idle") {
    return "idle";
  }
  if (load === "running" || band === "in_load") {
    return "running";
  }
  return "unknown";
}

export function isTelemetryTimestampFresh(
  timestamp: string | null | undefined,
  nowMs: number = Date.now(),
  telemetryTimeoutMs: number = DEVICE_STATUS_TELEMETRY_TIMEOUT_MS,
): boolean {
  if (!timestamp) return false;
  const parsedMs = Date.parse(timestamp);
  if (!Number.isFinite(parsedMs)) return false;
  return nowMs - parsedMs <= telemetryTimeoutMs;
}

export function preserveKnownStatusAgainstTransientUnknown({
  currentOperationalStatus,
  currentLoadState,
  incomingOperationalStatus,
  incomingLoadState,
  incomingRuntimeStatus,
  incomingLastSeenTimestamp,
  source,
  nowMs = Date.now(),
  telemetryTimeoutMs = DEVICE_STATUS_TELEMETRY_TIMEOUT_MS,
}: {
  currentOperationalStatus: DeviceOperationalStatus;
  currentLoadState: DeviceLoadState;
  incomingOperationalStatus: DeviceOperationalStatus;
  incomingLoadState: DeviceLoadState;
  incomingRuntimeStatus?: string | null;
  incomingLastSeenTimestamp?: string | null;
  source: StatusMergeSource;
  nowMs?: number;
  telemetryTimeoutMs?: number;
}): { operationalStatus: DeviceOperationalStatus; loadState: DeviceLoadState } {
  if (incomingOperationalStatus !== "unknown") {
    return {
      operationalStatus: incomingOperationalStatus,
      loadState: incomingLoadState,
    };
  }
  if (currentOperationalStatus === "unknown") {
    return {
      operationalStatus: incomingOperationalStatus,
      loadState: incomingLoadState,
    };
  }

  const runtime = (incomingRuntimeStatus ?? "").trim().toLowerCase();
  const telemetryFresh = isTelemetryTimestampFresh(
    incomingLastSeenTimestamp,
    nowMs,
    telemetryTimeoutMs,
  );
  const unknownFromWeakPartial = source === "stream_partial" || source === "current_state_poll";
  const shouldHoldKnownStatus = unknownFromWeakPartial && runtime === "running" && telemetryFresh;

  if (shouldHoldKnownStatus) {
    return {
      operationalStatus: currentOperationalStatus,
      loadState: incomingLoadState === "unknown" ? currentLoadState : incomingLoadState,
    };
  }

  return {
    operationalStatus: incomingOperationalStatus,
    loadState: incomingLoadState,
  };
}

export function mergeCurrentStateWithStability<
  T extends {
    state?: string | null;
    current_band?: string | null;
    timestamp?: string | null;
  },
>(
  current: T | null | undefined,
  incoming: T | null | undefined,
  options: {
    runtimeStatus?: string | null;
    source: StatusMergeSource;
    nowMs?: number;
    telemetryTimeoutMs?: number;
  },
): T | null | undefined {
  if (!incoming) return incoming;
  if (!current) return incoming;

  const currentState = (current.state ?? "unknown").trim().toLowerCase();
  const incomingState = (incoming.state ?? "unknown").trim().toLowerCase();
  if (incomingState !== "unknown" || currentState === "unknown") {
    return incoming;
  }

  const runtime = (options.runtimeStatus ?? "").trim().toLowerCase();
  const telemetryFresh = isTelemetryTimestampFresh(
    incoming.timestamp,
    options.nowMs,
    options.telemetryTimeoutMs,
  );
  const unknownFromWeakSource = options.source === "current_state_poll" || options.source === "stream_partial";
  const shouldHoldKnown = unknownFromWeakSource && runtime === "running" && telemetryFresh;

  if (!shouldHoldKnown) {
    return incoming;
  }

  return {
    ...incoming,
    state: current.state,
    current_band:
      (incoming.current_band ?? "").trim().toLowerCase() === "unknown"
        ? current.current_band
        : incoming.current_band,
  };
}

export function getOperationalStatusMeta(status: DeviceOperationalStatus): {
  label: string;
  className: string;
} {
  switch (status) {
    case "overconsumption":
      return { label: "Overconsumption", className: "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-200" };
    case "running":
      return { label: "In Load", className: "bg-emerald-100 text-emerald-800 border-emerald-200" };
    case "idle":
      return { label: "Idle", className: "bg-amber-100 text-amber-800 border-amber-200" };
    case "stopped":
      return { label: "Stopped", className: "bg-rose-100 text-rose-800 border-rose-200" };
    default:
      return { label: "Unknown", className: "bg-slate-100 text-slate-700 border-slate-200" };
  }
}
