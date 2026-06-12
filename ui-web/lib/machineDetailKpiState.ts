export type MachineKpiState =
  | {
      kind: "ready";
      title: null;
      message: null;
    }
  | {
      kind: "loading";
      title: string;
      message: string;
    }
  | {
      kind: "degraded";
      title: string;
      message: string;
    }
  | {
      kind: "waiting_for_telemetry";
      title: string;
      message: string;
    };

export function deriveMachineKpiState({
  hydrationLoading,
  hydrationFailed,
  hydrationError,
  hasTelemetry,
  dynamicMetricCount,
}: {
  hydrationLoading: boolean;
  hydrationFailed: boolean;
  hydrationError: string | null;
  hasTelemetry: boolean;
  dynamicMetricCount: number;
}): MachineKpiState {
  if (hydrationFailed) {
    return {
      kind: "degraded",
      title: "Detailed KPIs unavailable",
      message:
        hydrationError ||
        "Telemetry-backed KPI cards could not be loaded. Summary status is shown above, but detailed overview metrics are unavailable right now.",
    };
  }

  if (hydrationLoading) {
    return {
      kind: "loading",
      title: "Detailed KPIs loading",
      message: "Telemetry-backed KPI cards are still hydrating in the background.",
    };
  }

  if (!hasTelemetry || dynamicMetricCount === 0) {
    return {
      kind: "waiting_for_telemetry",
      title: "Detailed KPIs waiting for telemetry",
      message:
        "This machine has not produced numeric telemetry for overview KPI cards yet. Summary status remains visible above.",
    };
  }

  return {
    kind: "ready",
    title: null,
    message: null,
  };
}
