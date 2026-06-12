"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import {
  getDeviceById,
  getDashboardBootstrap,
  getDashboardBootstrapSummary,
  Device,
  DashboardBootstrapSummaryData,
  DeviceDetailSnapshotData,
  getDeviceDetailSnapshot,
  getIdleConfig,
  saveIdleConfig,
  getCurrentState,
  CurrentState,
  getShifts,
  createShift,
  updateShift,
  deleteShift,
  Shift,
  ShiftCreate,
  getUptime,
  UptimeData,
  getHealthConfigs,
  createHealthConfig,
  deleteHealthConfig,
  updateHealthConfig,
  HealthConfig,
  HealthConfigCreate,
  calculateHealthScore,
  HealthScore,
  ParameterScore,
  TelemetryValues,
  validateHealthWeights,
  WeightValidation,
  getPerformanceTrends,
  PerformanceTrendData,
  PerformanceTrendRange,
  PerformanceTrendMetric,
  DashboardWidgetConfig,
  getDashboardWidgetConfig,
  saveDashboardWidgetConfig,
  getMaintenanceLogRecords,
  getMaintenanceLogSummary,
  MaintenanceLogRecord,
  MaintenanceLogSummary,
  MaintenanceLogMutationInput,
  createMaintenanceLogRecord,
  updateMaintenanceLogRecord,
  deleteMaintenanceLogRecord,
  getDegradationScore,
  DegradationScore,
  getAnomalyActivity,
  AnomalyActivity,
} from "@/lib/deviceApi";
import {
  TelemetryPoint,
  getTelemetryHistory,
  getTelemetryWebsocketTicket,
  getActivityEvents,
  getActivityUnreadCount,
  markAllActivityRead,
  clearActivityHistory,
  ActivityEvent,
  acknowledgeAlert,
  resolveAlert,
  isTelemetryHistoryUnavailableError,
} from "@/lib/dataApi";
import {
  getActivityHistoryDegradedMessage,
  isActivityHistoryAbortError,
  isTransientActivityHistoryError,
} from "@/lib/activityHistoryResilience";
import { DATA_SERVICE_BASE } from "@/lib/api";
import { buildPerformanceTrendDisplayModel } from "@/lib/performanceTrendDisplay";
import {
  findHealthConfigForMetric,
  findMatchingHealthConfigsForMetric,
  findParameterScoreForMetric,
  matchesHealthParameterKey,
} from "@/lib/healthScoring";
import { formatCurrencyValue, formatCo2Kg, formatCo2Footnote, formatEnergyKwh } from "@/lib/presentation";
import {
  EXCLUSIVE_LOSS_BUCKET_HELP,
  OVERCONSUMPTION_THRESHOLD_HELP,
  deriveThresholdsFromFla,
  formatIdleThresholdPctLabel,
  getEngineeringSaveBlockReason,
  hasUnsavedEngineeringDraft,
  getOutsideShiftFinancialBucketMessage,
  parseEngineeringNumberDraft,
} from "@/lib/wasteSemantics";
import { getVisibleDeviceDetailTabs } from "@/lib/deviceDetailTabs";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TimeSeriesChart } from "@/components/charts/telemetry-charts";
import { isPhaseDiagnosticField } from "@/lib/telemetryContract";
import { MachineRulesView } from "@/app/(protected)/machines/[deviceId]/rules/machine-rules-view";
import { formatIST, getRelativeTime } from "@/lib/utils";
import { ActivationTimestampField } from "@/components/devices/ActivationTimestampField";
import { DegradationScoreCard } from "@/components/devices/DegradationScoreCard";
import { AnomalyActivityCard } from "@/components/devices/AnomalyActivityCard";
import {
  getOperationalStatusMeta,
  mergeCurrentStateWithStability,
  type DeviceLoadState,
  type DeviceOperationalStatus,
} from "@/lib/deviceStatus";
import { useAdaptivePolling } from "@/lib/useAdaptivePolling";
import { loadMachineDetailBootstrap, loadMachineDetailSummary } from "@/lib/machineDetailLoadContract";
import { deriveMachineKpiState } from "@/lib/machineDetailKpiState";
import {
  buildSyntheticMachineFromSummary,
  deriveMachineDetailShellState,
  shouldAcceptIncomingShellSummary,
} from "@/lib/machineDetailShellState";
import { usePermissions } from "@/hooks/usePermissions";
import { ReadOnlyBanner } from "@/components/auth/ReadOnlyBanner";
import { LockedPremiumCard } from "@/components/auth/LockedPremiumCard";
import { useAuth } from "@/lib/authContext";
import { hasFeature } from "@/lib/features";
import {
  buildMaintenanceFormValues,
  formatMaintenanceCostInput,
  formatMaintenanceDate,
  MAINTENANCE_STATUS_OPTIONS,
  normalizeMaintenanceApiError,
  truncateDescription,
  type MaintenanceLogFormValues,
  validateMaintenanceForm,
} from "@/lib/maintenanceLog";

const METRIC_LABELS: Record<string, string> = {
  power: "Power", voltage: "Voltage (Avg)", current: "Current (Avg)", temperature: "Temperature",
  current_l1: "Current L1", current_l2: "Current L2", current_l3: "Current L3",
  voltage_l1: "Voltage L1", voltage_l2: "Voltage L2", voltage_l3: "Voltage L3",
  pressure: "Pressure", humidity: "Humidity", vibration: "Vibration", frequency: "Frequency",
  power_factor: "Power Factor", speed: "Speed", torque: "Torque", oil_pressure: "Oil Pressure",
};

const METRIC_UNITS: Record<string, string> = {
  power: " W", voltage: " V", current: " A", temperature: " °C",
  pressure: " bar", humidity: " %", vibration: " mm/s", frequency: " Hz",
  power_factor: "", speed: " RPM", torque: " Nm", oil_pressure: " bar",
};

const METRIC_COLORS: Record<string, string> = {
  power: "#2563eb", voltage: "#d97706", current: "#7c3aed", temperature: "#dc2626",
  pressure: "#059669", humidity: "#0891b2", vibration: "#ea580c", frequency: "#4f46e5",
  power_factor: "#8b5cf6", speed: "#0d9488", torque: "#be185d", oil_pressure: "#65a30d",
};

const METRIC_RANGES: Record<string, [number, number]> = {
  power: [0, 500], voltage: [200, 250], current: [0, 20], temperature: [0, 120],
  pressure: [0, 10], humidity: [0, 100], vibration: [0, 10], frequency: [45, 55],
  power_factor: [0.8, 1.0], speed: [1000, 2000], torque: [0, 500], oil_pressure: [0, 5],
};

const DAYS_OF_WEEK = [
  { value: null, label: "All Days" },
  { value: 0, label: "Monday" }, { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" }, { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" }, { value: 5, label: "Saturday" }, { value: 6, label: "Sunday" },
];

const TREND_RANGE_OPTIONS: { label: string; value: PerformanceTrendRange }[] = [
  { label: "30m", value: "30m" },
  { label: "1h", value: "1h" },
  { label: "6h", value: "6h" },
  { label: "24h", value: "24h" },
  { label: "7d", value: "7d" },
  { label: "30d", value: "30d" },
];

type OverviewChartRange = "live" | "6h" | "24h" | "7d";

const OVERVIEW_CHART_RANGE_OPTIONS: { label: string; value: OverviewChartRange; description: string }[] = [
  { label: "Live", value: "live", description: "Recent live buffer" },
  { label: "6h", value: "6h", description: "Historical telemetry, 1m average" },
  { label: "24h", value: "24h", description: "Historical telemetry, 5m average" },
  { label: "7d", value: "7d", description: "Historical telemetry, 15m average" },
];

const RECENT_TELEMETRY_BUFFER_SIZE = 200;
const RECENT_TELEMETRY_PAGE_SIZE = 10;
const MACHINE_DETAIL_DEFERRED_HYDRATION_TIMEOUT_MS = 15_000;
const MACHINE_DETAIL_FALLBACK_BOOTSTRAP_TIMEOUT_MS = 15_000;

type DevicePageTab = "overview" | "telemetry" | "maintenance" | "parameters" | "rules";

type ShiftSegment = {
  day: number;
  start: number;
  end: number;
};

function toMinutes(timeValue: string): number | null {
  const parts = timeValue.split(":");
  if (parts.length < 2) return null;
  const hour = Number(parts[0]);
  const minute = Number(parts[1]);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return hour * 60 + minute;
}

function toDisplayTime(timeValue: string): string {
  const parts = timeValue.split(":");
  if (parts.length < 2) return timeValue;
  const hh = parts[0].padStart(2, "0");
  const mm = parts[1].padStart(2, "0");
  return `${hh}:${mm}`;
}

function isOvernightRange(startTime: string, endTime: string): boolean {
  const start = toMinutes(startTime);
  const end = toMinutes(endTime);
  if (start === null || end === null) return false;
  return end <= start;
}

function formatShiftRange(startTime: string, endTime: string): string {
  const overnight = isOvernightRange(startTime, endTime);
  return `${toDisplayTime(startTime)} - ${toDisplayTime(endTime)}${overnight ? " (+1 day)" : ""}`;
}

function buildShiftSegments(startTime: string, endTime: string, dayOfWeek: number | null): ShiftSegment[] {
  const start = toMinutes(startTime);
  const end = toMinutes(endTime);
  if (start === null || end === null || start === end) return [];

  const days = dayOfWeek === null ? [0, 1, 2, 3, 4, 5, 6] : [dayOfWeek];
  const segments: ShiftSegment[] = [];
  for (const day of days) {
    if (end > start) {
      segments.push({ day, start, end });
      continue;
    }
    segments.push({ day, start, end: 24 * 60 });
    segments.push({ day: (day + 1) % 7, start: 0, end });
  }
  return segments;
}

function hasSegmentOverlap(a: ShiftSegment, b: ShiftSegment): boolean {
  if (a.day !== b.day) return false;
  return a.start < b.end && b.start < a.end;
}

function findOverlapConflicts(candidate: ShiftCreate, existingShifts: Shift[], excludeShiftId: number | null = null): Shift[] {
  const candidateSegments = buildShiftSegments(candidate.shift_start, candidate.shift_end, candidate.day_of_week ?? null);
  if (candidateSegments.length === 0) return [];

  return existingShifts.filter((shift) => {
    if (excludeShiftId !== null && shift.id === excludeShiftId) {
      return false;
    }
    const shiftSegments = buildShiftSegments(shift.shift_start, shift.shift_end, shift.day_of_week);
    return candidateSegments.some((cand) => shiftSegments.some((seg) => hasSegmentOverlap(cand, seg)));
  });
}

function getDynamicMetrics(telemetry: TelemetryPoint[]): string[] {
  const metrics = new Set<string>();
  for (const point of telemetry) {
    for (const [key, value] of Object.entries(point)) {
      if (key !== 'timestamp' && key !== 'device_id' && key !== 'schema_version' &&
          key !== 'enrichment_status' && key !== 'table' && typeof value === 'number') {
        metrics.add(key);
      }
    }
  }
  return Array.from(metrics);
}

function getNumericMetricKeys(values: Record<string, number> | null | undefined): string[] {
  if (!values) return [];
  return Object.entries(values)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .map(([key]) => key);
}

function getMetricData(telemetry: TelemetryPoint[], metric: string) {
  return telemetry
    .map((t) => {
      const value = t[metric];
      return typeof value === "number" ? { timestamp: t.timestamp, value } : null;
    })
    .filter((item): item is { timestamp: string; value: number } => item !== null);
}

function getOverviewHistoryParams(range: OverviewChartRange): Record<string, string> | null {
  if (range === "live") {
    return null;
  }

  const end = new Date();
  const start = new Date(end);
  let interval = "1m";
  let limit = "1000";

  if (range === "6h") {
    start.setHours(end.getHours() - 6);
    interval = "1m";
    limit = "1000";
  } else if (range === "24h") {
    start.setHours(end.getHours() - 24);
    interval = "5m";
    limit = "1000";
  } else if (range === "7d") {
    start.setDate(end.getDate() - 7);
    interval = "15m";
    limit = "1000";
  }

  return {
    start_time: start.toISOString(),
    end_time: end.toISOString(),
    aggregate: "mean",
    interval,
    limit,
  };
}

function formatLossOverviewCost(value: number | null | undefined, currency: string, costsAvailable: boolean): string {
  if (!costsAvailable || value == null) {
    return "Set tariff in Settings";
  }
  return formatCurrencyValue(Number(value || 0), currency || "INR");
}

function sortTelemetryAsc(items: TelemetryPoint[]): TelemetryPoint[] {
  return [...items].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );
}

function sortTelemetryDesc(items: TelemetryPoint[]): TelemetryPoint[] {
  return [...items].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );
}

function mergeTelemetryAsc(existing: TelemetryPoint[], incoming: TelemetryPoint[]): TelemetryPoint[] {
  const byTimestamp = new Map<string, TelemetryPoint>();
  for (const point of [...existing, ...incoming]) {
    if (!point?.timestamp) continue;
    byTimestamp.set(point.timestamp, point);
  }
  return sortTelemetryAsc(Array.from(byTimestamp.values()));
}

function mergeTelemetryDesc(existing: TelemetryPoint[], incoming: TelemetryPoint[]): TelemetryPoint[] {
  const byTimestamp = new Map<string, TelemetryPoint>();
  for (const point of [...existing, ...incoming]) {
    if (!point?.timestamp) continue;
    byTimestamp.set(point.timestamp, point);
  }
  return sortTelemetryDesc(Array.from(byTimestamp.values()));
}

function formatTimestamp(ts: string): string {
  return formatIST(ts, ts);
}

function formatMinutes(totalMinutes: number | null | undefined): string {
  if (typeof totalMinutes !== "number" || Number.isNaN(totalMinutes) || totalMinutes < 0) {
    return "—";
  }
  const rounded = Math.round(totalMinutes);
  const hrs = Math.floor(rounded / 60);
  const mins = rounded % 60;
  return `${hrs}h ${mins}m`;
}

function formatTelemetryMetricValue(metric: string, value: unknown): string {
  if (typeof value !== "number") {
    return "—";
  }
  return `${value.toFixed(2)}${METRIC_UNITS[metric] || ""}`;
}

