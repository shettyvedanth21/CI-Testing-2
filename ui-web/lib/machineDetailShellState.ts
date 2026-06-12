import type {
  CurrentState,
  DashboardBootstrapSummaryData,
  DashboardLossOverview,
  DashboardOverviewReadiness,
  Device,
} from "./deviceApi";
import { resolveOperationalStatus, type DeviceLoadState, type DeviceOperationalStatus } from "./deviceStatus";

export interface MachineDetailShellMachine {
  id: string;
  name: string;
  type: string;
  location: string;
  runtime_status: Device["runtime_status"];
  last_seen_timestamp: string | null;
  first_telemetry_timestamp: string | null;
  data_source_type: string | null | undefined;
}

export interface MachineDetailShellState {
  machine: MachineDetailShellMachine;
  healthPercent: number | null;
  uptimePercent: number | null;
  effectiveLoadState: DeviceLoadState;
  currentBand: string;
  operationalStatus: DeviceOperationalStatus;
  lossOverview: DashboardLossOverview | null;
  overviewReadiness: DashboardOverviewReadiness;
}

function parseIsoTimestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function summaryFreshnessRank(summary: DashboardBootstrapSummaryData): [number, number, number, number] {
  return [
    Number(summary.version || 0),
    parseIsoTimestamp(summary.live_updated_at) ?? -1,
    parseIsoTimestamp(summary.last_seen_timestamp) ?? -1,
    parseIsoTimestamp(summary.generated_at) ?? -1,
  ];
}

export function shouldAcceptIncomingShellSummary(
  current: DashboardBootstrapSummaryData | null,
  incoming: DashboardBootstrapSummaryData,
): boolean {
  if (!current) {
    return true;
  }

  const currentRank = summaryFreshnessRank(current);
  const incomingRank = summaryFreshnessRank(incoming);
  for (let index = 0; index < incomingRank.length; index += 1) {
    if (incomingRank[index] > currentRank[index]) {
      return true;
    }
    if (incomingRank[index] < currentRank[index]) {
      return false;
    }
  }
  return true;
}

function buildFallbackMachine(machine: Device | null): MachineDetailShellMachine {
  return {
    id: machine?.id ?? "",
    name: machine?.name ?? "",
    type: machine?.type ?? "",
    location: machine?.location ?? "",
    runtime_status: machine?.runtime_status ?? "stopped",
    last_seen_timestamp: machine?.last_seen_timestamp ?? null,
    first_telemetry_timestamp: machine?.first_telemetry_timestamp ?? null,
    data_source_type: machine?.data_source_type,
  };
}

export function buildSyntheticMachineFromSummary(
  summary: DashboardBootstrapSummaryData,
): MachineDetailShellMachine {
  return {
    id: summary.device_id,
    name: summary.device_name,
    type: summary.device_type,
    location: summary.location ?? "",
    runtime_status: summary.runtime_status as Device["runtime_status"],
    last_seen_timestamp: summary.last_seen_timestamp,
    first_telemetry_timestamp: summary.first_telemetry_timestamp,
    data_source_type: summary.data_source_type,
  };
}

export function deriveMachineDetailShellState({
  summary,
  shellCurrentState,
  fallbackMachine,
  fallbackHealthPercent,
  fallbackUptimePercent,
}: {
  summary: DashboardBootstrapSummaryData | null;
  shellCurrentState: CurrentState | null;
  fallbackMachine: Device | null;
  fallbackHealthPercent: number | null;
  fallbackUptimePercent: number | null;
}): MachineDetailShellState {
  const machine = summary ? buildSyntheticMachineFromSummary(summary) : buildFallbackMachine(fallbackMachine);
  const effectiveLoadState = (shellCurrentState?.state ?? summary?.load_state ?? "unknown") as DeviceLoadState;
  const currentBand = shellCurrentState?.current_band ?? summary?.current_band ?? "unknown";

  return {
    machine,
    healthPercent: summary?.health_score ?? fallbackHealthPercent,
    uptimePercent: summary?.current_shift_uptime_percentage ?? fallbackUptimePercent ?? null,
    effectiveLoadState,
    currentBand,
    lossOverview: summary?.loss_overview ?? null,
    overviewReadiness: summary?.overview_readiness ?? {
      summary_ready: false,
      telemetry_ready: Boolean(machine.last_seen_timestamp),
      health_ready: summary?.health_score != null || fallbackHealthPercent != null,
      uptime_ready: summary?.current_shift_uptime_percentage != null || fallbackUptimePercent != null,
      loss_ready: false,
    },
    operationalStatus: resolveOperationalStatus({
      runtimeStatus: machine.runtime_status,
      loadState: effectiveLoadState,
      currentBand,
      hasTelemetry: Boolean(machine.last_seen_timestamp),
    }),
  };
}