function TelemetryRowCard({
  point,
  metrics,
}: {
  point: TelemetryPoint;
  metrics: string[];
}) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
            Telemetry Sample
          </p>
          <p className="mt-1 break-all font-mono text-sm text-slate-900">
            {formatTimestamp(point.timestamp)}
          </p>
        </div>
      </div>
      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {metrics.map((metric) => (
          <div key={metric} className="rounded-xl bg-slate-50 px-3 py-2">
            <dt className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">
              {METRIC_LABELS[metric] || metric}
              {isPhaseDiagnosticField(metric) ? " • Diagnostic" : ""}
            </dt>
            <dd className="mt-1 text-sm font-semibold text-slate-900">
              {formatTelemetryMetricValue(metric, point[metric])}
            </dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

function formatEventType(eventType: string): string {
  return eventType
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function UptimeCircle({ uptime, onClick }: { uptime: UptimeData | null; onClick: () => void }) {
  const percentage = uptime?.uptime_percentage ?? 0;
  const color = percentage >= 95 ? "#22c55e" : percentage >= 80 ? "#eab308" : "#ef4444";
  
  return (
    <div className="relative cursor-pointer group" onClick={onClick}>
      <div className="w-16 h-16">
        <svg className="w-full h-full transform -rotate-90">
          <circle cx="32" cy="32" r="28" stroke="#e2e8f0" strokeWidth="6" fill="none" />
          <circle cx="32" cy="32" r="28" stroke={color} strokeWidth="6" fill="none"
            strokeDasharray={`${(percentage / 100) * 176} 176`} className="transition-all duration-500" />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-xs font-bold">{percentage.toFixed(0)}%</span>
        </div>
      </div>
      
      <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 w-48 bg-white shadow-lg rounded-lg border p-3 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50">
        <p className="text-xs font-semibold text-slate-700 mb-2">Uptime Details</p>
        {uptime ? (
          <>
            <p className="text-xs text-slate-600">Active Shifts: <span className="font-medium">{uptime.shifts_configured}</span></p>
            {uptime.uptime_percentage === null ? (
              <p className="text-xs text-amber-700 mt-2">{uptime.message || "No active shift window right now."}</p>
            ) : (
              <>
                <p className="text-xs text-slate-600">Planned: <span className="font-medium">{formatMinutes(uptime.total_planned_minutes)}</span></p>
                <p className="text-xs text-slate-600">Effective: <span className="font-medium">{formatMinutes(uptime.total_effective_minutes)}</span></p>
                <p className="text-xs text-slate-600">Running: <span className="font-medium">{formatMinutes(uptime.actual_running_minutes)}</span></p>
                <p className="text-xs text-slate-500 mt-2">Uptime = running minutes / effective shift minutes.</p>
              </>
            )}
          </>
        ) : (
          <p className="text-xs text-slate-500">No shifts configured</p>
        )}
      </div>
    </div>
  );
}

function HealthScoreCircle({ healthScore, onClick }: { healthScore: HealthScore | null; onClick: () => void }) {
  const hasScore = typeof healthScore?.health_score === "number";
  const score = hasScore ? (healthScore?.health_score as number) : 0;
  const statusColor = healthScore?.status_color || "⚪";
  
  const colorMap: Record<string, string> = {
    "🟢": "#22c55e", "🟡": "#eab308", "🟠": "#f97316", "🔴": "#ef4444", "⚪": "#94a3b8"
  };
  const color = healthScore ? colorMap[statusColor] || "#94a3b8" : "#94a3b8";
  const isStandby = healthScore?.status === "Standby";
  
  return (
    <div className="relative cursor-pointer group" onClick={onClick}>
      <div className="w-16 h-16">
        <svg className="w-full h-full transform -rotate-90">
          <circle cx="32" cy="32" r="28" stroke="#e2e8f0" strokeWidth="6" fill="none" />
          <circle cx="32" cy="32" r="28" stroke={color} strokeWidth="6" fill="none"
            strokeDasharray={`${(score / 100) * 176} 176`} className="transition-all duration-500" />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xs font-bold">{isStandby || !hasScore ? "—" : `${score.toFixed(0)}%`}</span>
          <span className="text-[10px]">{isStandby ? "Standby" : statusColor}</span>
        </div>
      </div>
      
      <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 w-56 bg-white shadow-lg rounded-lg border p-3 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50">
        <p className="text-xs font-semibold text-slate-700 mb-2">Health Score Details</p>
        {healthScore ? (
          <>
            <p className="text-xs text-slate-600">Status: <span className="font-medium">{healthScore.status} {healthScore.status_color}</span></p>
            <p className="text-xs text-slate-600">Machine State: <span className="font-medium">{healthScore.machine_state}</span></p>
            <p className="text-xs text-slate-600">Parameters: <span className="font-medium">{healthScore.parameters_included} included, {healthScore.parameters_skipped} skipped</span></p>
            <p className="text-xs text-slate-600">Total Weight: <span className="font-medium">{healthScore.total_weight_configured}%</span></p>
            {healthScore.parameter_scores.length > 0 && (
              <div className="mt-2 border-t pt-2">
                <p className="text-xs font-medium text-slate-700">Parameter Scores:</p>
                {healthScore.parameter_scores.slice(0, 5).map((p) => (
                  <p key={p.parameter_name} className="text-xs text-slate-600">
                    {p.parameter_name}: {p.raw_score !== null ? `${p.raw_score}%` : p.status} {p.status_color}
                  </p>
                ))}
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-slate-500">No health data</p>
        )}
      </div>
    </div>
  );
}

function OperationalStatusBadge({ status }: { status: DeviceOperationalStatus }) {
  const item = getOperationalStatusMeta(status);
  return (
    <span className={`inline-flex max-w-full items-center rounded-full border px-3 py-1 text-center text-xs font-semibold leading-tight [overflow-wrap:anywhere] ${item.className}`}>
      {item.label}
    </span>
  );
}

function getDetailedLoadStateLabel(state: DeviceLoadState | undefined): string {
  if (state === "running") return "In Load";
  if (state === "idle") return "Idle";
  if (state === "overconsumption") return "Overconsumption";
  if (state === "unloaded") return "Unloaded";
  return "Unknown";
}

function getBackendStatusBadge(statusColor: string | null | undefined): { color: string; bgColor: string } {
  if (statusColor === "🟢") return { color: "text-green-700", bgColor: "bg-green-100" };
  if (statusColor === "🟡") return { color: "text-yellow-700", bgColor: "bg-yellow-100" };
  if (statusColor === "🟠") return { color: "text-orange-700", bgColor: "bg-orange-100" };
  if (statusColor === "🔴") return { color: "text-red-700", bgColor: "bg-red-100" };
  return { color: "text-slate-600", bgColor: "bg-slate-100" };
}

function ParameterEfficiencyCard({
  metric, 
  value, 
  healthConfig,
  parameterScore,
  onConfigure 
}: { 
  metric: string; 
  value: number; 
  healthConfig: HealthConfig | null;
  parameterScore: ParameterScore | null;
  onConfigure: () => void;
}) {
  const { canEditDevice } = usePermissions();
  const fallbackRange = METRIC_RANGES[metric] || [0, 100];
  const min = healthConfig?.normal_min ?? fallbackRange[0];
  const max = healthConfig?.normal_max ?? fallbackRange[1];
  const denominator = Math.max(max - min, 1);
  const valuePct = Math.max(0, Math.min(100, ((value - min) / denominator) * 100));

  const normalMin = healthConfig?.normal_min ?? null;
  const normalMax = healthConfig?.normal_max ?? null;
  const hasNormalRange = normalMin !== null && normalMax !== null;
  const normalStartPct = hasNormalRange ? Math.max(0, Math.min(100, ((normalMin - min) / denominator) * 100)) : null;
  const normalEndPct = hasNormalRange ? Math.max(0, Math.min(100, ((normalMax - min) / denominator) * 100)) : null;

  const score = parameterScore?.raw_score ?? null;
  const status = getBackendStatusBadge(parameterScore?.status_color);
  const displayLabel = parameterScore?.status || (healthConfig ? "Awaiting backend score" : "Display only");
  const scoreLabel = score !== null ? `Score ${score.toFixed(0)}% • ${displayLabel}` : displayLabel;
  const telemetryLabel =
    parameterScore?.telemetry_key && !matchesHealthParameterKey(parameterScore.telemetry_key, metric)
      ? `Resolved from ${parameterScore.telemetry_key}`
      : null;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <p className="text-[11px] uppercase tracking-[0.18em] font-semibold text-slate-500">
            {METRIC_LABELS[metric] || metric}
          </p>
          <p className="text-3xl font-bold text-slate-900 mt-2">
            {value.toFixed(2)}
            <span className="text-lg font-semibold text-slate-500 ml-1">{METRIC_UNITS[metric]?.trim() || ""}</span>
          </p>
        </div>
        {canEditDevice ? (
          <button
            onClick={onConfigure}
            className="text-xs font-medium px-2.5 py-1.5 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-100"
          >
            {healthConfig ? "Edit Range" : "Set Range"}
          </button>
        ) : null}
      </div>

      <div className="relative h-3 rounded-full bg-slate-200 overflow-hidden">
        {hasNormalRange && normalStartPct !== null && normalEndPct !== null && (
          <div
            className="absolute top-0 h-full bg-emerald-100"
            style={{ left: `${Math.min(normalStartPct, normalEndPct)}%`, width: `${Math.abs(normalEndPct - normalStartPct)}%` }}
          />
        )}
        <div
          className="absolute left-0 top-0 h-full rounded-full transition-all duration-500"
          style={{
            width: `${valuePct}%`,
            background: "linear-gradient(90deg, #4f46e5 0%, #6366f1 70%, #818cf8 100%)",
          }}
        />
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-slate-600 sm:grid-cols-2 lg:grid-cols-3">
        <p>Min: <span className="font-semibold text-slate-800">{min.toFixed(2)}</span></p>
        <p>Max: <span className="font-semibold text-slate-800">{max.toFixed(2)}</span></p>
        <p className="text-right">
          Normal band:{" "}
          <span className="font-semibold text-slate-800">
            {hasNormalRange ? `${(normalMin as number).toFixed(2)}-${(normalMax as number).toFixed(2)}` : "Not set"}
          </span>
        </p>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <div className={`text-xs px-2 py-1 rounded-full font-medium ${status.color} ${status.bgColor}`}>
          {scoreLabel}
        </div>
        <div className="text-xs text-slate-500 text-right">
          {healthConfig ? (
            <div>
              Weight: <span className="font-semibold text-slate-700">{healthConfig.weight}%</span>
            </div>
          ) : (
            <div>Display only</div>
          )}
          {telemetryLabel ? <div>{telemetryLabel}</div> : null}
        </div>
      </div>
    </div>
  );
}

function HealthConfigModal({ 
  isOpen, 
  onClose, 
  deviceId, 
  metric,
  existingConfig,
  allConfigs,
  onSave,
  onDelete 
}: { 
  isOpen: boolean; 
  onClose: () => void; 
  deviceId: string;
  metric: string;
  existingConfig: HealthConfig | null;
  allConfigs: HealthConfig[];
  onSave: (config: HealthConfigCreate) => void;
  onDelete: (configId: number) => Promise<void>;
}) {
  const { canDeleteDevice } = usePermissions();
  const initialFormData = useMemo<HealthConfigCreate>(() => {
    if (existingConfig) {
      return {
        parameter_name: existingConfig.parameter_name,
        normal_min: existingConfig.normal_min ?? undefined,
        normal_max: existingConfig.normal_max ?? undefined,
        weight: existingConfig.weight,
        ignore_zero_value: existingConfig.ignore_zero_value,
        is_active: existingConfig.is_active,
      };
    }

    const defaultRanges: Record<string, [number, number]> = {
      pressure: [2, 6],
      temperature: [20, 60],
      vibration: [0, 3],
      power: [100, 400],
      voltage: [210, 240],
      current: [2, 15],
      frequency: [48, 52],
      power_factor: [0.85, 1.0],
      speed: [1200, 1800],
      torque: [50, 300],
      oil_pressure: [1, 4],
      humidity: [30, 70],
    };
    const defaults = defaultRanges[metric];
    return {
      parameter_name: metric,
      normal_min: defaults?.[0] ?? undefined,
      normal_max: defaults?.[1] ?? undefined,
      weight: 0,
      ignore_zero_value: false,
      is_active: true,
    };
  }, [existingConfig, metric]);

  const [formData, setFormData] = useState<HealthConfigCreate>(initialFormData);
  const [normalMinInput, setNormalMinInput] = useState(
    initialFormData.normal_min == null ? "" : String(initialFormData.normal_min),
  );
  const [normalMaxInput, setNormalMaxInput] = useState(
    initialFormData.normal_max == null ? "" : String(initialFormData.normal_max),
  );
  const [deleteInFlight, setDeleteInFlight] = useState(false);
  const [saveInFlight, setSaveInFlight] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setFormData(initialFormData);
    setNormalMinInput(initialFormData.normal_min == null ? "" : String(initialFormData.normal_min));
    setNormalMaxInput(initialFormData.normal_max == null ? "" : String(initialFormData.normal_max));
    setValidationError(null);
    setDeleteInFlight(false);
    setSaveInFlight(false);
  }, [initialFormData, isOpen]);
  
  if (!isOpen) return null;
  
  const totalWeight = allConfigs
    .filter(c => c.is_active && !matchesHealthParameterKey(c.parameter_name, metric))
    .reduce((sum, c) => sum + c.weight, 0) + formData.weight;
  
  const otherWeightsSum = allConfigs
    .filter(c => c.is_active && !matchesHealthParameterKey(c.parameter_name, metric))
    .reduce((sum, c) => sum + c.weight, 0);
  
  const remainingWeight = 100 - otherWeightsSum;
  const currentWeight = existingConfig?.weight || 0;
  const maxAllowedWeight = remainingWeight + currentWeight;
  const isWeightValid = Math.abs(totalWeight - 100) < 0.01;
  
  const handleWeightChange = (value: number) => {
    // Allow any value that's within the allowed range (remaining + current weight)
    // This allows decreasing weight when editing
    if (!isNaN(value) && value >= 0 && value <= maxAllowedWeight) {
      setFormData({ ...formData, weight: value });
    }
  };

  const handleSaveClick = async () => {
    if (saveInFlight || deleteInFlight) {
      return;
    }
    const parsedNormalMin = normalMinInput.trim().length === 0 ? undefined : Number(normalMinInput.trim());
    const parsedNormalMax = normalMaxInput.trim().length === 0 ? undefined : Number(normalMaxInput.trim());

    if (normalMinInput.trim().length > 0 && !Number.isFinite(parsedNormalMin)) {
      setValidationError("Normal Min must be a finite number.");
      return;
    }
    if (normalMaxInput.trim().length > 0 && !Number.isFinite(parsedNormalMax)) {
      setValidationError("Normal Max must be a finite number.");
      return;
    }
    if (
      parsedNormalMin != null &&
      parsedNormalMax != null &&
      parsedNormalMin > parsedNormalMax
    ) {
      setValidationError("Normal Min cannot be greater than Normal Max.");
      return;
    }
    setValidationError(null);
    setSaveInFlight(true);
    try {
      await onSave({
        ...formData,
        normal_min: parsedNormalMin,
        normal_max: parsedNormalMax,
      });
    } finally {
      setSaveInFlight(false);
    }
  };
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">Configure Health: {metric}</h3>
          <button
            onClick={onClose}
            disabled={saveInFlight || deleteInFlight}
            className="text-slate-400 hover:text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            ✕
          </button>
        </div>
        
        <div className="space-y-4">
          <div className="p-3 bg-blue-50 rounded text-sm">
            <p className="font-medium text-blue-800 mb-2">Normal Range</p>
            <p className="text-blue-600 text-xs">Values inside this band receive the full 100% parameter score.</p>
          </div>
          
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-sm font-medium mb-1">Normal Min</label>
              <input
                type="text"
                inputMode="decimal"
                value={normalMinInput}
                onChange={(e) => setNormalMinInput(e.target.value)}
                disabled={saveInFlight || deleteInFlight}
                className="w-full px-3 py-2 border rounded-md"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Normal Max</label>
              <input
                type="text"
                inputMode="decimal"
                value={normalMaxInput}
                onChange={(e) => setNormalMaxInput(e.target.value)}
                disabled={saveInFlight || deleteInFlight}
                className="w-full px-3 py-2 border rounded-md"
              />
            </div>
          </div>
          
            <div className="border-t pt-4">
              <label className="block text-sm font-medium mb-1">
                Weight (%) 
                {existingConfig && <span className="text-xs text-slate-500 font-normal ml-2">(Saved: {currentWeight}%, Max: {maxAllowedWeight}%)</span>}
              </label>
              <input 
                type="number" 
                min="0" 
                max={maxAllowedWeight}
                step="1" 
                value={formData.weight} 
                onChange={(e) => handleWeightChange(parseFloat(e.target.value) || 0)}
                disabled={saveInFlight || deleteInFlight}
                className="w-full px-3 py-2 border rounded-md" 
              />
              <div className={`text-xs mt-2 p-2 rounded ${isWeightValid ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
                <p>Total Weight: <strong>{totalWeight.toFixed(1)}%</strong> / 100%</p>
                <p>Remaining: <strong>{remainingWeight.toFixed(1)}%</strong></p>
                {!isWeightValid && <p className="mt-1">⚠️ Total must equal 100% to calculate health score</p>}
                {isWeightValid && <p className="mt-1">✓ Weight configured correctly</p>}
              </div>
            </div>
          
          <div className="flex items-center gap-2">
            <input type="checkbox" id="ignoreZero" checked={formData.ignore_zero_value} onChange={(e) => setFormData({ ...formData, ignore_zero_value: e.target.checked })} disabled={saveInFlight || deleteInFlight} className="rounded" />
            <label htmlFor="ignoreZero" className="text-sm">Ignore zero values (exclude from scoring when machine is off)</label>
          </div>

          {saveInFlight ? (
            <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-700">
              Saving configuration and refreshing machine details...
            </div>
          ) : null}

          {validationError ? (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {validationError}
            </div>
          ) : null}
          
          {existingConfig && canDeleteDevice && (
            <Button
              variant="danger"
              className="w-full"
              disabled={saveInFlight || deleteInFlight}
              onClick={async () => {
                if (saveInFlight || deleteInFlight) return;
                setDeleteInFlight(true);
                try {
                  await onDelete(existingConfig.id);
                } finally {
                  setDeleteInFlight(false);
                }
              }}
            >
              {deleteInFlight ? "Deleting..." : "Delete Configuration"}
            </Button>
          )}
        </div>
        
        <div className="flex gap-2 mt-6">
          <Button variant="outline" onClick={onClose} disabled={saveInFlight || deleteInFlight} className="flex-1">Cancel</Button>
          <Button onClick={() => void handleSaveClick()} disabled={saveInFlight || deleteInFlight} className="flex-1">
            {saveInFlight ? "Saving..." : isWeightValid ? "Save" : `Save (${totalWeight.toFixed(0)}%)`}
          </Button>
        </div>
        {!isWeightValid && (
          <p className="text-xs text-center mt-2 text-amber-600">
            ⚠️ Note: Health score will only calculate when total weight = 100%
          </p>
        )}
      </div>
    </div>
  );
}

function MaintenanceLogFormModal({
  isOpen,
  record,
  onClose,
  onSubmit,
  isSubmitting,
  error,
}: {
  isOpen: boolean;
  record: MaintenanceLogRecord | null;
  onClose: () => void;
  onSubmit: (payload: MaintenanceLogMutationInput) => Promise<void>;
  isSubmitting: boolean;
  error: string | null;
}) {
  const titleId = useId();
  const maintenanceDateId = `${titleId}-maintenance-date`;
  const costId = `${titleId}-cost`;
  const issueTitleId = `${titleId}-issue-title`;
  const notesId = `${titleId}-notes`;
  const performedById = `${titleId}-performed-by`;
  const statusId = `${titleId}-status`;
  const nextDueDateId = `${titleId}-next-due-date`;
  const isEditing = Boolean(record);
  const [formValues, setFormValues] = useState<MaintenanceLogFormValues>(buildMaintenanceFormValues(record));
  const [validationError, setValidationError] = useState<string | null>(null);

  if (!isOpen) {
    return null;
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const result = validateMaintenanceForm(formValues);
    if (!result.payload) {
      setValidationError(result.error);
      return;
    }
    setValidationError(null);
    await onSubmit(result.payload);
  }

  const displayError = validationError || error;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button
        type="button"
        aria-label="Close maintenance form"
        className="absolute inset-0"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 flex max-h-[90vh] w-full max-w-[640px] flex-col overflow-hidden rounded-[1.5rem] border border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-[var(--shadow-raised)]"
      >
        <div className="border-b border-[var(--border-subtle)] px-5 py-4">
          <h2 id={titleId} className="text-lg font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            {isEditing ? "Edit Maintenance Record" : "Add Maintenance"}
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Keep this machine’s service history clear and easy for the team to understand.
          </p>
        </div>

        <form className="space-y-5 overflow-y-auto p-5" onSubmit={(event) => void handleSubmit(event)}>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <label htmlFor={maintenanceDateId} className="mb-1 block text-sm font-medium text-slate-700">
                Maintenance date <span className="text-rose-500">*</span>
              </label>
              <input
                id={maintenanceDateId}
                type="date"
                value={formValues.maintenance_date}
                onChange={(event) => setFormValues((current) => ({ ...current, maintenance_date: event.target.value }))}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900"
              />
            </div>
            <div>
              <label htmlFor={costId} className="mb-1 block text-sm font-medium text-slate-700">
                Cost <span className="text-rose-500">*</span>
              </label>
              <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-500">INR</span>
                  <input
                    id={costId}
                    type="text"
                    inputMode="decimal"
                    value={formValues.cost}
                    onChange={(event) =>
                      setFormValues((current) => ({
                        ...current,
                        cost: formatMaintenanceCostInput(event.target.value),
                      }))
                    }
                    placeholder="0.00"
                    className="w-full border-0 p-0 text-sm text-slate-900 outline-none"
                  />
                </div>
              </div>
              <p className="mt-1 text-xs text-slate-500">Use numbers only, for example 1250 or 1250.50.</p>
            </div>
          </div>

          <div>
            <label htmlFor={issueTitleId} className="mb-1 block text-sm font-medium text-slate-700">
              Issue title <span className="text-rose-500">*</span>
            </label>
            <input
              id={issueTitleId}
              type="text"
              value={formValues.title}
              onChange={(event) => setFormValues((current) => ({ ...current, title: event.target.value }))}
              placeholder="Example: Oil change and filter replacement"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900"
            />
          </div>

          <div>
            <label htmlFor={notesId} className="mb-1 block text-sm font-medium text-slate-700">
              Notes <span className="text-rose-500">*</span>
            </label>
            <textarea
              id={notesId}
              rows={5}
              value={formValues.description}
              onChange={(event) => setFormValues((current) => ({ ...current, description: event.target.value }))}
              placeholder="Describe what happened, what was fixed, or what was replaced."
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900"
            />
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div>
              <label htmlFor={performedById} className="mb-1 block text-sm font-medium text-slate-700">Performed by</label>
              <input
                id={performedById}
                type="text"
                value={formValues.performed_by}
                onChange={(event) => setFormValues((current) => ({ ...current, performed_by: event.target.value }))}
                placeholder="Technician or vendor"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900"
              />
            </div>
            <div>
              <label htmlFor={statusId} className="mb-1 block text-sm font-medium text-slate-700">Status</label>
              <select
                id={statusId}
                value={formValues.status}
                onChange={(event) => setFormValues((current) => ({ ...current, status: event.target.value }))}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900"
              >
                {MAINTENANCE_STATUS_OPTIONS.map((option) => (
                  <option key={option.value || "blank"} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={nextDueDateId} className="mb-1 block text-sm font-medium text-slate-700">Next due date</label>
              <input
                id={nextDueDateId}
                type="date"
                value={formValues.next_due_date}
                onChange={(event) => setFormValues((current) => ({ ...current, next_due_date: event.target.value }))}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900"
              />
              <p className="mt-1 text-xs text-slate-500">Leave blank if nothing is scheduled yet.</p>
            </div>
          </div>

          {displayError ? (
            <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-4 py-3 text-sm text-[var(--tone-danger-text)]">
              {displayError}
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? (isEditing ? "Saving..." : "Adding...") : isEditing ? "Save Changes" : "Add Maintenance"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function DeleteMaintenanceLogDialog({
  isOpen,
  record,
  onClose,
  onConfirm,
  isDeleting,
  error,
}: {
  isOpen: boolean;
  record: MaintenanceLogRecord | null;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  isDeleting: boolean;
  error: string | null;
}) {
  const titleId = useId();

  if (!isOpen || !record) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button
        type="button"
        aria-label="Close delete maintenance dialog"
        className="absolute inset-0"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 w-full max-w-[460px] rounded-[1.5rem] border border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-[var(--shadow-raised)]"
      >
        <div className="border-b border-[var(--border-subtle)] px-5 py-4">
          <h2 id={titleId} className="text-lg font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            Delete Maintenance Record
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Are you sure you want to remove this maintenance entry?
          </p>
        </div>

        <div className="space-y-4 p-5">
          <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-3">
            <p className="text-base font-semibold text-[var(--text-primary)]">{record.title}</p>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">
              {formatMaintenanceDate(record.maintenance_date, "—")} • {formatCurrencyValue(record.cost, "INR")}
            </p>
          </div>

          <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            This will remove the record from the machine’s maintenance history and update the summary above.
          </div>

          {error ? (
            <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-3 py-2 text-sm text-[var(--tone-danger-text)]">
              {error}
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={isDeleting}>
              Cancel
            </Button>
            <Button type="button" variant="danger" onClick={() => void onConfirm()} disabled={isDeleting}>
              {isDeleting ? "Deleting..." : "Delete Record"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function MachineDashboardPage() {
  const { me } = useAuth();
  const machineHealthEnabled = hasFeature(me, "machine_health");
  const { canEditDevice, canDeleteDevice, canCreateRule, canAcknowledgeAlert, isReadOnly } = usePermissions();
  const params = useParams();
  const deviceId = (params.deviceId as string) || "";

  const [machine, setMachine] = useState<Device | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryPoint[]>([]);
  const [telemetryStreamRows, setTelemetryStreamRows] = useState<TelemetryPoint[]>([]);
  const [telemetryTablePage, setTelemetryTablePage] = useState(1);
  const [telemetryHistoryRows, setTelemetryHistoryRows] = useState<TelemetryPoint[]>([]);
  const [telemetryHistoryLoading, setTelemetryHistoryLoading] = useState(false);
  const [telemetryHistoryLoaded, setTelemetryHistoryLoaded] = useState(false);
  const [telemetryHistoryError, setTelemetryHistoryError] = useState<string | null>(null);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [uptime, setUptime] = useState<UptimeData | null>(null);
  const [healthConfigs, setHealthConfigs] = useState<HealthConfig[]>([]);
  const [healthScore, setHealthScore] = useState<HealthScore | null>(null);
  const [degradationScore, setDegradationScore] = useState<DegradationScore | null>(null);
  const [degradationLoading, setDegradationLoading] = useState(false);
  const [degradationError, setDegradationError] = useState<string | null>(null);
  const [anomalyActivity, setAnomalyActivity] = useState<AnomalyActivity | null>(null);
  const [anomalyLoading, setAnomalyLoading] = useState(false);
  const [anomalyError, setAnomalyError] = useState<string | null>(null);
  const [healthStaleRefresh, setHealthStaleRefresh] = useState(false);
  const healthPollFailRef = useRef(0);
  const [currentState, setCurrentState] = useState<CurrentState | null>(null);
  const [fullLoadCurrentInput, setFullLoadCurrentInput] = useState<string>("");
  const [persistedFullLoadCurrent, setPersistedFullLoadCurrent] = useState<number | null>(null);
  const [idleThresholdPctInput, setIdleThresholdPctInput] = useState<string>("");
  const [persistedIdleThresholdPct, setPersistedIdleThresholdPct] = useState<number | null>(null);
  const [engineeringSaveMessage, setEngineeringSaveMessage] = useState<string>("");
  const [engineeringSaving, setEngineeringSaving] = useState(false);
  const [widgetConfig, setWidgetConfig] = useState<DashboardWidgetConfig | null>(null);
  const [selectedWidgetFields, setSelectedWidgetFields] = useState<string[]>([]);
  const [widgetSaveMessage, setWidgetSaveMessage] = useState<string>("");
  const [widgetSaving, setWidgetSaving] = useState(false);
  const [widgetDirty, setWidgetDirty] = useState(false);
  const [maintenanceRecords, setMaintenanceRecords] = useState<MaintenanceLogRecord[]>([]);
  const [maintenanceSummary, setMaintenanceSummary] = useState<MaintenanceLogSummary | null>(null);
  const [maintenanceLoading, setMaintenanceLoading] = useState(false);
  const [maintenanceLoaded, setMaintenanceLoaded] = useState(false);
  const [maintenanceError, setMaintenanceError] = useState<string | null>(null);
  const [showMaintenanceModal, setShowMaintenanceModal] = useState(false);
  const [maintenanceEditingRecord, setMaintenanceEditingRecord] = useState<MaintenanceLogRecord | null>(null);
  const [maintenanceSubmitting, setMaintenanceSubmitting] = useState(false);
  const [maintenanceSubmitError, setMaintenanceSubmitError] = useState<string | null>(null);
  const [maintenanceDeleteTarget, setMaintenanceDeleteTarget] = useState<MaintenanceLogRecord | null>(null);
  const [maintenanceDeleting, setMaintenanceDeleting] = useState(false);
  const [maintenanceDeleteError, setMaintenanceDeleteError] = useState<string | null>(null);
  const [maintenanceActionMessage, setMaintenanceActionMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingMessage, setLoadingMessage] = useState<string>("Loading machine dashboard...");
  const [hydrationLoading, setHydrationLoading] = useState(false);
  const [hydrationError, setHydrationError] = useState<string | null>(null);
  const [detailSnapshot, setDetailSnapshot] = useState<DeviceDetailSnapshotData | null>(null);
  const [shellSummary, setShellSummary] = useState<DashboardBootstrapSummaryData | null>(null);
  const shellSummaryRef = useRef<DashboardBootstrapSummaryData | null>(null);
  const hydrationInFlightRef = useRef(false);
  const bootstrapHydrationInFlightRef = useRef(false);
  const [activeTab, setActiveTab] = useState<DevicePageTab>("overview");
  const [showAddShift, setShowAddShift] = useState(false);
  const [showHealthConfig, setShowHealthConfig] = useState(false);
  const [selectedMetric, setSelectedMetric] = useState<string>("");
  const [showAlertHistory, setShowAlertHistory] = useState(false);
  const [activityEvents, setActivityEvents] = useState<ActivityEvent[]>([]);
  const [unreadEventCount, setUnreadEventCount] = useState(0);
  const [activityHistoryError, setActivityHistoryError] = useState<string | null>(null);
  const [activityHistoryLoading, setActivityHistoryLoading] = useState(false);
  const [activityHistoryLoaded, setActivityHistoryLoaded] = useState(false);
  const [alertActionMessage, setAlertActionMessage] = useState<string | null>(null);
  const [alertActionBusyId, setAlertActionBusyId] = useState<string | null>(null);
  const [trendMetric, setTrendMetric] = useState<PerformanceTrendMetric>("health");
  const [trendRange, setTrendRange] = useState<PerformanceTrendRange>("1h");
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendError, setTrendError] = useState<string | null>(null);
  const [trendData, setTrendData] = useState<PerformanceTrendData | null>(null);
  const [trendSectionPrimed, setTrendSectionPrimed] = useState(false);
  const [overviewChartRange, setOverviewChartRange] = useState<OverviewChartRange>("live");
  const [overviewHistoryTelemetry, setOverviewHistoryTelemetry] = useState<TelemetryPoint[]>([]);
  const [overviewHistoryLoading, setOverviewHistoryLoading] = useState(false);
  const [overviewHistoryError, setOverviewHistoryError] = useState<string | null>(null);
  const [shellCurrentState, setShellCurrentState] = useState<CurrentState | null>(null);
  const [newShift, setNewShift] = useState<ShiftCreate>({
    shift_name: "", shift_start: "09:00", shift_end: "17:00", maintenance_break_minutes: 0, day_of_week: null, is_active: true,
  });
  const [editingShiftId, setEditingShiftId] = useState<number | null>(null);
  const latestTelemetryTimestampRef = useRef<string | null>(null);
  const telemetryWsRef = useRef<WebSocket | null>(null);
  const telemetryWsConnectAttemptRef = useRef(0);
  const activityHistoryAbortRef = useRef<AbortController | null>(null);
  const activityHistoryRequestIdRef = useRef(0);
  const activityEventsRef = useRef<ActivityEvent[]>([]);
  const overviewHistoryRequestIdRef = useRef(0);
  const trendSectionRef = useRef<HTMLDivElement | null>(null);
  const visibleTabs: ReadonlyArray<{ id: DevicePageTab; label: string }> = getVisibleDeviceDetailTabs({
    isReadOnly,
    canEditDevice,
    canCreateRule,
  });
  const activeTabVisible = visibleTabs.some((tab) => tab.id === activeTab);

  const commitShellSummary = (summary: DashboardBootstrapSummaryData): DashboardBootstrapSummaryData => {
    const current = shellSummaryRef.current;
    if (!shouldAcceptIncomingShellSummary(current, summary)) {
      return current ?? summary;
    }
    shellSummaryRef.current = summary;
    setShellSummary(summary);
    setMachine(buildSyntheticMachineFromSummary(summary) as Device);
    const summaryFreshness = Date.parse(
      summary.live_updated_at ?? summary.last_seen_timestamp ?? summary.generated_at,
    );
    setShellCurrentState((previous) => {
      if (!previous) {
        return previous;
      }
      const currentFreshness = Date.parse(previous.timestamp ?? "");
      if (Number.isFinite(summaryFreshness) && Number.isFinite(currentFreshness) && currentFreshness > summaryFreshness) {
        return previous;
      }
      return null;
    });
    return summary;
  };

  const fetchSummary = async (): Promise<DashboardBootstrapSummaryData | null> => {
    setLoadingMessage("Loading machine status...");
    const summaryResult = await loadMachineDetailSummary({
      loadSummary: () => getDashboardBootstrapSummary(deviceId),
      fallbackError: "Failed to fetch machine summary",
      onRetry: () => {
        setLoadingMessage("Machine status is taking longer than usual. Retrying...");
      },
    });
    const summary = summaryResult.data;
    if (!summary) {
      return null;
    }
    commitShellSummary(summary);
    setError(null);
    setLoading(false);
    setLoadingMessage("Loading machine dashboard...");
    return summary;
  };

  const refreshShellSummary = async () => {
    try {
      const summary = await getDashboardBootstrapSummary(deviceId);
      commitShellSummary(summary);
    } catch (err) {
      console.error("Failed to refresh machine shell summary:", err);
    }
  };

  const fetchHydration = async () => {
    if (hydrationInFlightRef.current) {
      return;
    }
    hydrationInFlightRef.current = true;
    try {
      setHydrationLoading(true);
      setHydrationError(null);
      const snapshot = await getDeviceDetailSnapshot(deviceId);
      setDetailSnapshot(snapshot);
      setHealthConfigs(snapshot.health_configs);
      setWidgetConfig(snapshot.widget_config);
      setTelemetryHistoryError(null);
      if (!widgetDirty) {
        setSelectedWidgetFields(snapshot.widget_config?.effective_fields || []);
        setWidgetDirty(false);
      }
      setHealthScore(snapshot.health_score);
      if (snapshot.recent_telemetry.length > 0) {
        const recentSeed = snapshot.recent_telemetry as TelemetryPoint[];
        const ascSeed = sortTelemetryAsc(recentSeed);
        const descSeed = sortTelemetryDesc(recentSeed);
        setTelemetry((previous) => mergeTelemetryAsc(previous, ascSeed).slice(-100));
        setTelemetryStreamRows((previous) => mergeTelemetryDesc(previous, descSeed).slice(0, RECENT_TELEMETRY_BUFFER_SIZE));
        setTelemetryTablePage(1);
        if (descSeed[0]?.timestamp) {
          const incomingTs = Date.parse(descSeed[0].timestamp);
          const currentTs = Date.parse(latestTelemetryTimestampRef.current ?? "");
          if (!Number.isFinite(currentTs) || incomingTs > currentTs) {
            latestTelemetryTimestampRef.current = descSeed[0].timestamp;
          }
        }
      }
    } catch (err) {
      setHydrationError(err instanceof Error ? err.message : "Detailed machine KPIs could not be loaded.");
    } finally {
      hydrationInFlightRef.current = false;
      setHydrationLoading(false);
      void fetchDeferredBootstrap();
    }
  };

  const fetchDeferredBootstrap = async () => {
    if (bootstrapHydrationInFlightRef.current) {
      return;
    }
    bootstrapHydrationInFlightRef.current = true;
    try {
      const bootstrapResult = await loadMachineDetailBootstrap({
        loadBootstrap: () =>
          getDashboardBootstrap(deviceId, {
            timeoutMs: MACHINE_DETAIL_DEFERRED_HYDRATION_TIMEOUT_MS,
          }),
        fallbackError: "Detailed machine dashboard sections could not be loaded.",
        maxAttempts: 2,
      });
      const bootstrap = bootstrapResult.data;
      if (!bootstrap) {
        return;
      }
      const machineData = bootstrap.device ?? (await getDeviceById(deviceId));
      const ascTelemetry = sortTelemetryAsc(bootstrap.telemetry ?? []);
      const descTelemetry = sortTelemetryDesc(bootstrap.telemetry ?? []);
      if (ascTelemetry.length > 0) {
        setTelemetry((previous) => mergeTelemetryAsc(previous, ascTelemetry).slice(-100));
        if (!detailSnapshot?.availability.recent_telemetry_ready && telemetryStreamRows.length === 0) {
          setTelemetryStreamRows(descTelemetry.slice(0, RECENT_TELEMETRY_BUFFER_SIZE));
          setTelemetryTablePage(1);
          latestTelemetryTimestampRef.current = descTelemetry[0]?.timestamp || null;
        }
      }
      if (!shellSummaryRef.current) {
        setMachine(machineData);
      }
      setUptime(bootstrap.uptime);
      setShifts(bootstrap.shifts);
      setCurrentState((previous) =>
        mergeCurrentStateWithStability(previous, bootstrap.current_state, {
          runtimeStatus: machineData?.runtime_status,
          source: "bootstrap",
        }) ?? previous ?? null,
      );
      setFullLoadCurrentInput(
        bootstrap.idle_config?.full_load_current_a != null
          ? String(bootstrap.idle_config.full_load_current_a)
          : ""
      );
      setPersistedFullLoadCurrent(
        bootstrap.idle_config?.full_load_current_a != null
          ? Number(bootstrap.idle_config.full_load_current_a)
          : null,
      );
      setIdleThresholdPctInput(
        bootstrap.idle_config?.idle_threshold_pct_of_fla != null
          ? String(bootstrap.idle_config.idle_threshold_pct_of_fla)
          : ""
      );
      setPersistedIdleThresholdPct(
        bootstrap.idle_config?.idle_threshold_pct_of_fla != null
          ? Number(bootstrap.idle_config.idle_threshold_pct_of_fla)
          : null,
      );
    } finally {
      bootstrapHydrationInFlightRef.current = false;
    }
  };

  const fetchData = async (isInitial = false) => {
    if (!isInitial) {
      try {
        const bootstrap = await getDashboardBootstrap(deviceId);
        const machineData = bootstrap.device ?? (await getDeviceById(deviceId));
        if (!shellSummaryRef.current) {
          setMachine(machineData);
        }
        setUptime(bootstrap.uptime);
        setShifts(bootstrap.shifts);
        setHealthConfigs(bootstrap.health_configs);
        setWidgetConfig(bootstrap.widget_config);
        setCurrentState((previous) =>
          mergeCurrentStateWithStability(previous, bootstrap.current_state, {
            runtimeStatus: machineData?.runtime_status,
            source: "bootstrap",
          }) ?? previous ?? null,
        );
        if (!widgetDirty) {
          setSelectedWidgetFields(bootstrap.widget_config?.effective_fields || []);
          setWidgetDirty(false);
        }
        setHealthScore(bootstrap.health_score);
        setFullLoadCurrentInput(
          bootstrap.idle_config?.full_load_current_a != null
            ? String(bootstrap.idle_config.full_load_current_a)
            : ""
        );
        setPersistedFullLoadCurrent(
          bootstrap.idle_config?.full_load_current_a != null
            ? Number(bootstrap.idle_config.full_load_current_a)
            : null,
        );
        setIdleThresholdPctInput(
          bootstrap.idle_config?.idle_threshold_pct_of_fla != null
            ? String(bootstrap.idle_config.idle_threshold_pct_of_fla)
            : ""
        );
        setPersistedIdleThresholdPct(
          bootstrap.idle_config?.idle_threshold_pct_of_fla != null
            ? Number(bootstrap.idle_config.idle_threshold_pct_of_fla)
            : null,
        );
      } catch {
        // Non-initial refresh — keep existing state.
      }
      return;
    }

    setShellSummary(null);
    shellSummaryRef.current = null;
    setShellCurrentState(null);
    const summary = await fetchSummary();
    if (summary) {
      void fetchHydration();
    } else {
      try {
        setLoadingMessage("Loading machine dashboard...");
        const bootstrapResult = await loadMachineDetailBootstrap({
          loadBootstrap: () =>
            getDashboardBootstrap(deviceId, {
              timeoutMs: MACHINE_DETAIL_FALLBACK_BOOTSTRAP_TIMEOUT_MS,
            }),
          fallbackError: "Failed to fetch machine dashboard",
          maxAttempts: 2,
        });
        const bootstrap = bootstrapResult.data;
        if (!bootstrap) {
          throw new Error(bootstrapResult.fatalError || "Failed to fetch machine dashboard");
        }
        const machineData = bootstrap.device ?? (await getDeviceById(deviceId));
        const ascTelemetry = sortTelemetryAsc(bootstrap.telemetry ?? []);
        const descTelemetry = sortTelemetryDesc(bootstrap.telemetry ?? []);
        if (ascTelemetry.length > 0) {
          setTelemetry((previous) => mergeTelemetryAsc(previous, ascTelemetry).slice(-100));
          if (!detailSnapshot?.availability.recent_telemetry_ready) {
            setTelemetryStreamRows(descTelemetry.slice(0, RECENT_TELEMETRY_BUFFER_SIZE));
            setTelemetryTablePage(1);
            latestTelemetryTimestampRef.current = descTelemetry[0]?.timestamp || null;
          }
        }
        if (!shellSummaryRef.current) {
          setMachine(machineData);
        }
        setUptime(bootstrap.uptime);
        setShifts(bootstrap.shifts);
        setHealthConfigs(bootstrap.health_configs);
        setWidgetConfig(bootstrap.widget_config);
        setCurrentState((previous) => {
          if (shellSummaryRef.current) {
            return previous ?? bootstrap.current_state ?? null;
          }
          return mergeCurrentStateWithStability(previous, bootstrap.current_state, {
            runtimeStatus: machineData?.runtime_status,
            source: "bootstrap",
          }) ?? null;
        });
        if (!widgetDirty) {
          setSelectedWidgetFields(bootstrap.widget_config?.effective_fields || []);
          setWidgetDirty(false);
        }
        setHealthScore(bootstrap.health_score);
        setFullLoadCurrentInput(
          bootstrap.idle_config?.full_load_current_a != null
            ? String(bootstrap.idle_config.full_load_current_a)
            : ""
        );
        setPersistedFullLoadCurrent(
          bootstrap.idle_config?.full_load_current_a != null
            ? Number(bootstrap.idle_config.full_load_current_a)
            : null,
        );
        setIdleThresholdPctInput(
          bootstrap.idle_config?.idle_threshold_pct_of_fla != null
            ? String(bootstrap.idle_config.idle_threshold_pct_of_fla)
            : ""
        );
        setPersistedIdleThresholdPct(
          bootstrap.idle_config?.idle_threshold_pct_of_fla != null
            ? Number(bootstrap.idle_config.idle_threshold_pct_of_fla)
            : null,
        );
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to fetch data");
      } finally {
        setLoading(false);
        setLoadingMessage("Loading machine dashboard...");
      }
    }
  };

  const connectTelemetryStream = async () => {
    if (!deviceId || typeof window === "undefined") return;
    const connectAttemptId = telemetryWsConnectAttemptRef.current + 1;
    telemetryWsConnectAttemptRef.current = connectAttemptId;
    if (telemetryWsRef.current) {
      telemetryWsRef.current.close();
      telemetryWsRef.current = null;
    }
    const wsProto = window.location.protocol === "https:" ? "wss" : "ws";
    let ticketPayload: { ticket: string; expires_in_seconds: number };
    try {
      ticketPayload = await getTelemetryWebsocketTicket(deviceId);
    } catch (err) {
      console.error("Failed to acquire telemetry WebSocket ticket:", err);
      return;
    }
    if (telemetryWsConnectAttemptRef.current !== connectAttemptId) return;
    const wsParams = new URLSearchParams();
    wsParams.set("ticket", ticketPayload.ticket);
    const qs = wsParams.toString();
    const wsUrl = `${wsProto}://${window.location.host}${DATA_SERVICE_BASE}/ws/telemetry/${deviceId}${qs ? `?${qs}` : ""}`;
    const ws = new WebSocket(wsUrl);
    telemetryWsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.type !== "telemetry" || !payload?.data) return;
        const latest = {
          ...(payload.data || {}),
          timestamp: payload.timestamp || new Date().toISOString(),
        } as TelemetryPoint;
        if (!latest.timestamp || latestTelemetryTimestampRef.current === latest.timestamp) return;
        latestTelemetryTimestampRef.current = latest.timestamp;
        setTelemetryStreamRows((prev) => sortTelemetryDesc([latest, ...prev]).slice(0, RECENT_TELEMETRY_BUFFER_SIZE));
        setTelemetry((prev) => sortTelemetryAsc([...prev, latest]).slice(-100));
      } catch (err) {
        console.error("Telemetry WS parse error:", err);
      }
    };
    ws.onerror = () => {
      ws.close();
    };
  };

  const loadActivityHistory = async (options?: { force?: boolean }) => {
    if (!options?.force && !showAlertHistory) {
      return;
    }
    const requestId = activityHistoryRequestIdRef.current + 1;
    activityHistoryRequestIdRef.current = requestId;
    const hadCachedEvents = activityEventsRef.current.length > 0;
    activityHistoryAbortRef.current?.abort();
    const controller = new AbortController();
    activityHistoryAbortRef.current = controller;
    setActivityHistoryLoading(true);
    try {
      const [eventsResult, unreadCount] = await Promise.all([
        getActivityEvents({ deviceId, page: 1, pageSize: 25, signal: controller.signal }),
        getActivityUnreadCount(deviceId, { signal: controller.signal }),
      ]);
      if (controller.signal.aborted || activityHistoryRequestIdRef.current !== requestId) {
        return;
      }
      setActivityEvents(eventsResult.data);
      activityEventsRef.current = eventsResult.data;
      setUnreadEventCount(unreadCount);
      setActivityHistoryError(null);
      setActivityHistoryLoaded(true);
    } catch (err) {
      if (controller.signal.aborted || activityHistoryRequestIdRef.current !== requestId || isActivityHistoryAbortError(err)) {
        return;
      }
      if (isTransientActivityHistoryError(err)) {
        setActivityHistoryError(getActivityHistoryDegradedMessage(hadCachedEvents));
        return;
      }
      setActivityHistoryError(err instanceof Error ? err.message : "Activity history is unavailable right now.");
      console.error("Failed to load activity history:", err);
    } finally {
      if (activityHistoryAbortRef.current === controller) {
        activityHistoryAbortRef.current = null;
        setActivityHistoryLoading(false);
      }
    }
  };

  const loadUnreadActivityCount = async () => {
    try {
      const unreadCount = await getActivityUnreadCount(deviceId);
      setUnreadEventCount(unreadCount);
    } catch (err) {
      console.error("Failed to load unread activity count:", err);
    }
  };

  const loadPerformanceTrends = async () => {
    try {
      setTrendLoading(true);
      setTrendError(null);
      const data = await getPerformanceTrends(deviceId, trendMetric, trendRange);
      setTrendData(data);
    } catch (err) {
      setTrendError(err instanceof Error ? err.message : "Failed to load performance trends");
    } finally {
      setTrendLoading(false);
    }
  };

  const loadIdleConfig = async () => {
    try {
      const config = await getIdleConfig(deviceId);
      setFullLoadCurrentInput(
        config.full_load_current_a != null
          ? String(config.full_load_current_a)
          : ""
      );
      setPersistedFullLoadCurrent(
        config.full_load_current_a != null
          ? Number(config.full_load_current_a)
          : null,
      );
      setIdleThresholdPctInput(
        config.idle_threshold_pct_of_fla != null
          ? String(config.idle_threshold_pct_of_fla)
          : ""
      );
      setPersistedIdleThresholdPct(
        config.idle_threshold_pct_of_fla != null
          ? Number(config.idle_threshold_pct_of_fla)
          : null,
      );
    } catch (err) {
      console.error("Failed to load idle config:", err);
    }
  };

  const loadCurrentState = async () => {
    try {
      const state = await getCurrentState(deviceId);
      setCurrentState((previous) =>
        mergeCurrentStateWithStability(previous, state, {
          runtimeStatus: shellSummaryRef.current?.runtime_status ?? machine?.runtime_status,
          source: "current_state_poll",
        }) ?? null,
      );
      setShellCurrentState((previous) =>
        mergeCurrentStateWithStability(previous, state, {
          runtimeStatus: shellSummaryRef.current?.runtime_status ?? machine?.runtime_status,
          source: "current_state_poll",
        }) ?? null,
      );
    } catch (err) {
      console.error("Failed to load current state:", err);
    }
  };

  const loadOlderTelemetryHistory = async () => {
    if (telemetryHistoryLoading) return;
    try {
      setTelemetryHistoryLoading(true);
      setTelemetryHistoryError(null);
      const oldestRecentTimestamp = telemetryStreamRows[telemetryStreamRows.length - 1]?.timestamp ?? null;
      const recentCutoffMs = oldestRecentTimestamp ? Date.parse(oldestRecentTimestamp) : null;
      const historyRows = await getTelemetryHistory(deviceId, oldestRecentTimestamp ? {
        limit: "100",
        end_time: oldestRecentTimestamp,
      } : {
        limit: "100",
      });
      const filteredRows = historyRows.filter((point) => {
        if (!point?.timestamp) return false;
        if (!Number.isFinite(recentCutoffMs ?? Number.NaN)) return true;
        return Date.parse(point.timestamp) < (recentCutoffMs as number);
      });
      setTelemetryHistoryRows(sortTelemetryDesc(filteredRows));
      setTelemetryHistoryLoaded(true);
    } catch (err) {
      if (isTelemetryHistoryUnavailableError(err)) {
        setTelemetryHistoryError(
          telemetryStreamRows.length > 0
            ? "Recent telemetry is available, but older history is temporarily unavailable."
            : "Telemetry history is temporarily unavailable.",
        );
      } else {
        setTelemetryHistoryError(err instanceof Error ? err.message : "Telemetry history is unavailable right now.");
      }
      setTelemetryHistoryLoaded(true);
    } finally {
      setTelemetryHistoryLoading(false);
    }
  };

  const loadMaintenanceLog = async ({ background = false }: { background?: boolean } = {}) => {
    try {
      if (!background) {
        setMaintenanceLoading(true);
      }
      const [summary, records] = await Promise.all([
        getMaintenanceLogSummary(deviceId),
        getMaintenanceLogRecords(deviceId),
      ]);
      setMaintenanceSummary(summary);
      setMaintenanceRecords(records);
      setMaintenanceError(null);
      setMaintenanceLoaded(true);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load Maintenance Log";
      setMaintenanceError(message);
      return false;
    } finally {
      if (!background) {
        setMaintenanceLoading(false);
      }
    }
  };

  const reconcileAfterCrud = async (options?: { refreshShifts?: boolean; refreshHealthConfigs?: boolean }) => {
    try {
      const tasks: Promise<unknown>[] = [getUptime(deviceId)];
      if (options?.refreshShifts) tasks.push(getShifts(deviceId));
      if (options?.refreshHealthConfigs) tasks.push(getHealthConfigs(deviceId));

      const results = await Promise.all(tasks);
      setUptime(results[0] as UptimeData);
      let nextIndex = 1;
      if (options?.refreshShifts) {
        setShifts(results[nextIndex] as Shift[]);
        nextIndex += 1;
      }
      if (options?.refreshHealthConfigs) {
        setHealthConfigs(results[nextIndex] as HealthConfig[]);
      }
    } catch {
      // Keep optimistic state and let periodic polling reconcile eventually.
    }

    const latest = telemetryStreamRows[0];
    if (latest) {
      const values: Record<string, number> = {};
      for (const [key, value] of Object.entries(latest)) {
        if (typeof value === "number" && Number.isFinite(value)) {
          values[key] = value;
        }
      }
      if (Object.keys(values).length > 0) {
        try {
          const score = await calculateHealthScore(deviceId, { values, machine_state: "RUNNING" } as TelemetryValues);
          setHealthScore(score);
        } catch {
          // Non-blocking reconciliation.
        }
      }
    }
    if (trendSectionPrimed) {
      void loadPerformanceTrends();
    }
  };

  useEffect(() => {
    if (!deviceId) return;
    fetchData(true);
    void connectTelemetryStream();
    return () => {
      telemetryWsConnectAttemptRef.current += 1;
      activityHistoryAbortRef.current?.abort();
      activityHistoryAbortRef.current = null;
      if (telemetryWsRef.current) {
        telemetryWsRef.current.close();
        telemetryWsRef.current = null;
      }
    };
  }, [deviceId]);

  useEffect(() => {
    if (!deviceId || activeTab !== "overview" || overviewChartRange === "live") {
      setOverviewHistoryTelemetry([]);
      setOverviewHistoryError(null);
      setOverviewHistoryLoading(false);
      return;
    }

    const requestId = overviewHistoryRequestIdRef.current + 1;
    overviewHistoryRequestIdRef.current = requestId;
    const params = getOverviewHistoryParams(overviewChartRange);
    if (!params) {
      return;
    }

    setOverviewHistoryLoading(true);
    setOverviewHistoryError(null);
    getTelemetryHistory(deviceId, params)
      .then((points) => {
        if (overviewHistoryRequestIdRef.current !== requestId) {
          return;
        }
        setOverviewHistoryTelemetry(sortTelemetryAsc(points));
      })
      .catch((err) => {
        if (overviewHistoryRequestIdRef.current !== requestId) {
          return;
        }
        setOverviewHistoryTelemetry([]);
        if (isTelemetryHistoryUnavailableError(err)) {
          setOverviewHistoryError("Historical telemetry is temporarily unavailable. Live charts are still available.");
          return;
        }
        setOverviewHistoryError(err instanceof Error ? err.message : "Historical telemetry could not be loaded.");
      })
      .finally(() => {
        if (overviewHistoryRequestIdRef.current === requestId) {
          setOverviewHistoryLoading(false);
        }
      });
  }, [activeTab, deviceId, overviewChartRange]);

  useAdaptivePolling(
    () => {
      if (!deviceId) return;
      void refreshShellSummary();
      if (activeTab === "parameters") {
        void loadCurrentState();
      }
      if (!machineHealthEnabled) return;
      let settled = 0;
      let failed = 0;
      const tally = () => {
        settled += 1;
        if (settled < 2) return;
        if (failed < 2) {
          healthPollFailRef.current = 0;
          setHealthStaleRefresh(false);
        } else {
          healthPollFailRef.current += 1;
          if (healthPollFailRef.current >= 2) {
            setHealthStaleRefresh(true);
          }
        }
      };
      getDegradationScore(deviceId)
        .then((fresh) => {
          setDegradationScore((prev) => {
            if (!prev || !fresh.score_trend) return fresh;
            const prevContribs = prev.score_trend.some((p) => p.contributions && p.contributions.length > 0);
            if (!prevContribs) return fresh;
            const merged = fresh.score_trend.map((fp) => {
              if (fp.contributions && fp.contributions.length > 0) return fp;
              const matchIdx = prev.score_trend.findIndex(
                (pp) => pp.computed_at === fp.computed_at
              );
              if (matchIdx >= 0 && prev.score_trend[matchIdx].contributions && prev.score_trend[matchIdx].contributions!.length > 0) {
                return { ...fp, contributions: prev.score_trend[matchIdx].contributions };
              }
              return fp;
            });
            return { ...fresh, score_trend: merged };
          });
          tally();
        })
        .catch(() => { failed += 1; tally(); });
      getAnomalyActivity(deviceId)
        .then((data) => { setAnomalyActivity(data); tally(); })
        .catch(() => { failed += 1; tally(); });
    },
    30000,
    90000
  );

  useAdaptivePolling(
    () => {
      if (!deviceId) return;
      if (showAlertHistory) {
        void loadActivityHistory();
        return;
      }
      void loadUnreadActivityCount();
    },
    6000,
    20000
  );

  useEffect(() => {
    setActivityEvents([]);
    activityEventsRef.current = [];
    setUnreadEventCount(0);
    setActivityHistoryError(null);
    setActivityHistoryLoading(false);
    setActivityHistoryLoaded(false);
    setTrendData(null);
    setTrendError(null);
    setTrendLoading(false);
    setTrendSectionPrimed(false);
    setTelemetry([]);
    setTelemetryStreamRows([]);
    setTelemetryTablePage(1);
    setTelemetryHistoryRows([]);
    setTelemetryHistoryLoading(false);
    setTelemetryHistoryLoaded(false);
    setTelemetryHistoryError(null);
    setOverviewChartRange("live");
    setOverviewHistoryTelemetry([]);
    setOverviewHistoryLoading(false);
    setOverviewHistoryError(null);
    overviewHistoryRequestIdRef.current += 1;
    if (!deviceId) return;
    void loadUnreadActivityCount();
  }, [deviceId]);

  useEffect(() => {
    setMaintenanceRecords([]);
    setMaintenanceSummary(null);
    setMaintenanceLoading(false);
    setMaintenanceLoaded(false);
    setMaintenanceError(null);
    setMaintenanceActionMessage(null);
    setShowMaintenanceModal(false);
    setMaintenanceEditingRecord(null);
    setMaintenanceSubmitError(null);
    setMaintenanceDeleteTarget(null);
    setMaintenanceDeleteError(null);
    setDegradationScore(null);
    setDegradationLoading(false);
    setDegradationError(null);
    setAnomalyActivity(null);
    setAnomalyLoading(false);
    setAnomalyError(null);
    setHealthStaleRefresh(false);
    healthPollFailRef.current = 0;
  }, [deviceId]);

  useEffect(() => {
    if (!deviceId || activeTab !== "maintenance" || maintenanceLoaded || maintenanceLoading) return;
    void loadMaintenanceLog();
  }, [activeTab, deviceId, maintenanceLoaded, maintenanceLoading]);

  useEffect(() => {
    if (!machineHealthEnabled) {
      setDegradationScore(null);
      setDegradationError(null);
      setDegradationLoading(false);
      return;
    }
    if (!deviceId || !shellSummary || degradationLoading) return;
    let cancelled = false;
    setDegradationLoading(true);
    setDegradationError(null);
    getDegradationScore(deviceId, { include_trend_contributions: true })
      .then((data) => {
        if (!cancelled) setDegradationScore(data);
      })
      .catch((err) => {
        if (!cancelled) setDegradationError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setDegradationLoading(false);
      });
    return () => { cancelled = true; };
  }, [deviceId, shellSummary, machineHealthEnabled]);

  useEffect(() => {
    if (!machineHealthEnabled) {
      setAnomalyActivity(null);
      setAnomalyError(null);
      setAnomalyLoading(false);
      return;
    }
    if (!deviceId || !shellSummary || anomalyLoading) return;
    let cancelled = false;
    setAnomalyLoading(true);
    setAnomalyError(null);
    getAnomalyActivity(deviceId)
      .then((data) => {
        if (!cancelled) setAnomalyActivity(data);
      })
      .catch((err) => {
        if (!cancelled) setAnomalyError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setAnomalyLoading(false);
      });
    return () => { cancelled = true; };
  }, [deviceId, shellSummary, machineHealthEnabled]);

  useEffect(() => {
    if (!deviceId || activeTab !== "parameters") return;
    void loadIdleConfig();
    void loadCurrentState();
  }, [activeTab, deviceId]);

  useEffect(() => {
    if (!showAlertHistory || !deviceId) return;
    void loadActivityHistory();
  }, [deviceId, showAlertHistory]);

  useEffect(() => {
    if (activeTab !== "overview") return;
    const node = trendSectionRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setTrendSectionPrimed(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setTrendSectionPrimed(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px 0px" },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, [activeTab, deviceId]);

  useEffect(() => {
    if (!deviceId || activeTab !== "overview" || !trendSectionPrimed) return;
    void loadPerformanceTrends();
  }, [activeTab, deviceId, trendMetric, trendRange, trendSectionPrimed]);

  useAdaptivePolling(
    () => {
      if (!deviceId || activeTab !== "maintenance" || !maintenanceLoaded) return;
      void loadMaintenanceLog({ background: true });
    },
    60000,
    180000,
  );

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(telemetryStreamRows.length / RECENT_TELEMETRY_PAGE_SIZE));
    setTelemetryTablePage((current) => Math.min(current, totalPages));
  }, [telemetryStreamRows.length]);

  const handleAddShift = async () => {
    if (newShift.shift_start === newShift.shift_end) {
      alert("Shift start and end times cannot be the same.");
      return;
    }
    if (shiftOverlapConflicts.length > 0) {
      alert("Shift overlaps with existing shifts. Please pick a non-overlapping time.");
      return;
    }
    try {
      if (editingShiftId !== null) {
        const updated = await updateShift(deviceId, editingShiftId, newShift);
        setShifts((prev) => prev.map((shift) => (shift.id === updated.id ? updated : shift)).sort((a, b) => a.shift_start.localeCompare(b.shift_start)));
      } else {
        const created = await createShift(deviceId, newShift);
        setShifts((prev) => [...prev, created].sort((a, b) => a.shift_start.localeCompare(b.shift_start)));
      }
      setShowAddShift(false);
      setEditingShiftId(null);
      setNewShift({ shift_name: "", shift_start: "09:00", shift_end: "17:00", maintenance_break_minutes: 0, day_of_week: null, is_active: true });
      void reconcileAfterCrud({ refreshShifts: true });
    } catch (err) { alert("Failed: " + (err as Error).message); }
  };

  const handleDeleteShift = async (shiftId: number) => {
    if (!confirm("Delete this shift?")) return;
    const previous = shifts;
    setShifts((prev) => prev.filter((shift) => shift.id !== shiftId));
    try {
      await deleteShift(deviceId, shiftId);
      if (editingShiftId === shiftId) {
        setEditingShiftId(null);
        setShowAddShift(false);
      }
      void reconcileAfterCrud({ refreshShifts: true });
    } catch (err) {
      setShifts(previous);
      alert("Failed: " + (err as Error).message);
    }
  };

  const reconcileAfterHealthConfigChange = () => {
    void fetchData(false).catch((err) => {
      console.error("Failed to refresh machine detail summary after health configuration change:", err);
    });
    void fetchHydration().catch((err) => {
      console.error("Failed to refresh detailed machine hydration after health configuration change:", err);
    });
    void refreshShellSummary().catch((err) => {
      console.error("Failed to refresh machine shell summary after health configuration change:", err);
    });
    if (activeTab === "parameters") {
      void loadCurrentState().catch((err) => {
        console.error("Failed to refresh machine current state after health configuration change:", err);
      });
    }
    if (trendSectionPrimed) {
      void loadPerformanceTrends().catch((err) => {
        console.error("Failed to refresh performance trends after health configuration change:", err);
      });
    }
  };

  const handleSaveHealthConfig = async (config: HealthConfigCreate) => {
    try {
      const existing = findHealthConfigForMetric(config.parameter_name, healthConfigs);
      if (existing) {
        const updated = await updateHealthConfig(deviceId, existing.id, config);
        setHealthConfigs((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      } else {
        const created = await createHealthConfig(deviceId, config);
        setHealthConfigs((prev) => [...prev.filter((item) => item.id !== created.id), created]);
      }
      setShowHealthConfig(false);
      setSelectedMetric("");
      reconcileAfterHealthConfigChange();
    } catch (err) { alert("Failed: " + (err as Error).message); }
  };

  const handleDeleteHealthConfig = async (configId: number) => {
    const previous = healthConfigs;
    const previousHealthScore = healthScore;
    setHealthConfigs((prev) => prev.filter((cfg) => cfg.id !== configId));
    setHealthScore(null);
    try {
      await deleteHealthConfig(deviceId, configId);
      setShowHealthConfig(false);
      setSelectedMetric("");
      reconcileAfterHealthConfigChange();
    } catch (err) {
      setHealthConfigs(previous);
      setHealthScore(previousHealthScore);
      alert("Failed: " + (err as Error).message);
    }
  };

  const handleSaveEngineeringConfig = async () => {
    const fullLoadCurrent = parsedFullLoadCurrentDraft;
    const idleThresholdPct = resolvedIdleThresholdPctDraft;
    if (fullLoadCurrent == null || engineeringSaveBlockReason) {
      return;
    }
    try {
      setEngineeringSaving(true);
      setEngineeringSaveMessage("");
      await saveIdleConfig(deviceId, {
        full_load_current_a: fullLoadCurrent,
        idle_threshold_pct_of_fla: idleThresholdPct,
      });
      await Promise.all([loadIdleConfig(), loadCurrentState(), refreshShellSummary()]);
      setEngineeringSaveMessage("FLA-based load classification saved.");
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    } finally {
      setEngineeringSaving(false);
    }
  };

  const handleToggleWidgetField = (field: string) => {
    setWidgetSaveMessage("");
    setWidgetDirty(true);
    setSelectedWidgetFields((prev) => {
      if (prev.includes(field)) {
        return prev.filter((f) => f !== field);
      }
      return [...prev, field];
    });
  };

  const handleSaveWidgetConfig = async () => {
    try {
      setWidgetSaving(true);
      setWidgetSaveMessage("");
      const saved = await saveDashboardWidgetConfig(deviceId, selectedWidgetFields);
      setWidgetConfig(saved);
      setSelectedWidgetFields(saved.effective_fields || []);
      setWidgetSaveMessage("Widget configuration saved.");
      setWidgetDirty(false);
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    } finally {
      setWidgetSaving(false);
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await markAllActivityRead(deviceId);
      await loadActivityHistory({ force: true });
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    }
  };

  const handleClearHistory = async () => {
    if (!confirm("Clear all alert history for this machine?")) return;
    try {
      await clearActivityHistory(deviceId);
      await loadActivityHistory({ force: true });
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    }
  };

  const handleAlertMutation = async (event: ActivityEvent, action: "acknowledge" | "resolve") => {
    if (!event.alertId) return;
    setAlertActionBusyId(event.eventId);
    setAlertActionMessage(null);
    try {
      if (action === "acknowledge") {
        await acknowledgeAlert(event.alertId, me?.user?.full_name || me?.user?.email || "machine-ui");
        setAlertActionMessage("Alert acknowledged.");
      } else {
        await resolveAlert(event.alertId);
        setAlertActionMessage("Alert resolved.");
      }
      await loadActivityHistory({ force: true });
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    } finally {
      setAlertActionBusyId(null);
    }
  };

  const canManageMaintenance = canEditDevice && !isReadOnly;
  const canDeleteMaintenance = canDeleteDevice && canManageMaintenance;
  const maintenanceHasVisibleData = Boolean(maintenanceSummary) || maintenanceRecords.length > 0;

  const openAddMaintenanceModal = () => {
    if (!canManageMaintenance) return;
    setMaintenanceEditingRecord(null);
    setMaintenanceSubmitError(null);
    setShowMaintenanceModal(true);
  };

  const openEditMaintenanceModal = (record: MaintenanceLogRecord) => {
    if (!canManageMaintenance) return;
    setMaintenanceEditingRecord(record);
    setMaintenanceSubmitError(null);
    setShowMaintenanceModal(true);
  };

  const closeMaintenanceModal = () => {
    if (maintenanceSubmitting) return;
    setShowMaintenanceModal(false);
    setMaintenanceEditingRecord(null);
    setMaintenanceSubmitError(null);
  };

  const closeMaintenanceDeleteDialog = () => {
    if (maintenanceDeleting) return;
    setMaintenanceDeleteTarget(null);
    setMaintenanceDeleteError(null);
  };

  const handleSubmitMaintenanceRecord = async (payload: MaintenanceLogMutationInput) => {
    if (!canManageMaintenance) return;
    try {
      setMaintenanceSubmitting(true);
      setMaintenanceSubmitError(null);
      setMaintenanceActionMessage(null);
      if (maintenanceEditingRecord) {
        await updateMaintenanceLogRecord(deviceId, maintenanceEditingRecord.id, payload);
      } else {
        await createMaintenanceLogRecord(deviceId, payload);
      }
      setShowMaintenanceModal(false);
      setMaintenanceEditingRecord(null);
      const refreshed = await loadMaintenanceLog({ background: true });
      if (refreshed) {
        setMaintenanceActionMessage(
          maintenanceEditingRecord ? "Maintenance record updated." : "Maintenance record added."
        );
      } else {
        setMaintenanceActionMessage("Maintenance record saved. Refresh the page to load the latest history.");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to save this maintenance record right now.";
      setMaintenanceSubmitError(normalizeMaintenanceApiError(message));
    } finally {
      setMaintenanceSubmitting(false);
    }
  };

  const handleDeleteMaintenanceRecord = async () => {
    if (!canDeleteMaintenance || !canManageMaintenance) return;
    if (!maintenanceDeleteTarget) return;
    try {
      setMaintenanceDeleting(true);
      setMaintenanceDeleteError(null);
      setMaintenanceActionMessage(null);
      await deleteMaintenanceLogRecord(deviceId, maintenanceDeleteTarget.id);
      setMaintenanceDeleteTarget(null);
      const refreshed = await loadMaintenanceLog({ background: true });
      if (refreshed) {
        setMaintenanceActionMessage("Maintenance record deleted.");
      } else {
        setMaintenanceActionMessage("Maintenance record deleted. Refresh the page to load the latest history.");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to delete this maintenance record right now.";
      setMaintenanceDeleteError(normalizeMaintenanceApiError(message));
    } finally {
      setMaintenanceDeleting(false);
    }
  };

  if (loading) return <div className="p-8"><div className="flex h-64 flex-col items-center justify-center gap-4"><div className="h-12 w-12 animate-spin rounded-full border-b-2 border-blue-600"></div><p className="text-sm text-slate-600">{loadingMessage}</p></div></div>;
  if (error && !machine) return <div className="p-8"><div className="bg-red-50 border p-6 rounded"><h2 className="text-red-800 font-semibold">Error</h2><p className="text-red-600">{error}</p><Link href="/machines"><Button className="mt-4">Back</Button></Link></div></div>;
  if (!machine) return <div className="p-8"><div className="flex h-64 flex-col items-center justify-center gap-4"><div className="h-12 w-12 animate-spin rounded-full border-b-2 border-blue-600"></div><p className="text-sm text-slate-600">Loading machine...</p></div></div>;

  const latestTelemetry = telemetry.at(-1);
  const snapshotNumericFields = detailSnapshot?.snapshot?.numeric_fields ?? null;
  const latestOverviewMetrics = snapshotNumericFields ?? latestTelemetry ?? null;
  const snapshotMetrics = getNumericMetricKeys(snapshotNumericFields);
  const overviewChartTelemetry = overviewChartRange === "live" ? telemetry : overviewHistoryTelemetry;
  const dynamicMetrics = Array.from(new Set([
    ...snapshotMetrics,
    ...getDynamicMetrics(telemetry),
    ...getDynamicMetrics(overviewHistoryTelemetry),
  ]));
  const telemetryBufferedRowCount = telemetryStreamRows.length;
  const telemetryTableTotalPages = Math.max(1, Math.ceil(telemetryBufferedRowCount / RECENT_TELEMETRY_PAGE_SIZE));
  const telemetryTableCurrentPage = Math.min(telemetryTablePage, telemetryTableTotalPages);
  const telemetryTableStartIndex = (telemetryTableCurrentPage - 1) * RECENT_TELEMETRY_PAGE_SIZE;
  const telemetryTableVisibleRows = telemetryStreamRows.slice(
    telemetryTableStartIndex,
    telemetryTableStartIndex + RECENT_TELEMETRY_PAGE_SIZE,
  );
  const effectiveWidgetFields = widgetConfig?.effective_fields || dynamicMetrics;
  const selectedWidgetFieldSet = new Set(selectedWidgetFields);
  const visibleOverviewMetrics = effectiveWidgetFields.filter(
    (field) => typeof latestOverviewMetrics?.[field] === "number",
  );
  const overviewChartRangeLabel =
    OVERVIEW_CHART_RANGE_OPTIONS.find((option) => option.value === overviewChartRange)?.description || "Telemetry trend";
  const kpiState = deriveMachineKpiState({
    hydrationLoading,
    hydrationFailed: Boolean(hydrationError),
    hydrationError,
    hasTelemetry: snapshotMetrics.length > 0 || telemetry.length > 0,
    dynamicMetricCount: visibleOverviewMetrics.length,
  });
  const shellState = deriveMachineDetailShellState({
    summary: shellSummary,
    shellCurrentState,
    fallbackMachine: machine,
    fallbackHealthPercent: typeof healthScore?.health_score === "number" ? healthScore.health_score : null,
    fallbackUptimePercent: typeof uptime?.uptime_percentage === "number" ? uptime.uptime_percentage : null,
  });
  const shellMachine = shellState.machine;
  const healthPercent = shellState.healthPercent;
  const uptimePercent = shellState.uptimePercent;
  const lossOverview = shellState.lossOverview;
  const overviewReadiness = shellState.overviewReadiness;
  const trendDisplay = buildPerformanceTrendDisplayModel(trendData, trendMetric);
  const effectiveLoadState = shellState.effectiveLoadState as DeviceLoadState;
  const operationalStatus = shellState.operationalStatus;
  const operationalStatusMeta = getOperationalStatusMeta(operationalStatus);
  const effectiveLoadStateLabel = getDetailedLoadStateLabel(effectiveLoadState);
  const currentBandLabel =
    currentState?.current_band === "in_load"
      ? "In Load"
      : currentState?.current_band === "overconsumption"
        ? "Overconsumption"
        : currentState?.current_band === "idle"
          ? "Idle"
          : currentState?.current_band === "unloaded"
            ? "Unloaded"
            : shellCurrentState?.current_band === "in_load"
              ? "In Load"
              : shellCurrentState?.current_band === "overconsumption"
                ? "Overconsumption"
                : shellCurrentState?.current_band === "idle"
                  ? "Idle"
                  : shellCurrentState?.current_band === "unloaded"
                    ? "Unloaded"
                    : shellSummary?.current_band === "in_load"
              ? "In Load"
              : shellSummary?.current_band === "overconsumption"
                ? "Overconsumption"
                : shellSummary?.current_band === "idle"
                  ? "Idle"
                  : shellSummary?.current_band === "unloaded"
                    ? "Unloaded"
                    : "Unknown";
  const noActiveShiftWindow = uptime?.uptime_percentage == null;
  const outsideShiftFinancialBucketMessage = noActiveShiftWindow
    ? getOutsideShiftFinancialBucketMessage(effectiveLoadStateLabel)
    : null;
  const parsedFullLoadCurrentDraft = parseEngineeringNumberDraft(fullLoadCurrentInput);
  const parsedIdleThresholdPctDraft = parseEngineeringNumberDraft(idleThresholdPctInput);
  const resolvedIdleThresholdPctDraft =
    idleThresholdPctInput.trim().length > 0
      ? parsedIdleThresholdPctDraft
      : (persistedIdleThresholdPct ?? 0.25);
  const engineeringSaveBlockReason = getEngineeringSaveBlockReason(
    parsedFullLoadCurrentDraft,
    resolvedIdleThresholdPctDraft,
  );
  const fullLoadCurrentDraftDiffersFromSaved = hasUnsavedEngineeringDraft(
    fullLoadCurrentInput,
    persistedFullLoadCurrent,
  );
  const idleThresholdPctDraftDiffersFromSaved = hasUnsavedEngineeringDraft(
    idleThresholdPctInput,
    persistedIdleThresholdPct,
  );
  const thresholdPreview = deriveThresholdsFromFla(
    parsedFullLoadCurrentDraft,
    resolvedIdleThresholdPctDraft,
  );
  const shiftTimeEqual = newShift.shift_start === newShift.shift_end;
  const shiftOverlapConflicts = findOverlapConflicts(newShift, shifts, editingShiftId);
  const shiftFormError = shiftTimeEqual
    ? "Start and end cannot be the same."
    : shiftOverlapConflicts.length > 0
      ? `Overlaps with: ${shiftOverlapConflicts
          .map((s) => `${s.shift_name} (${formatShiftRange(s.shift_start, s.shift_end)})`)
          .join(", ")}`
      : "";
  const shiftFormBlocked = !newShift.shift_name || shiftTimeEqual || shiftOverlapConflicts.length > 0;
  const maintenanceEmpty = maintenanceLoaded && maintenanceRecords.length === 0;

  return (
    <div className="section-spacing">
      <ReadOnlyBanner />
      <div className="w-full">
          <div className="mb-6 sm:mb-8">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-slate-500 sm:mb-4">
            <Link href="/machines" className="hover:text-slate-900">Machines</Link><span>/</span><span className="text-slate-900">{shellMachine.name}</span>
          </div>
          <div className="relative rounded-3xl border border-slate-200 bg-gradient-to-b from-white to-slate-50/70 p-4 sm:p-6 md:p-8 shadow-sm">
            <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-6">
              <div className="min-w-0 flex-1">
                <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl md:text-4xl">{shellMachine.name}</h1>
                <p
                  className="mt-1 break-all font-mono text-sm text-slate-500 sm:text-base lg:text-lg"
                  title={shellMachine.id}
                >
                  {shellMachine.id}
                </p>
                <ActivationTimestampField
                  label="Activated"
                  timestamp={shellMachine.first_telemetry_timestamp}
                  emptyText="Not activated yet"
                  className="mt-2 flex items-center gap-2 text-sm text-slate-500"
                  labelClassName="font-medium text-slate-700"
                  valueClassName="text-slate-500"
                />
                {shellMachine.last_seen_timestamp ? (
                  <p className="text-sm text-slate-500 mt-2">
                    Last seen: {formatIST(shellMachine.last_seen_timestamp)}
                  </p>
                ) : (
                  <p className="text-sm text-slate-500 mt-2">Last seen: No data received</p>
                )}
              </div>

              <div className="flex flex-wrap items-center justify-start gap-3 self-start lg:justify-end">
                <button
                  type="button"
                  onClick={() => setShowAlertHistory((prev) => !prev)}
                  className="relative inline-flex items-center justify-center w-11 h-11 rounded-xl border border-slate-200 bg-white hover:bg-slate-50"
                  title="Machine alert history"
                >
                  <svg className="w-5 h-5 text-slate-700" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2a2 2 0 0 1-.6 1.4L4 17h5" />
                    <path d="M10 17a2 2 0 0 0 4 0" />
                  </svg>
                  {unreadEventCount > 0 && (
                    <span className="absolute -top-1 -right-1 min-w-5 h-5 px-1 rounded-full bg-red-600 text-white text-[10px] leading-5 text-center">
                      {unreadEventCount > 99 ? "99+" : unreadEventCount}
                    </span>
                  )}
                </button>
                <StatusBadge status={shellMachine.runtime_status} />
                <OperationalStatusBadge status={operationalStatus} />
              </div>
            </div>

            <div className="mt-7 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Name</p>
                <p className="text-xl font-semibold text-slate-900 mt-2">{shellMachine.name}</p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Status</p>
                <p
                  className={`mt-2 text-xl font-bold leading-tight [overflow-wrap:anywhere] sm:text-2xl ${
                    operationalStatusMeta.className.split(" ").find((token) => token.startsWith("text-")) || "text-slate-900"
                  }`}
                  title={operationalStatusMeta.label}
                >
                  {operationalStatusMeta.label}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">ID</p>
                <p
                  className="mt-2 break-all font-mono text-sm font-semibold leading-snug text-slate-800 sm:text-base"
                  title={shellMachine.id}
                >
                  {shellMachine.id}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Type</p>
                <p className="text-xl font-semibold text-slate-900 mt-2 capitalize">{shellMachine.type || "—"}</p>
              </div>
              <div className="relative group rounded-xl border border-slate-200 bg-white p-4 cursor-help">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Uptime</p>
                <p className="mt-2 text-2xl font-bold text-slate-900 sm:text-3xl">{uptimePercent !== null ? `${uptimePercent.toFixed(1)}%` : "—"}</p>
                <p className="mt-1 text-[11px] text-slate-500">
                  {uptimePercent !== null
                    ? <><span className="md:hidden">Details below</span><span className="hidden md:inline">Hover for calc details</span></>
                    : overviewReadiness.uptime_ready
                      ? <><span className="md:hidden">Details below</span><span className="hidden md:inline">Hover for calc details</span></>
                      : hydrationLoading && !uptime
                        ? "Loading shift details..."
                        : hydrationError && !uptime
                          ? "Shift-backed uptime details are temporarily unavailable."
                          : (uptime?.message || "No active shift window")}
                </p>
                <div className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-600 md:hidden">
                  {hydrationLoading && !uptime ? (
                    <p>Loading shift details...</p>
                  ) : uptime ? (
                    <div className="space-y-1.5">
                      <p>Active shifts: <span className="font-medium text-slate-800">{uptime.shifts_configured}</span></p>
                      {uptime.uptime_percentage === null ? (
                        <p className="text-amber-700">{uptime.message || "No active shift window right now."}</p>
                      ) : (
                        <>
                          <p>Planned duration: <span className="font-medium text-slate-800">{formatMinutes(uptime.total_planned_minutes)}</span></p>
                          <p>Effective duration: <span className="font-medium text-slate-800">{formatMinutes(uptime.total_effective_minutes)}</span></p>
                          <p>Actual running: <span className="font-medium text-slate-800">{formatMinutes(uptime.actual_running_minutes)}</span></p>
                        </>
                      )}
                    </div>
                  ) : (
                    <p>No shift configuration found.</p>
                  )}
                </div>
                <div className="pointer-events-none absolute left-1/2 top-full z-30 mt-2 hidden w-72 -translate-x-1/2 rounded-xl border border-slate-200 bg-white p-3 shadow-xl opacity-0 invisible transition-all group-hover:visible group-hover:opacity-100 md:block">
                  <p className="text-xs font-semibold text-slate-700 mb-2">Uptime Calculation</p>
                  {hydrationLoading && !uptime ? (
                    <p className="text-xs text-slate-500">Loading shift details...</p>
                  ) : uptime ? (
                    <>
                      <p className="text-xs text-slate-600">Active shifts: <span className="font-medium">{uptime.shifts_configured}</span></p>
                      {uptime.uptime_percentage === null ? (
                        <p className="text-xs text-amber-700 mt-1">{uptime.message || "No active shift window right now."}</p>
                      ) : (
                        <>
                          <p className="text-xs text-slate-600">Planned duration: <span className="font-medium">{formatMinutes(uptime.total_planned_minutes)}</span></p>
                          <p className="text-xs text-slate-600">Effective duration: <span className="font-medium">{formatMinutes(uptime.total_effective_minutes)}</span></p>
                          <p className="text-xs text-slate-600">Actual running: <span className="font-medium">{formatMinutes(uptime.actual_running_minutes)}</span></p>
                          {uptime.window_start && uptime.window_end && (
                            <p className="text-xs text-slate-600">
                              Shift window: <span className="font-medium">{formatIST(uptime.window_start, "—")} → {formatIST(uptime.window_end, "—")}</span>
                            </p>
                          )}
                          <p className="text-xs text-slate-500 mt-2">Formula: uptime = running minutes / effective shift minutes.</p>
                        </>
                      )}
                    </>
                  ) : (
                    <p className="text-xs text-slate-500">No shift configuration found.</p>
                  )}
                </div>
              </div>
              <div className="relative group rounded-xl border border-slate-200 bg-white p-4 cursor-help">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Health Score</p>
                <p className={`mt-1 text-4xl font-extrabold sm:text-5xl ${healthPercent !== null && healthPercent >= 70 ? "text-emerald-400" : healthPercent !== null ? "text-orange-500" : "text-slate-400"}`}>
                  {healthPercent !== null ? `${healthPercent.toFixed(0)}%` : "—"}
                </p>
                <p className="text-[11px] text-slate-500 mt-1">
                  {healthPercent !== null || overviewReadiness.health_ready
                    ? <><span className="md:hidden">Details below</span><span className="hidden md:inline">Hover for calc details</span></>
                    : hydrationLoading && !healthScore
                      ? "Loading breakdown..."
                      : hydrationError && !healthScore
                        ? "Detailed health breakdown is temporarily unavailable."
                        : <><span className="md:hidden">Details below</span><span className="hidden md:inline">Hover for calc details</span></>}
                </p>
                <div className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-600 md:hidden">
                  {hydrationLoading && !healthScore ? (
                    <p>Loading breakdown...</p>
                  ) : healthScore ? (
                    <div className="space-y-1.5">
                      <p>Status: <span className="font-medium text-slate-800">{healthScore.status}</span></p>
                      <p>Machine state: <span className="font-medium text-slate-800">{healthScore.machine_state}</span></p>
                      <p>Parameters used: <span className="font-medium text-slate-800">{healthScore.parameters_included}</span>, skipped: <span className="font-medium text-slate-800">{healthScore.parameters_skipped}</span></p>
                      <p>Configured weight total: <span className="font-medium text-slate-800">{healthScore.total_weight_configured}%</span></p>
                    </div>
                  ) : (
                    <p>No health data available.</p>
                  )}
                </div>
                <div className="pointer-events-none absolute right-0 top-full z-30 mt-2 hidden w-80 rounded-xl border border-slate-200 bg-white p-3 shadow-xl opacity-0 invisible transition-all group-hover:visible group-hover:opacity-100 md:block">
                  <p className="text-xs font-semibold text-slate-700 mb-2">Health Score Breakdown</p>
                  {hydrationLoading && !healthScore ? (
                    <p className="text-xs text-slate-500">Loading breakdown...</p>
                  ) : healthScore ? (
                    <>
                      <p className="text-xs text-slate-600">Status: <span className="font-medium">{healthScore.status}</span></p>
                      <p className="text-xs text-slate-600">Machine state: <span className="font-medium">{healthScore.machine_state}</span></p>
                      <p className="text-xs text-slate-600">Parameters used: <span className="font-medium">{healthScore.parameters_included}</span>, skipped: <span className="font-medium">{healthScore.parameters_skipped}</span></p>
                      <p className="text-xs text-slate-600">Configured weight total: <span className="font-medium">{healthScore.total_weight_configured}%</span></p>
                      <p className="text-xs text-slate-500 mt-2">Health = sum of each parameter score multiplied by its configured weight.</p>
                      <div className="mt-2 border-t border-slate-100 pt-2 space-y-1">
                        {healthScore.parameter_scores.slice(0, 4).map((p) => (
                          <p key={p.parameter_name} className="text-xs text-slate-600">
                            {p.parameter_name}: {p.raw_score !== null ? `${p.raw_score.toFixed(1)}%` : p.status} ({p.weight}% wt)
                          </p>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="text-xs text-slate-500">No health data available.</p>
                  )}
                </div>
              </div>
            </div>

            <div className="mt-6">
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold mb-3">Machine Health</p>
              {hasFeature(me, "machine_health") ? (
              <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
                <div className="lg:col-span-3">
                  <DegradationScoreCard data={degradationScore} loading={degradationLoading} error={degradationError} staleRefresh={healthStaleRefresh} />
                </div>
                <div className="lg:col-span-2">
                  <AnomalyActivityCard data={anomalyActivity} loading={anomalyLoading} error={anomalyError} staleRefresh={healthStaleRefresh} />
                </div>
              </div>
              ) : (
              <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
                <div className="lg:col-span-3">
                  <LockedPremiumCard feature="machine_health" description="Risk assessment scores, signal breakdown, and degradation trends." />
                </div>
                <div className="lg:col-span-2">
                  <LockedPremiumCard feature="machine_health" description="Anomaly activity counts, severity tracking, and event timeline." />
                </div>
              </div>
              )}
            </div>

            <div className="mt-4 text-sm text-slate-600">
              <span className="font-medium text-slate-700">Location:</span> {shellMachine.location || "—"}
            </div>
            {outsideShiftFinancialBucketMessage && (
              <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                {outsideShiftFinancialBucketMessage}
              </div>
            )}

            <div className="mt-5 rounded-2xl border border-slate-200 bg-white p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Waste & Loss Today</p>
              {lossOverview ? (
              <>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-xl border border-slate-200 p-3">
                  <p className="text-xs text-slate-500 uppercase tracking-wide">Idle Loss</p>
                  <p className="text-lg font-semibold text-slate-900 mt-1">{formatEnergyKwh(lossOverview.idle_kwh)}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    {formatLossOverviewCost(lossOverview.idle_cost_inr, lossOverview.currency, lossOverview.costs_available)}
                  </p>
                </div>
                <div className="rounded-xl border border-slate-200 p-3">
                  <p className="text-xs text-slate-500 uppercase tracking-wide">Off-hours Loss</p>
                  <p className="text-lg font-semibold text-slate-900 mt-1">{formatEnergyKwh(lossOverview.off_hours_kwh)}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    {formatLossOverviewCost(lossOverview.off_hours_cost_inr, lossOverview.currency, lossOverview.costs_available)}
                  </p>
                </div>
                <div className="rounded-xl border border-slate-200 p-3">
                  <p className="text-xs text-slate-500 uppercase tracking-wide">Overconsumption Loss</p>
                  <p className="text-lg font-semibold text-slate-900 mt-1">{formatEnergyKwh(lossOverview.overconsumption_kwh)}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    {formatLossOverviewCost(
                      lossOverview.overconsumption_cost_inr,
                      lossOverview.currency,
                      lossOverview.costs_available,
                    )}
                  </p>
                </div>
                <div className="rounded-xl border border-slate-200 p-3 bg-slate-50">
                  <p className="text-xs text-slate-500 uppercase tracking-wide">Total Loss</p>
                  <p className="text-lg font-semibold text-slate-900 mt-1">{formatEnergyKwh(lossOverview.total_loss_kwh)}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    {formatLossOverviewCost(
                      lossOverview.total_loss_cost_inr,
                      lossOverview.currency,
                      lossOverview.costs_available,
                    )}
                  </p>
                </div>
              </div>
              <p className="text-xs text-slate-500 mt-3">
                Today energy: {formatEnergyKwh(lossOverview.today_energy_kwh)}
                {lossOverview.last_telemetry_ts ? ` · Last telemetry ${formatIST(lossOverview.last_telemetry_ts)}` : ""}
              </p>
              <p className="text-xs text-slate-500 mt-2">{EXCLUSIVE_LOSS_BUCKET_HELP}</p>
              {outsideShiftFinancialBucketMessage && (
                <p className="text-xs text-amber-700 mt-2">{outsideShiftFinancialBucketMessage}</p>
              )}
              </>
              ) : hydrationLoading ? (
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {["Idle Loss", "Off-hours Loss", "Overconsumption Loss", "Total Loss"].map((label) => (
                    <div key={label} className="rounded-xl border border-slate-200 p-3">
                      <p className="text-xs text-slate-500 uppercase tracking-wide">{label}</p>
                      <div className="mt-2 h-5 w-20 animate-pulse rounded bg-slate-200" />
                      <div className="mt-1 h-3 w-16 animate-pulse rounded bg-slate-100" />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-900">
                  Waste and loss overview is not ready yet for this machine.
                </div>
              )}
            </div>

            <div className="mt-3 rounded-2xl border border-slate-200 bg-white p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">CO₂ Emissions</p>
              {lossOverview?.co2_overview?.available ? (
              <>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <div className="rounded-xl border border-[var(--tone-success-border)] bg-[var(--tone-success-bg)] p-3">
                  <p className="text-xs text-[var(--tone-success-text)] uppercase tracking-wide">Today's CO₂</p>
                  <p className="text-lg font-semibold text-slate-900 mt-1">{formatCo2Kg(lossOverview.co2_overview.today?.co2_kg)}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    Based on {lossOverview.co2_overview.today?.energy_kwh?.toFixed(2) ?? "—"} kWh
                  </p>
                </div>
                <div className="rounded-xl border border-slate-200 p-3">
                  <p className="text-xs text-slate-500 uppercase tracking-wide">This Month's CO₂</p>
                  <p className="text-lg font-semibold text-slate-900 mt-1">{formatCo2Kg(lossOverview.co2_overview.month?.co2_kg)}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    Based on {lossOverview.co2_overview.month?.energy_kwh?.toFixed(2) ?? "—"} kWh
                  </p>
                </div>
                <div className="rounded-xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] p-3">
                  <p className="text-xs text-[var(--tone-danger-text)] uppercase tracking-wide">Avoidable CO₂ Today</p>
                  {lossOverview.co2_overview.today?.avoidable_co2_available ? (
                    <>
                      <p className="text-lg font-semibold text-slate-900 mt-1">{formatCo2Kg(lossOverview.co2_overview.today.avoidable_co2_kg)}</p>
                      <p className="text-xs text-slate-500 mt-1">
                        From {lossOverview.co2_overview.today.loss_kwh?.toFixed(2) ?? "—"} kWh loss
                      </p>
                    </>
                  ) : (
                    <>
                      <p className="text-lg font-semibold text-slate-400 mt-1">—</p>
                      <p className="text-xs text-amber-700 mt-1">
                        {lossOverview.co2_overview.today?.avoidable_co2_reason === "loss_data_not_current_day"
                          ? "Available only for the current day"
                          : "Avoidable CO₂ unavailable"}
                      </p>
                    </>
                  )}
                </div>
              </div>
              {lossOverview.co2_overview.factor ? (
                <p className="text-xs text-slate-500 mt-3">
                  {formatCo2Footnote({
                    value: lossOverview.co2_overview.factor.value,
                    unit: lossOverview.co2_overview.factor.unit,
                    source: lossOverview.co2_overview.factor.source,
                    factorSource: lossOverview.co2_overview.factor_source,
                  })}
                </p>
              ) : null}
              </>
              ) : lossOverview?.co2_overview && !lossOverview.co2_overview.available ? (
                <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-900">
                  {lossOverview.co2_overview.reason === "emission_factor_not_configured"
                    ? "CO₂ emissions data is unavailable — an emission factor has not been configured for this organisation."
                    : "CO₂ emissions data is unavailable for this machine."}
                </div>
              ) : hydrationLoading ? (
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {["Today's CO₂", "This Month's CO₂", "Avoidable CO₂ Today"].map((label) => (
                    <div key={label} className="rounded-xl border border-slate-200 p-3">
                      <p className="text-xs text-slate-500 uppercase tracking-wide">{label}</p>
                      <div className="mt-2 h-5 w-20 animate-pulse rounded bg-slate-200" />
                      <div className="mt-1 h-3 w-16 animate-pulse rounded bg-slate-100" />
                    </div>
                  ))}
                </div>
              ) : null}
            </div>

            {showAlertHistory && (
              <div className="absolute right-3 top-16 z-40 w-[calc(100vw-1.5rem)] max-w-[460px] max-h-[520px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl sm:right-6">
                <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">Machine Alerts</p>
                    <p className="text-xs text-slate-500">{shellMachine.id}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowAlertHistory(false)}
                    className="text-slate-400 hover:text-slate-700"
                  >
                    ✕
                  </button>
                </div>
                <div className="max-h-[380px] overflow-y-auto p-3 space-y-3">
                  {alertActionMessage ? (
                    <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-800">
                      {alertActionMessage}
                    </div>
                  ) : null}
                  {activityHistoryError ? (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800">
                      {activityHistoryError}
                    </div>
                  ) : null}
                  {activityHistoryLoading && !activityHistoryLoaded && activityEvents.length === 0 ? (
                    <div className="text-center text-sm text-slate-500 py-8">Loading activity history...</div>
                  ) : null}
                  {activityEvents.length === 0 && !activityHistoryError && (!activityHistoryLoading || activityHistoryLoaded) ? (
                    <div className="text-center text-sm text-slate-500 py-8">No alert history</div>
                  ) : (
                    activityEvents.map((event) => (
                      <div key={event.eventId} className={`rounded-lg border p-3 ${event.isRead ? "bg-slate-50 border-slate-200" : "bg-red-50 border-red-200"}`}>
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-sm font-semibold text-slate-900">{event.title}</p>
                          <span className="text-[11px] px-2 py-0.5 rounded bg-slate-100 text-slate-700">
                            {formatEventType(event.eventType)}
                          </span>
                        </div>
                        <p className="text-xs text-slate-600 mt-1">{event.message}</p>
                        <p className="mt-2 text-[11px] font-medium text-slate-600">
                          Status: {event.eventType === "alert_resolved" ? "Resolved" : event.eventType === "alert_acknowledged" ? "Acknowledged" : event.isRead ? "Seen" : "Open"}
                        </p>
                        {canAcknowledgeAlert && event.alertId && event.eventType === "alert_triggered" ? (
                          <div className="mt-3 flex items-center gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={alertActionBusyId === event.eventId}
                              onClick={() => void handleAlertMutation(event, "acknowledge")}
                            >
                              Acknowledge
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={alertActionBusyId === event.eventId}
                              onClick={() => void handleAlertMutation(event, "resolve")}
                            >
                              Resolve
                            </Button>
                          </div>
                        ) : null}
                        <p className="text-[11px] text-slate-500 mt-2">{formatTimestamp(event.createdAt)}</p>
                      </div>
                    ))
                  )}
                </div>
                <div className="px-4 py-3 border-t border-slate-200 flex items-center justify-between gap-2">
                  <Button variant="outline" size="sm" onClick={handleMarkAllRead}>Mark all read</Button>
                  <Button variant="danger" size="sm" onClick={handleClearHistory}>Clear history</Button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="mb-6 border-b border-slate-200">
          <div className="w-full overflow-x-auto pb-1">
          <nav className="responsive-tab-strip -mb-px" aria-label="Machine detail tabs">
            {visibleTabs.map((tab) => (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                className={`responsive-tab-link border-b-2 px-2 pb-4 pt-3 text-sm font-medium ${activeTab === tab.id ? "border-blue-600 text-blue-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>
                {tab.label}
              </button>
            ))}
          </nav>
          </div>
        </div>

        {(activeTab === "overview" || !activeTabVisible) && (
          <div className="space-y-6">
            {hydrationLoading && telemetry.length === 0 && (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                {[1, 2, 3, 4].map((i) => (
                  <div key={i} className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="h-4 w-24 animate-pulse rounded bg-slate-200" />
                    <div className="mt-3 h-6 w-16 animate-pulse rounded bg-slate-100" />
                  </div>
                ))}
              </div>
            )}
            {kpiState.kind !== "ready" && (
              <Card className={kpiState.kind === "degraded" ? "border-amber-200 bg-amber-50" : "border-slate-200"}>
                <CardHeader className="flex flex-row items-start justify-between gap-4">
                  <div>
                    <CardTitle>{kpiState.title}</CardTitle>
                    <p className="mt-1 text-sm text-slate-600">{kpiState.message}</p>
                  </div>
                  {kpiState.kind === "degraded" && (
                    <Button variant="outline" onClick={() => void fetchHydration()}>
                      Retry KPIs
                    </Button>
                  )}
                </CardHeader>
              </Card>
            )}
            {visibleOverviewMetrics.length > 0 && (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                {visibleOverviewMetrics.map((metric) => {
                  const value = latestOverviewMetrics?.[metric];
                  if (typeof value !== 'number') return null;
                  const healthConfigForMetric = findHealthConfigForMetric(metric, healthConfigs);
                  const parameterScoreForMetric = findParameterScoreForMetric(metric, healthScore?.parameter_scores || []);
                  return (
                    <ParameterEfficiencyCard
                      key={metric}
                      metric={metric}
                      value={value}
                      healthConfig={healthConfigForMetric}
                      parameterScore={parameterScoreForMetric}
                      onConfigure={() => { setSelectedMetric(metric); setShowHealthConfig(true); }}
                    />
                  );
                })}
              </div>
            )}
            {visibleOverviewMetrics.length === 0 && !hydrationLoading && (
              <Card className={kpiState.kind === "degraded" ? "border-amber-200 bg-amber-50" : undefined}>
                <CardContent className="py-10 text-center">
                  <p className="text-base font-medium text-slate-900">
                    {kpiState.title || "Overview KPIs unavailable"}
                  </p>
                  <p className="mt-2 text-sm text-slate-600">
                    {kpiState.message || "Overview KPI cards will appear once telemetry-backed hydration succeeds."}
                  </p>
                </CardContent>
              </Card>
            )}

            {visibleOverviewMetrics.length > 0 && (
              <Card>
                <CardHeader className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <CardTitle>Telemetry Trends</CardTitle>
                    <p className="mt-1 text-sm text-slate-500">
                      {overviewChartRangeLabel}
                      {overviewChartRange !== "live" && overviewHistoryTelemetry.length > 0
                        ? ` · ${overviewHistoryTelemetry.length} historical points`
                        : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {OVERVIEW_CHART_RANGE_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => setOverviewChartRange(option.value)}
                        className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition ${
                          overviewChartRange === option.value
                            ? "border-blue-600 bg-blue-50 text-blue-700"
                            : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                        }`}
                        aria-pressed={overviewChartRange === option.value}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </CardHeader>
                {overviewHistoryLoading && overviewChartRange !== "live" && (
                  <CardContent className="pt-0">
                    <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700">
                      Loading historical telemetry for {overviewChartRangeLabel.toLowerCase()}...
                    </div>
                  </CardContent>
                )}
                {overviewHistoryError && overviewChartRange !== "live" && (
                  <CardContent className="pt-0">
                    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                      {overviewHistoryError}
                    </div>
                  </CardContent>
                )}
              </Card>
            )}

            {overviewChartTelemetry.length > 0 && visibleOverviewMetrics.length > 0 && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {visibleOverviewMetrics.map((metric) => {
                  const data = getMetricData(overviewChartTelemetry, metric);
                  if (data.length === 0) return null;
                  return <Card key={metric}><CardHeader><CardTitle>{METRIC_LABELS[metric] || metric} Trend</CardTitle></CardHeader><CardContent><TimeSeriesChart data={data} color={METRIC_COLORS[metric] || "#2563eb"} unit={METRIC_UNITS[metric] || ""} /></CardContent></Card>;
                })}
              </div>
            )}

            <div ref={trendSectionRef}>
            <Card>
              <CardHeader className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle>Performance Trends</CardTitle>
                  <p className="text-sm text-slate-500 mt-1">Recent telemetry-derived {trendMetric} trend</p>
                </div>
                <div className="flex w-full flex-wrap items-center gap-2 justify-start sm:w-auto sm:justify-end">
                  <div className="inline-flex rounded-lg border border-slate-200 p-1">
                    {([
                      { value: "health", label: "Health" },
                      { value: "uptime", label: "Uptime" },
                    ] as { value: PerformanceTrendMetric; label: string }[]).map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        onClick={() => setTrendMetric(item.value)}
                        className={`px-3 py-1.5 text-sm rounded-md ${
                          trendMetric === item.value
                            ? "bg-blue-600 text-white"
                            : "text-slate-600 hover:bg-slate-100"
                        }`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                  <div className="inline-flex rounded-lg border border-slate-200 p-1">
                    {TREND_RANGE_OPTIONS.map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        onClick={() => setTrendRange(item.value)}
                        className={`px-2.5 py-1.5 text-sm rounded-md ${
                          trendRange === item.value
                            ? "bg-slate-800 text-white"
                            : "text-slate-600 hover:bg-slate-100"
                        }`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {!trendSectionPrimed ? (
                  <div className="h-64 flex flex-col items-center justify-center text-slate-500">
                    <p>Trend data will load when this section comes into view.</p>
                    <p className="text-sm mt-1">Overview details stay fast until you reach the trend section.</p>
                  </div>
                ) : trendLoading ? (
                  <div className="h-64 flex items-center justify-center text-slate-500">Loading trends...</div>
                ) : trendError ? (
                  <div className="h-64 flex items-center justify-center text-red-600">{trendError}</div>
                ) : trendDisplay.empty ? (
                  <div className="h-64 flex flex-col items-center justify-center text-slate-500">
                    <p>No {trendMetric} trend data available.</p>
                    <p className="text-sm mt-1">{trendDisplay.message || "Configure health/shift settings and wait for trend snapshots."}</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <TimeSeriesChart
                      data={trendDisplay.hasMeasuredData ? trendDisplay.chartData : trendDisplay.staleChartData}
                      color={trendMetric === "health" ? "#10b981" : "#2563eb"}
                      unit="%"
                      showArea={trendDisplay.hasMeasuredData}
                      strokeDasharray={trendDisplay.hasFallbackOnly ? "8 6" : undefined}
                      lineName={trendDisplay.hasFallbackOnly ? "Last known value" : undefined}
                      title={`${trendMetric === "health" ? "Health Score" : "Uptime"} (${trendRange})`}
                    />
                    {trendDisplay.hasFallbackOnly && (
                      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                        <p className="font-medium">{trendDisplay.message}</p>
                        {trendDisplay.staleLabel && (
                          <p className="mt-1 text-amber-800">{trendDisplay.staleLabel}</p>
                        )}
                      </div>
                    )}
                    {trendDisplay.hasMeasuredData && trendData?.metric_message && (
                      <p className="text-xs text-slate-500">{trendData.metric_message}</p>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
            </div>

          </div>
        )}

        {activeTabVisible && activeTab === "telemetry" && (
          <div className="space-y-6">
            {telemetryStreamRows.length === 0 ? <Card><CardContent className="py-12 text-center text-slate-500">No recent telemetry seed yet. Load older history below to check earlier samples.</CardContent></Card> : (
              <Card>
                <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <CardTitle>Recent Telemetry</CardTitle>
                    <p className="text-xs text-slate-400 mt-1">
                      Auto-refresh every 1s • {telemetryBufferedRowCount} buffered rows • Page {telemetryTableCurrentPage} of {telemetryTableTotalPages}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={telemetryTableCurrentPage <= 1}
                      onClick={() => setTelemetryTablePage((prev) => Math.max(1, prev - 1))}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={telemetryTableCurrentPage >= telemetryTableTotalPages}
                      onClick={() => setTelemetryTablePage((prev) => Math.min(telemetryTableTotalPages, prev + 1))}
                    >
                      Next
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  <>
                    <div className="space-y-3 md:hidden">
                      {telemetryTableVisibleRows.map((point, i) => (
                        <TelemetryRowCard
                          key={`${point.timestamp}-${i}`}
                          point={point}
                          metrics={dynamicMetrics}
                        />
                      ))}
                    </div>
                    <div className="hidden overflow-x-auto md:block">
                      <table className="min-w-full divide-y divide-slate-200">
                        <thead className="bg-slate-50"><tr>
                          <th className="px-6 py-3 text-left text-xs font-medium text-slate-500">Timestamp</th>
                          {dynamicMetrics.map((m) => (
                            <th key={m} className="px-6 py-3 text-left text-xs font-medium text-slate-500">
                              {METRIC_LABELS[m] || m}{isPhaseDiagnosticField(m) ? <span className="block text-[10px] font-normal text-slate-400">Diagnostic</span> : null}
                            </th>
                          ))}
                        </tr></thead>
                        <tbody className="bg-white divide-y">
                          {telemetryTableVisibleRows.map((point, i) => (
                            <tr key={i} className={telemetryTableCurrentPage === 1 && i === 0 ? "bg-blue-50" : ""}>
                              <td className="px-6 py-3 text-sm font-mono">{formatTimestamp(point.timestamp)}</td>
                              {dynamicMetrics.map((m) => {
                                const value = point[m];
                                return <td key={m} className="px-6 py-3 text-sm">{typeof value === "number" ? value.toFixed(2) : "—"}</td>;
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                  <div className="mt-4 flex flex-col gap-2 text-sm text-slate-500 sm:flex-row sm:items-center sm:justify-between">
                    <span>Showing rows {telemetryTableStartIndex + 1}-{Math.min(telemetryTableStartIndex + telemetryTableVisibleRows.length, telemetryBufferedRowCount)} of {telemetryBufferedRowCount}</span>
                    <span>Newest rows continue to stream into the buffer at the top.</span>
                  </div>
                </CardContent>
              </Card>
            )}
            <Card>
              <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle>Older Telemetry History</CardTitle>
                  <p className="mt-1 text-sm text-slate-500">
                    Recent telemetry loads from the fast projection lane first. Older history is fetched on demand.
                  </p>
                </div>
                <Button variant="outline" onClick={() => void loadOlderTelemetryHistory()} disabled={telemetryHistoryLoading}>
                  {telemetryHistoryLoading ? "Loading..." : "Load Older History"}
                </Button>
              </CardHeader>
              <CardContent>
                {telemetryHistoryError ? (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-900">
                    {telemetryHistoryError}
                  </div>
                ) : telemetryHistoryLoading ? (
                  <div className="py-8 text-center text-slate-500">Loading older telemetry history...</div>
                ) : telemetryHistoryRows.length > 0 ? (
                  <>
                    <div className="space-y-3 md:hidden">
                      {telemetryHistoryRows.map((point, i) => (
                        <TelemetryRowCard
                          key={`${point.timestamp}-${i}`}
                          point={point}
                          metrics={dynamicMetrics}
                        />
                      ))}
                    </div>
                    <div className="hidden overflow-x-auto md:block">
                      <table className="min-w-full divide-y divide-slate-200">
                        <thead className="bg-slate-50"><tr>
                          <th className="px-6 py-3 text-left text-xs font-medium text-slate-500">Timestamp</th>
                          {dynamicMetrics.map((m) => (
                            <th key={m} className="px-6 py-3 text-left text-xs font-medium text-slate-500">
                              {METRIC_LABELS[m] || m}{isPhaseDiagnosticField(m) ? <span className="block text-[10px] font-normal text-slate-400">Diagnostic</span> : null}
                            </th>
                          ))}
                        </tr></thead>
                        <tbody className="bg-white divide-y">
                          {telemetryHistoryRows.map((point, i) => (
                            <tr key={`${point.timestamp}-${i}`}>
                              <td className="px-6 py-3 text-sm font-mono">{formatTimestamp(point.timestamp)}</td>
                              {dynamicMetrics.map((m) => {
                                const value = point[m];
                                return <td key={m} className="px-6 py-3 text-sm">{typeof value === "number" ? value.toFixed(2) : "—"}</td>;
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : telemetryHistoryLoaded ? (
                  <div className="py-8 text-center text-slate-500">
                    {telemetryStreamRows.length > 0
                      ? "No older telemetry history beyond the recent seed."
                      : "No telemetry received yet for this machine."}
                  </div>
                ) : (
                  <div className="py-8 text-center text-slate-500">
                    {telemetryStreamRows.length > 0
                      ? "Recent telemetry is shown above. Load older history when you need deeper rows."
                      : "Load telemetry history to check whether this machine has older samples."}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {activeTabVisible && activeTab === "maintenance" && (
          <div className="space-y-6">
            {maintenanceLoading ? (
              <>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  {Array.from({ length: 4 }).map((_, index) => (
                    <Card key={index}>
                      <CardContent className="space-y-3">
                        <div className="h-3 w-24 animate-pulse rounded bg-slate-200" />
                        <div className="h-8 w-32 animate-pulse rounded bg-slate-200" />
                        <div className="h-3 w-20 animate-pulse rounded bg-slate-100" />
                      </CardContent>
                    </Card>
                  ))}
                </div>
                <Card>
                  <CardContent className="space-y-4 py-6">
                    {Array.from({ length: 3 }).map((_, index) => (
                      <div key={index} className="rounded-2xl border border-slate-200 p-4">
                        <div className="h-4 w-40 animate-pulse rounded bg-slate-200" />
                        <div className="mt-3 h-3 w-full animate-pulse rounded bg-slate-100" />
                        <div className="mt-2 h-3 w-5/6 animate-pulse rounded bg-slate-100" />
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </>
            ) : maintenanceError && !maintenanceHasVisibleData ? (
              <Card>
                <CardContent className="py-10 text-center">
                  <p className="text-base font-semibold text-slate-900">Maintenance Log is unavailable right now</p>
                  <p className="mt-2 text-sm text-slate-500">{maintenanceError}</p>
                  <Button className="mt-4" variant="outline" onClick={() => void loadMaintenanceLog()}>
                    Try Again
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  <Card>
                    <CardContent>
                      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Last Maintenance</p>
                      <p className="mt-3 text-2xl font-semibold text-slate-900">
                        {formatMaintenanceDate(maintenanceSummary?.latest_maintenance_date, "No records yet")}
                      </p>
                      <p className="mt-2 text-sm text-slate-500">
                        {maintenanceSummary?.last_recorded_at ? `Updated ${formatIST(maintenanceSummary.last_recorded_at)}` : "No maintenance history recorded yet"}
                      </p>
                    </CardContent>
                  </Card>
                  <Card>
                    <CardContent>
                      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Total Records</p>
                      <p className="mt-3 text-2xl font-semibold text-slate-900">{maintenanceSummary?.total_records ?? 0}</p>
                      <p className="mt-2 text-sm text-slate-500">Every entry is saved against this machine</p>
                    </CardContent>
                  </Card>
                  <Card>
                    <CardContent>
                      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Total Cost</p>
                      <p className="mt-3 text-2xl font-semibold text-slate-900">
                        {formatCurrencyValue(maintenanceSummary?.total_cost ?? 0, "INR")}
                      </p>
                      <p className="mt-2 text-sm text-slate-500">Based on recorded maintenance spending</p>
                    </CardContent>
                  </Card>
                  <Card>
                    <CardContent>
                      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Next Due</p>
                      <p className="mt-3 text-2xl font-semibold text-slate-900">
                        {formatMaintenanceDate(maintenanceSummary?.next_due_date, "Not scheduled")}
                      </p>
                      <p className="mt-2 text-sm text-slate-500">Shows the nearest upcoming due date on file</p>
                    </CardContent>
                  </Card>
                </div>

                <Card>
                  <CardHeader className="flex flex-row items-center justify-between gap-4">
                    <div>
                      <CardTitle>Maintenance History</CardTitle>
                      <p className="mt-1 text-sm text-slate-500">
                        A clear record of work completed for this machine.
                      </p>
                    </div>
                    {canManageMaintenance ? (
                      <Button variant="outline" onClick={openAddMaintenanceModal}>
                        Add Maintenance
                      </Button>
                    ) : null}
                  </CardHeader>
                  <CardContent>
                    {maintenanceError && maintenanceHasVisibleData ? (
                      <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                        {maintenanceError}
                      </div>
                    ) : null}
                    {maintenanceActionMessage ? (
                      <div className="mb-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
                        {maintenanceActionMessage}
                      </div>
                    ) : null}
                    {maintenanceEmpty ? (
                      <div className="rounded-3xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                        <p className="text-lg font-semibold text-slate-900">No maintenance records yet</p>
                        <p className="mt-2 text-sm text-slate-500">
                          When service activity is recorded for this machine, it will appear here with dates, costs, and notes.
                        </p>
                        {canManageMaintenance ? (
                          <Button className="mt-5" variant="outline" onClick={openAddMaintenanceModal}>
                            Add the first maintenance record
                          </Button>
                        ) : null}
                      </div>
                    ) : (
                      <div className="space-y-4">
                        {maintenanceRecords.map((record) => (
                          <div
                            key={record.id}
                            className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition-shadow hover:shadow-md"
                          >
                            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                  <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <p className="text-base font-semibold text-slate-900">{record.title}</p>
                                      {record.status ? (
                                        <StatusBadge
                                          status={record.status.replace(/_/g, " ")}
                                          className="capitalize"
                                        />
                                      ) : null}
                                    </div>
                                  </div>
                                  {canManageMaintenance ? (
                                    <div className="flex items-center gap-2">
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => openEditMaintenanceModal(record)}
                                      >
                                        Edit
                                      </Button>
                                      {canDeleteMaintenance ? (
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                                          onClick={() => {
                                            setMaintenanceDeleteError(null);
                                            setMaintenanceDeleteTarget(record);
                                          }}
                                        >
                                          Delete
                                        </Button>
                                      ) : null}
                                    </div>
                                  ) : null}
                                </div>
                                <p className="mt-2 text-sm leading-6 text-slate-600">
                                  {truncateDescription(record.description)}
                                </p>
                                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-sm text-slate-500">
                                  <span>
                                    <span className="font-medium text-slate-700">Date:</span>{" "}
                                    {formatMaintenanceDate(record.maintenance_date, "—")}
                                  </span>
                                  <span>
                                    <span className="font-medium text-slate-700">Performed by:</span>{" "}
                                    {record.performed_by || "Not recorded"}
                                  </span>
                                  {record.next_due_date ? (
                                    <span>
                                      <span className="font-medium text-slate-700">Next due:</span>{" "}
                                      {formatMaintenanceDate(record.next_due_date)}
                                    </span>
                                  ) : null}
                                </div>
                              </div>
                              <div className="shrink-0 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-left lg:min-w-40 lg:text-right">
                                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Cost</p>
                                <p className="mt-2 text-xl font-semibold text-slate-900">
                                  {formatCurrencyValue(record.cost, "INR")}
                                </p>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </>
            )}
          </div>
        )}

        {activeTabVisible && activeTab === "parameters" && (
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>Telemetry Widgets</CardTitle>
                <p className="text-sm text-slate-600">
                  Select telemetry widgets to show on this machine dashboard.
                </p>
              </CardHeader>
              <CardContent>
                {widgetConfig && widgetConfig.available_fields.length > 0 ? (
                  <>
                    <div className="flex flex-wrap gap-3">
                      {widgetConfig.available_fields.map((field) => {
                        const selected = selectedWidgetFieldSet.has(field);
                        return (
                          <button
                            key={field}
                            type="button"
                            onClick={() => handleToggleWidgetField(field)}
                            className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition ${
                              selected
                                ? "border-blue-300 bg-blue-50 text-blue-700"
                                : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                            }`}
                          >
                            <span className={`inline-flex h-4 w-4 items-center justify-center rounded border text-xs ${
                              selected ? "border-blue-500 bg-blue-600 text-white" : "border-slate-400 text-transparent"
                            }`}>
                              ✓
                            </span>
                            <span
                              className="h-2.5 w-2.5 rounded-full"
                              style={{ backgroundColor: METRIC_COLORS[field] || "#64748b" }}
                            />
                            {METRIC_LABELS[field] || field}
                          </button>
                        );
                      })}
                    </div>
                    <div className="mt-4 flex items-center gap-3">
                      <Button onClick={handleSaveWidgetConfig} disabled={widgetSaving || !widgetDirty}>
                        {widgetSaving ? "Saving..." : "Save Widgets"}
                      </Button>
                      {widgetDirty && (
                        <p className="text-xs text-amber-700">
                          Unsaved changes
                        </p>
                      )}
                      {widgetConfig.default_applied && (
                        <p className="text-xs text-slate-500">
                          Default mode active: all discovered widgets are shown until you save.
                        </p>
                      )}
                      {widgetSaveMessage && (
                        <p className="text-xs text-emerald-700">{widgetSaveMessage}</p>
                      )}
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-slate-500">
                    No numeric telemetry fields discovered yet. Start telemetry to configure widgets.
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle>Shift Configuration</CardTitle>
                <Button onClick={() => {
                  if (showAddShift) {
                    setEditingShiftId(null);
                    setNewShift({ shift_name: "", shift_start: "09:00", shift_end: "17:00", maintenance_break_minutes: 0, day_of_week: null, is_active: true });
                  }
                  setShowAddShift(!showAddShift);
                }}>{showAddShift ? "Cancel" : "+ Add Shift"}</Button>
              </CardHeader>
              <CardContent>
                {showAddShift && (
                  <div className="bg-slate-50 p-4 rounded-lg mb-6 space-y-4">
                    <p className="text-xs text-slate-600">
                      Rule: overlaps are not allowed. Touching boundaries are allowed (for example, 09:00-10:00 and 10:00-11:00). Overnight shifts are shown as <span className="font-semibold">(+1 day)</span>.
                    </p>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div><label className="block text-sm font-medium mb-1">Shift Name</label><input type="text" value={newShift.shift_name} onChange={(e) => setNewShift({ ...newShift, shift_name: e.target.value })} placeholder="e.g., Morning Shift" className="w-full px-3 py-2 border rounded-md" /></div>
                      <div><label className="block text-sm font-medium mb-1">Day of Week</label><select value={newShift.day_of_week ?? ""} onChange={(e) => setNewShift({ ...newShift, day_of_week: e.target.value ? parseInt(e.target.value) : null })} className="w-full px-3 py-2 border rounded-md">{DAYS_OF_WEEK.map(d => <option key={d.value ?? "all"} value={d.value ?? ""}>{d.label}</option>)}</select></div>
                      <div><label className="block text-sm font-medium mb-1">Start Time</label><input type="time" value={newShift.shift_start} onChange={(e) => setNewShift({ ...newShift, shift_start: e.target.value })} className="w-full px-3 py-2 border rounded-md" /></div>
                      <div><label className="block text-sm font-medium mb-1">End Time</label><input type="time" value={newShift.shift_end} onChange={(e) => setNewShift({ ...newShift, shift_end: e.target.value })} className="w-full px-3 py-2 border rounded-md" /></div>
                      <div><label className="block text-sm font-medium mb-1">Maintenance Break (min)</label><input type="number" min="0" max="480" value={newShift.maintenance_break_minutes} onChange={(e) => setNewShift({ ...newShift, maintenance_break_minutes: parseInt(e.target.value) || 0 })} className="w-full px-3 py-2 border rounded-md" /></div>
                    </div>
                    {shiftFormError && (
                      <p className="text-sm text-red-600">{shiftFormError}</p>
                    )}
                    <Button onClick={handleAddShift} disabled={shiftFormBlocked}>{editingShiftId !== null ? "Save Changes" : "Save Shift"}</Button>
                  </div>
                )}
                {shifts.length === 0 ? <div className="text-center py-8 text-slate-500">No shifts configured</div> : (
                  <div className="space-y-4">
                    {shifts.map((shift) => (
                      <div key={shift.id} className={`flex items-center justify-between p-4 rounded-lg border ${shift.is_active ? "bg-white" : "bg-slate-50 opacity-60"}`}>
                        <div>
                          <div className="flex items-center gap-2"><h3 className="font-medium">{shift.shift_name}</h3>{!shift.is_active && <span className="text-xs bg-slate-200 px-2 py-0.5 rounded">Inactive</span>}</div>
                          <p className="text-sm text-slate-500 mt-1">
                            {formatShiftRange(shift.shift_start, shift.shift_end)}
                            {isOvernightRange(shift.shift_start, shift.shift_end) && (
                              <span className="ml-2 inline-flex rounded bg-indigo-100 px-2 py-0.5 text-[11px] font-medium text-indigo-700">Overnight shift</span>
                            )}
                            {shift.maintenance_break_minutes > 0 && <span className="ml-2">(Break: {shift.maintenance_break_minutes} min)</span>}
                          </p>
                          <p className="text-xs text-slate-400 mt-1">{DAYS_OF_WEEK.find(d => d.value === shift.day_of_week)?.label || "All Days"}</p>
                        </div>
                        {canDeleteDevice ? (
                          <div className="flex items-center gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                setEditingShiftId(shift.id);
                                setShowAddShift(true);
                                setNewShift({
                                  shift_name: shift.shift_name,
                                  shift_start: shift.shift_start,
                                  shift_end: shift.shift_end,
                                  maintenance_break_minutes: shift.maintenance_break_minutes,
                                  day_of_week: shift.day_of_week,
                                  is_active: shift.is_active,
                                });
                              }}
                            >
                              Edit
                            </Button>
                            <Button variant="danger" size="sm" onClick={() => handleDeleteShift(shift.id)}>Delete</Button>
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Parameter Health Configuration</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="mb-4 p-4 bg-blue-50 rounded-lg">
                  <p className="text-sm text-blue-800"><strong>Health Score:</strong> Each configured parameter gets a score of 100, 50, or 0 based on whether the value is in range, near range, or outside tolerance. The overall health score is the weighted sum of those parameter scores.</p>
                  <p className="text-sm text-blue-800 mt-1"><strong>Machine State:</strong> Health scoring runs for RUNNING, IDLE, and UNLOAD. For OFF and POWER CUT, the score shows as &quot;Standby&quot;.</p>
                  <p className="text-sm text-blue-800 mt-1"><strong>Weights:</strong> All active parameter weights must sum to 100%.</p>
                </div>
                
                {dynamicMetrics.length > 0 ? (
                  <>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {dynamicMetrics.map((metric) => {
                  const config = findHealthConfigForMetric(metric, healthConfigs);
                  const matchingConfigs = findMatchingHealthConfigsForMetric(metric, healthConfigs);
                  return (
                    <div key={metric} className={`p-4 rounded-lg border ${config?.is_active ? "bg-white" : "bg-slate-50 opacity-60"}`}>
                          <div className="flex items-center justify-between mb-2">
                            <h4 className="font-medium">{METRIC_LABELS[metric] || metric}</h4>
                            {matchingConfigs.length > 1 ? (
                              <span className="text-xs bg-amber-100 text-amber-700 px-2 py-0.5 rounded">Duplicate Configs</span>
                            ) : (
                              config && <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded">Configured</span>
                            )}
                          </div>
                          {config ? (
                            <div className="text-sm text-slate-600 space-y-1">
                              <p>Normal: {config.normal_min ?? "—"} - {config.normal_max ?? "—"}</p>
                              <p>Weight: {config.weight}%</p>
                              <p>Ignore Zero: {config.ignore_zero_value ? "Yes" : "No"}</p>
                              {matchingConfigs.length > 1 ? (
                                <p className="text-amber-700">
                                  Backend has {matchingConfigs.length} matching configs for this metric. The newest one is shown here.
                                </p>
                              ) : null}
                            </div>
                          ) : (
                            <p className="text-sm text-slate-500">Not configured</p>
                          )}
                          {canEditDevice ? (
                            <Button size="sm" className="mt-3 w-full" onClick={() => { setSelectedMetric(metric); setShowHealthConfig(true); }}>
                              {config ? "Edit" : "Configure"}
                            </Button>
                          ) : null}
                    </div>
                  );
                })}
                    </div>
                    {healthConfigs.length > 0 ? (
                      <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                        <p className="text-sm font-semibold text-slate-900">Configuration History</p>
                        <div className="mt-3 space-y-3">
                          {healthConfigs
                            .slice()
                            .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))
                            .map((config) => (
                              <div key={config.id} className="rounded-xl border border-slate-200 bg-white px-4 py-3">
                                <div className="flex flex-wrap items-center justify-between gap-3">
                                  <p className="text-sm font-semibold text-slate-900">{METRIC_LABELS[config.parameter_name] || config.parameter_name}</p>
                                  <span className="text-xs text-slate-500">Weight {config.weight}%</span>
                                </div>
                                <p className="mt-1 text-xs text-slate-500">
                                  Created {formatIST(config.created_at, "—")} • Last updated {formatIST(config.updated_at, "—")}
                                </p>
                              </div>
                            ))}
                        </div>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div className="text-center py-8 text-slate-500">No telemetry parameters available</div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Load Classification Configuration</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <p className="text-sm text-slate-600">
                    Full load current (FLA) is the primary engineering input. Idle is derived as a percentage of FLA,
                    and overconsumption starts above FLA. Loss booking still uses measured telemetry energy.
                  </p>
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div>
                      <label className="block text-sm font-medium mb-1">Full Load Current (A)</label>
                      <input
                        type="number"
                        min="0.01"
                        step="0.01"
                        value={fullLoadCurrentInput}
                        onChange={(e) => setFullLoadCurrentInput(e.target.value)}
                        className="w-full px-3 py-2 border rounded-md"
                        placeholder="e.g. 20.00"
                      />
                      <p className="mt-1 text-xs text-slate-500">
                        Saved: {hydrationLoading && persistedFullLoadCurrent == null && fullLoadCurrentInput === "" ? "Loading..." : persistedFullLoadCurrent != null ? `${persistedFullLoadCurrent.toFixed(2)} A` : "Not configured"}
                      </p>
                      {fullLoadCurrentDraftDiffersFromSaved && (
                        <p className="mt-1 text-xs text-amber-700">Draft differs from saved value.</p>
                      )}
                    </div>
                    <div>
                      <label className="block text-sm font-medium mb-1">Idle Threshold Percent of FLA</label>
                      <input
                        type="number"
                        min="0.01"
                        max="0.99"
                        step="0.01"
                        value={idleThresholdPctInput}
                        onChange={(e) => setIdleThresholdPctInput(e.target.value)}
                        className="w-full px-3 py-2 border rounded-md"
                        placeholder="Defaults to 0.25"
                      />
                      <p className="mt-1 text-xs text-slate-500">
                        Saved: {persistedIdleThresholdPct != null ? formatIdleThresholdPctLabel(persistedIdleThresholdPct) : "25% of FLA"}
                      </p>
                      {idleThresholdPctDraftDiffersFromSaved && (
                        <p className="mt-1 text-xs text-amber-700">Draft differs from saved value.</p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Button
                      onClick={handleSaveEngineeringConfig}
                      disabled={engineeringSaving || Boolean(engineeringSaveBlockReason)}
                    >
                      {engineeringSaving ? "Saving..." : "Save Classification"}
                    </Button>
                    {engineeringSaveMessage && (
                      <span className="text-sm text-emerald-700">{engineeringSaveMessage}</span>
                    )}
                  </div>
                  {engineeringSaveBlockReason && (
                    <p className="text-sm text-amber-700">{engineeringSaveBlockReason}</p>
                  )}

                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 space-y-1">
                    <p>Idle default: 25% of FLA unless you override the idle percentage.</p>
                    <p>
                      Derived idle threshold:{" "}
                      <span className="font-semibold text-slate-800">
                        {thresholdPreview.derivedIdleThreshold != null
                          ? `${thresholdPreview.derivedIdleThreshold.toFixed(2)} A`
                          : "Unavailable until FLA is configured"}
                      </span>
                    </p>
                    <p>
                      Derived overconsumption threshold:{" "}
                      <span className="font-semibold text-slate-800">
                        {thresholdPreview.derivedOverconsumptionThreshold != null
                          ? `${thresholdPreview.derivedOverconsumptionThreshold.toFixed(2)} A`
                          : "Unavailable until FLA is configured"}
                      </span>
                    </p>
                    <p>
                      Current operating band:{" "}
                      <span className="font-semibold text-slate-800">{currentBandLabel}</span>
                    </p>
                    <p>
                      Auto-detected current field:{" "}
                      <span className="font-semibold text-slate-800">
                        {currentState?.current_field || "Not detected"}
                      </span>
                    </p>
                    <p>
                      Device type:{" "}
                      <span className="font-semibold text-slate-800 capitalize">
                        {machine.data_source_type || "metered"}
                      </span>
                    </p>
                    <p>{OVERCONSUMPTION_THRESHOLD_HELP}</p>
                    {!currentState?.current_field && (
                      <p className="text-amber-700">
                        No current parameter found in telemetry. Idle and overconsumption detection will remain unavailable until current data is received.
                      </p>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {activeTabVisible && activeTab === "rules" && <MachineRulesView deviceId={deviceId} />}
      </div>
      
      <HealthConfigModal
        key={`${selectedMetric}-${findHealthConfigForMetric(selectedMetric, healthConfigs)?.id ?? "new"}`}
        isOpen={showHealthConfig}
        onClose={() => { setShowHealthConfig(false); setSelectedMetric(""); }}
        deviceId={deviceId}
        metric={selectedMetric}
        existingConfig={findHealthConfigForMetric(selectedMetric, healthConfigs)}
        allConfigs={healthConfigs}
        onSave={handleSaveHealthConfig}
        onDelete={handleDeleteHealthConfig}
      />
      {showMaintenanceModal ? (
        <MaintenanceLogFormModal
          key={maintenanceEditingRecord ? `edit-${maintenanceEditingRecord.id}` : "create-maintenance-record"}
          isOpen={showMaintenanceModal}
          record={maintenanceEditingRecord}
          onClose={closeMaintenanceModal}
          onSubmit={handleSubmitMaintenanceRecord}
          isSubmitting={maintenanceSubmitting}
          error={maintenanceSubmitError}
        />
      ) : null}
      <DeleteMaintenanceLogDialog
        isOpen={Boolean(maintenanceDeleteTarget)}
        record={maintenanceDeleteTarget}
        onClose={closeMaintenanceDeleteDialog}
        onConfirm={handleDeleteMaintenanceRecord}
        isDeleting={maintenanceDeleting}
        error={maintenanceDeleteError}
      />
    </div>
  );
}
