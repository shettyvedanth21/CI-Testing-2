"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AsyncJobHandoffCard } from "@/components/reports/AsyncJobHandoffCard";
import { getDevices, Device } from "@/lib/deviceApi";
import { authApi, type PlantProfile } from "@/lib/authApi";
import { useAuth } from "@/lib/authContext";
import { useTenantStore } from "@/lib/tenantStore";
import { resolveScopedTenantId, resolveVisiblePlants } from "@/lib/orgScope";
import { DeviceScopeSelector } from "@/components/reports/DeviceScopeSelector";
import {
  buildDeviceScopeCatalog,
  getDeviceScopeSummary,
  normalizeDeviceScopeSelection,
  resolveDeviceIdsForSelection,
  type DeviceScopeSelection,
} from "@/lib/deviceScopeSelection";
import {
  runAnalytics,
  runFleetAnalytics,
  preflightAnalytics,
  getAnalyticsStatus,
  getFormattedResults,
  getSupportedModels,
  listAnalyticsJobs,
  AnomalyFormattedResult,
  FailureFormattedResult,
  FleetFormattedResult,
  BlockedFormattedResult,
  AnalyticsJobListItem,
  ApiError,
  type AnalyticsPreflightResponse,
} from "@/lib/analyticsApi";
import {
  getAnalyticsConfidenceSummary,
  sanitizeAnalyticsNarrative,
} from "@/lib/analyticsPresentation";
import { formatISTCompact } from "@/lib/utils";
import {
  readAnalyticsHistorySnapshot,
  writeAnalyticsHistorySnapshot,
} from "@/lib/analyticsHistoryCache";
import {
  countLiveAnalyticsJobs,
  getAnalyticsHistoryRefreshMs,
  getAnalyticsStatusPollMs,
  mergeHistoryJobStatus,
  resolveSelectedAnalyticsJobId,
} from "@/lib/analyticsHistoryPolling";
import {
  type FleetProgress,
  formatJobSeconds,
  formatFleetProgressBadges,
  formatJobStatusSummary,
  getRunningJobTruthfulnessNote,
  getFleetAcceptedMessage,
  getFleetHistorySummary,
  getFleetOutcomeSummary,
  getJobFailureSummary,
  getLongRunningJobErrorMessage,
  getUserFacingJobStatusLabelWithCoverage,
  shouldShowRunningJobEta,
} from "@/lib/asyncJobPresentation";

type Screen = "wizard" | "anomaly" | "failure" | "fleet" | "blocked";
type AnalysisType = "anomaly" | "failure_prediction";
type Preset = "quick" | "recommended" | "deep" | "custom";
type ResultType = AnomalyFormattedResult | FailureFormattedResult | FleetFormattedResult;
type FleetExecItem = { device_id: string; reason?: string; message?: string };
type FleetExecMeta = {
  devices_skipped?: FleetExecItem[];
  devices_failed?: FleetExecItem[];
  devices_ready?: string[];
  selected_device_count?: number;
  coverage_pct?: number;
};
type FleetJobProgress = AnalyticsJobListItem["fleet_progress"];
type FleetResultSnapshot = FleetProgress;

const COLORS = {
  bg: "#f8fafc",
  panel: "#ffffff",
  panelBorder: "rgba(148, 163, 184, 0.3)",
  text: "#1e293b",
  muted: "rgba(71, 85, 105, 0.8)",
  accent: "#6366f1",
  good: "#22c55e",
  warn: "#f59e0b",
  bad: "#ef4444",
};

const PRESET_LABELS: Record<Preset, string> = {
  quick: "Last 24 Hours",
  recommended: "Last 7 Days",
  deep: "Last 30 Days",
  custom: "Custom",
};
const ANALYTICS_MAX_RANGE_DAYS = 30;

function formatYmd(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function getAnalyticsRangeSpanDays(start: string, end: string): number {
  if (!start || !end) return 0;
  const startTs = new Date(start).getTime();
  const endTs = new Date(end).getTime();
  if (!Number.isFinite(startTs) || !Number.isFinite(endTs) || endTs < startTs) return 0;
  return Math.round((endTs - startTs) / 86400000);
}

function getPresetRange(preset: Preset): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  if (preset === "quick") start.setDate(end.getDate() - 1);
  else if (preset === "recommended") start.setDate(end.getDate() - 7);
  else if (preset === "deep") start.setDate(end.getDate() - 30);
  return { start: formatYmd(start), end: formatYmd(end) };
}

function formatDaysAnalysed(days: number): string {
  if (!Number.isFinite(days) || days <= 0) return "0 minutes";
  if (days < 1) {
    const hours = Math.max(1, Math.round(days * 24));
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  const wholeDays = Math.max(1, Math.round(days));
  return `${wholeDays} day${wholeDays === 1 ? "" : "s"}`;
}

function badgeColor(level: string): string {
  if (level === "Very High") return "#4f46e5";
  if (level === "High") return "#22c55e";
  if (level === "Moderate") return "#f59e0b";
  if (/^\d+(\.\d+)?%?$/.test(level.trim())) return COLORS.text;
  return "#ef4444";
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : fallback;
}

function formatHistoryDate(value?: string | null): string {
  return formatISTCompact(value ?? null, "—");
}

function formatAnalysisLabel(result: ResultType | null): string {
  if (!result) return "Analysis";
  if (result.analysis_type === "anomaly_detection") return "Anomaly Detection";
  if (result.analysis_type === "failure_prediction") return "Risk Assessment";
  return "Fleet Analytics";
}

function isBlockedFormattedResult(result: ResultType | BlockedFormattedResult): result is BlockedFormattedResult {
  return (
    "status" in result &&
    (result.status === "no_data" || result.status === "insufficient_coverage") &&
    "summary" in result &&
    !("anomaly_list" in result) &&
    !("risk_factors" in result) &&
    !("device_summaries" in result)
  );
}

function isNoDataOutcome(errorCode?: string | null): boolean {
  return errorCode === "NO_TELEMETRY_IN_RANGE";
}

function formatHistoryStatusDetail(job: AnalyticsJobListItem): string {
  if (job.workflow_kind === "fleet" && job.fleet_progress) {
    const fleetSummary = getFleetHistorySummary(job.fleet_progress);
    if (job.status === "pending") {
      return fleetSummary ?? "Fleet analysis is queued";
    }
    if (job.status === "running") {
      const phaseText = job.phase_label?.trim() || "Running in stages";
      return fleetSummary ? `${phaseText} · ${fleetSummary}` : phaseText;
    }
    if (job.status === "completed") {
      return fleetSummary ? `Fleet coverage: ${fleetSummary}` : "Fleet analysis completed";
    }
  }
  if (job.status === "pending") {
    const queueText = typeof job.queue_position === "number" ? `Queue position ${job.queue_position + 1}` : "Queued";
    const waitText = formatJobSeconds(job.estimated_wait_seconds);
    return waitText ? `${queueText} · estimated wait ${waitText}` : queueText;
  }
  if (job.status === "running") {
    const phaseText = job.phase_label?.trim() || "Processing";
    const etaText = shouldShowRunningJobEta(job) ? formatJobSeconds(job.estimated_completion_seconds) : "";
    const truthfulnessNote = getRunningJobTruthfulnessNote(job);
    if (etaText) {
      return `${phaseText} · ETA ${etaText}`;
    }
    return truthfulnessNote ? `${phaseText} · ${truthfulnessNote}` : phaseText;
  }
  if (job.status === "completed") {
    return job.result_ready ? "Result ready to view" : "Completed";
  }
  if (isNoDataOutcome(job.error_code)) {
    return "No telemetry was available in the selected period";
  }
  return job.error_message ?? job.message ?? "Processing could not be completed";
}

function getFleetProgressSummary(
  fleetProgress: FleetJobProgress | null | undefined,
  fallbackSelectedCount?: number,
): string | null {
  const selectedCount =
    typeof fleetProgress?.selected_device_count === "number"
      ? fleetProgress.selected_device_count
      : fallbackSelectedCount;
  if (!selectedCount || selectedCount <= 1) return null;
  const badges = formatFleetProgressBadges({
    ...fleetProgress,
    selected_device_count: selectedCount,
  });
  return badges.length > 0 ? badges.join(" · ") : `${selectedCount} devices selected`;
}

function getFleetSnapshotFromExecutionMetadata(
  exec: FleetExecMeta,
  readyCount: number,
): FleetResultSnapshot {
  const failedCount = Array.isArray(exec.devices_failed) ? exec.devices_failed.length : 0;
  const skippedCount = Array.isArray(exec.devices_skipped) ? exec.devices_skipped.length : 0;
  const selectedCount = Number(exec.selected_device_count ?? readyCount + failedCount + skippedCount);
  const coverage =
    typeof exec.coverage_pct === "number" && Number.isFinite(exec.coverage_pct)
      ? exec.coverage_pct
      : selectedCount > 0
        ? (readyCount / selectedCount) * 100
        : 0;

  return {
    selected_device_count: selectedCount,
    completed_devices: readyCount,
    failed_devices: failedCount,
    skipped_devices: skippedCount,
    coverage_pct: coverage,
  };
}

function ConfidenceSummaryPanel({
  result,
}: {
  result: AnomalyFormattedResult | FailureFormattedResult;
}) {
  const summary = getAnalyticsConfidenceSummary(result);
  return (
    <div style={panelStyle()}>
      <h3 style={titleStyle()}>Confidence Summary</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 8 }}>
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 10 }}>
          <div style={{ fontSize: 10, color: COLORS.muted, textTransform: "uppercase", letterSpacing: 1 }}>
            Analysis Confidence
          </div>
          <div style={{ marginTop: 4, fontSize: 16, fontWeight: 700 }}>{summary.level}</div>
        </div>
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 10 }}>
          <div style={{ fontSize: 10, color: COLORS.muted, textTransform: "uppercase", letterSpacing: 1 }}>
            Evidence Strength
          </div>
          <div style={{ marginTop: 4, fontSize: 16, fontWeight: 700 }}>{summary.evidenceStrength}</div>
        </div>
      </div>
      <div style={{ marginTop: 10, fontSize: 11, fontWeight: 600 }}>{summary.summary}</div>
      <div style={{ marginTop: 6, color: COLORS.muted, fontSize: 11 }}>{summary.interpretation}</div>
      <div style={{ marginTop: 8, fontSize: 11 }}>
        <span style={{ fontWeight: 600 }}>Recommended action:</span> {summary.recommendedAction}
      </div>
      {summary.factors.length > 0 ? (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 10, color: COLORS.muted, textTransform: "uppercase", letterSpacing: 1 }}>
            Key contributing factors
          </div>
          <div style={{ marginTop: 4, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {summary.factors.map((factor) => (
              <span
                key={factor}
                style={{
                  padding: "4px 8px",
                  borderRadius: 999,
                  background: "#eff6ff",
                  color: "#1d4ed8",
                  fontSize: 10,
                  fontWeight: 600,
                }}
              >
                {factor}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function DataQualityBanner({ flags }: { flags?: Array<Record<string, unknown>> }) {
  if (!flags?.length) return null;
  return (
    <>
      {flags.map((flag, i) => {
        if (flag.type !== "data_confidence") return null;
        const color = flag.color as string | undefined;
        const style =
          color === "red"
            ? { background: "#fef2f2", color: "#b91c1c" }
            : color === "orange"
              ? { background: "#fff7ed", color: "#c2410c" }
              : color === "yellow"
                ? { background: "#fefce8", color: "#a16207" }
                : { background: "#eff6ff", color: "#1d4ed8" };
        return (
          <div key={`dq-${i}`} style={{ ...style, borderRadius: 8, padding: 8, fontSize: 11 }}>
            Analysis confidence: {String(flag.confidence_level ?? "Unknown")} — {String(flag.message ?? "")}
          </div>
        );
      })}
    </>
  );
}

function StepDots({ step }: { step: number }) {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
      {[1, 2, 3, 4, 5].map((i) => (
        <div key={i} style={{ width: 20, height: 20, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 9, fontWeight: 600, color: "white", background: step === i ? COLORS.accent : step > i ? COLORS.good : "#cbd5e1" }}>
          {step > i ? "✓" : i}
        </div>
      ))}
    </div>
  );
}

const HISTORY_PAGE_SIZE = 5;

function AnalyticsHistoryDetailCard({
  job,
  isOpening,
  onOpenResults,
}: {
  job: AnalyticsJobListItem | null;
  isOpening: boolean;
  onOpenResults: (jobId: string) => void;
}) {
  if (!job) {
    return (
      <div style={{ ...panelStyle(), minHeight: 220, display: "flex", alignItems: "center", justifyContent: "center", color: COLORS.muted, fontSize: 11 }}>
        Select a recent analytics job to view status, progress, and result readiness.
      </div>
    );
  }

  const isCompleted = job.status === "completed";
  const isFailed = job.status === "failed";
  const isResultReady = isCompleted && !!job.result_ready;
  const progress = typeof job.progress === "number" ? Math.max(0, Math.min(100, Math.round(job.progress))) : 0;
  const phaseSummary = formatJobStatusSummary(job);
  const runningTruthfulnessNote = getRunningJobTruthfulnessNote(job);
  const runningEtaText = shouldShowRunningJobEta(job) ? formatJobSeconds(job.estimated_completion_seconds) : "";
  const friendlyFailure = getJobFailureSummary(job, "This analytics job could not be completed.");
  const isFleetWorkflow = job.workflow_kind === "fleet";
  const fleetSummary = getFleetHistorySummary(job.fleet_progress);
  const fleetBadges = formatFleetProgressBadges(job.fleet_progress);
  const fleetOutcome = isFleetWorkflow ? getFleetOutcomeSummary(job.status, job.fleet_progress, { resultReady: job.result_ready }) : null;
  const fleetOutcomeTone = fleetOutcome?.tone === "good"
    ? { border: "1px solid #bbf7d0", background: "#f0fdf4", accent: "#166534" }
    : fleetOutcome?.tone === "warn"
      ? { border: "1px solid #fde68a", background: "#fffbeb", accent: "#b45309" }
      : fleetOutcome?.tone === "bad"
        ? { border: "1px solid #fecaca", background: "#fff1f2", accent: "#b91c1c" }
        : { border: "1px solid #bfdbfe", background: "#eff6ff", accent: "#1d4ed8" };

  return (
    <div style={{ ...panelStyle(), minHeight: 220 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div>
          <div style={{ color: COLORS.muted, fontSize: 10, textTransform: "uppercase", letterSpacing: 1.3 }}>Selected Job Details</div>
          <div style={{ marginTop: 4, fontFamily: "monospace", fontSize: 11, color: COLORS.text }}>{job.job_id}</div>
        </div>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            borderRadius: 999,
            padding: "4px 10px",
            background: isCompleted ? "#dcfce7" : isFailed ? "#fee2e2" : "#e0e7ff",
            color: isCompleted ? "#166534" : isFailed ? "#b91c1c" : "#4338ca",
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          {getUserFacingJobStatusLabelWithCoverage(job)}
        </span>
      </div>

      <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
        <div style={{ borderRadius: 10, border: "1px solid #e2e8f0", background: "#f8fafc", padding: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, color: COLORS.text }}>{phaseSummary}</div>
              <div style={{ marginTop: 3, color: COLORS.muted, fontSize: 11 }}>
                {isFleetWorkflow
                  ? fleetOutcome?.summary ?? friendlyFailure
                  : isResultReady
                    ? "Results are ready to reopen from this history view."
                    : isCompleted
                      ? "Analysis completed successfully. Results are still being finalized for this history view."
                      : isFailed
                        ? friendlyFailure
                        : "Status updates come directly from the background job."}
              </div>
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, color: COLORS.text }}>{progress}%</div>
          </div>
          <div style={{ marginTop: 8, height: 10, background: "#e2e8f0", borderRadius: 999, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${progress}%`, background: isFailed ? "#ef4444" : "linear-gradient(135deg,#6366f1,#0f766e)", borderRadius: 999, transition: "width 0.4s ease" }} />
          </div>
          <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 8, color: COLORS.muted, fontSize: 10 }}>
            {isFleetWorkflow ? <span>Fleet workflow</span> : null}
            {job.phase_label ? <span>Current step: {job.phase_label}</span> : null}
            {job.status === "pending" && typeof job.queue_position === "number" ? <span>Queue position {job.queue_position + 1}</span> : null}
            {job.status === "running" && runningEtaText ? <span>ETA {runningEtaText}</span> : null}
            {job.status === "running" && !runningEtaText && runningTruthfulnessNote ? <span>{runningTruthfulnessNote}</span> : null}
            {job.result_ready ? <span>Result ready</span> : null}
            {fleetBadges.slice(0, 4).map((badge) => <span key={badge}>{badge}</span>)}
          </div>
        </div>

        {isFleetWorkflow && fleetOutcome ? (
          <div style={{ borderRadius: 8, border: fleetOutcomeTone.border, background: fleetOutcomeTone.background, padding: 10 }}>
            <div style={{ color: fleetOutcomeTone.accent, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>
              Fleet Result State
            </div>
            <div style={{ marginTop: 4, fontSize: 13, fontWeight: 700, color: COLORS.text }}>{fleetOutcome.headline}</div>
            <div style={{ marginTop: 4, fontSize: 11, color: COLORS.muted }}>{fleetOutcome.action}</div>
          </div>
        ) : null}

        {isFleetWorkflow && (fleetSummary || fleetBadges.length > 0) ? (
          <div style={{ borderRadius: 8, border: "1px solid #dbeafe", background: "#eff6ff", padding: 10 }}>
            <div style={{ color: "#1d4ed8", fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>
              Fleet Coverage Summary
            </div>
            {fleetSummary ? (
              <div style={{ marginTop: 4, fontSize: 12, fontWeight: 600, color: COLORS.text }}>{fleetSummary}</div>
            ) : null}
            <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
              {fleetBadges.map((badge) => (
                <span
                  key={badge}
                  style={{
                    borderRadius: 999,
                    background: "white",
                    border: "1px solid #bfdbfe",
                    padding: "4px 8px",
                    color: "#1e3a8a",
                    fontSize: 10,
                    fontWeight: 600,
                  }}
                >
                  {badge}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8 }}>
          <div style={{ borderRadius: 8, border: "1px solid #e2e8f0", padding: 10 }}>
            <div style={{ color: COLORS.muted, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Created</div>
            <div style={{ marginTop: 4, fontSize: 11, fontWeight: 600 }}>{formatHistoryDate(job.created_at)}</div>
          </div>
          <div style={{ borderRadius: 8, border: "1px solid #e2e8f0", padding: 10 }}>
            <div style={{ color: COLORS.muted, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Started</div>
            <div style={{ marginTop: 4, fontSize: 11, fontWeight: 600 }}>{formatHistoryDate(job.started_at)}</div>
          </div>
          <div style={{ borderRadius: 8, border: "1px solid #e2e8f0", padding: 10 }}>
            <div style={{ color: COLORS.muted, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Completed</div>
            <div style={{ marginTop: 4, fontSize: 11, fontWeight: 600 }}>{formatHistoryDate(job.completed_at)}</div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            onClick={() => onOpenResults(job.job_id)}
            disabled={!isCompleted || !job.result_ready || isOpening}
            style={{
              padding: "8px 12px",
              borderRadius: 8,
              border: "none",
              background: isResultReady ? COLORS.accent : "#cbd5e1",
              color: "white",
              fontWeight: 700,
              fontSize: 11,
              cursor: !isResultReady || isOpening ? "not-allowed" : "pointer",
              opacity: !isResultReady || isOpening ? 0.7 : 1,
            }}
          >
            {isOpening ? "Opening..." : isResultReady ? (isFleetWorkflow ? "Open Fleet Result" : "Open Results") : isCompleted ? "Results Finalizing" : "Results Not Ready"}
          </button>
        </div>

        {isFailed && job.error_message ? (
          <details style={{ borderRadius: 8, border: "1px solid #fecaca", background: "#fff1f2", padding: 10 }}>
            <summary style={{ cursor: "pointer", color: "#9f1239", fontWeight: 700, fontSize: 11 }}>Technical details</summary>
            <div style={{ marginTop: 6, color: "#881337", fontSize: 11 }}>{job.error_message}</div>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function AnalyticsPageContent() {
  const search = useSearchParams();
  const { me } = useAuth();
  const { selectedTenantId } = useTenantStore();
  const scopedOrgId = resolveScopedTenantId(me, selectedTenantId);
  const historyCacheKey = useMemo(
    () => String(scopedOrgId ?? selectedTenantId ?? "default"),
    [scopedOrgId, selectedTenantId],
  );
  const initialHistorySnapshot = useMemo(
    () => readAnalyticsHistorySnapshot(historyCacheKey),
    [historyCacheKey],
  );
  const [screen, setScreen] = useState<Screen>("wizard");
  const [step, setStep] = useState(1);

  const [devices, setDevices] = useState<Device[]>([]);
  const [plants, setPlants] = useState<PlantProfile[]>([]);
  const [models, setModels] = useState<{ anomaly_detection: string[]; failure_prediction: string[]; forecasting: string[] } | null>(null);

  const [scopeSelection, setScopeSelection] = useState<DeviceScopeSelection>({
    mode: "all",
    plantId: null,
    deviceIds: [],
  });
  const [preset, setPreset] = useState<Preset>("recommended");
  const [dateRange, setDateRange] = useState(getPresetRange("recommended"));
  const [analysisType, setAnalysisType] = useState<AnalysisType | null>(null);

  const [jobId, setJobId] = useState<string | null>(null);
  const [activeJobStatus, setActiveJobStatus] = useState<AnalyticsJobListItem | null>(null);
  const [activeWorkflowSelectedCount, setActiveWorkflowSelectedCount] = useState<number>(0);
  const [isSubmittingAnalysis, setIsSubmittingAnalysis] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<AnalyticsPreflightResponse | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [result, setResult] = useState<ResultType | null>(null);
  const [blockedResult, setBlockedResult] = useState<BlockedFormattedResult | null>(null);
  const [historyJobs, setHistoryJobs] = useState<AnalyticsJobListItem[]>(() => initialHistorySnapshot?.jobs ?? []);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [openingJobId, setOpeningJobId] = useState<string | null>(null);
  const [selectedHistoryJobId, setSelectedHistoryJobId] = useState<string | null>(
    () => initialHistorySnapshot?.selectedJobId ?? null,
  );
  const [historyPage, setHistoryPage] = useState(() => initialHistorySnapshot?.page ?? 0);
  const [hasMoreHistory, setHasMoreHistory] = useState(() => initialHistorySnapshot?.hasMore ?? false);
  const [anomalyPage, setAnomalyPage] = useState(1);
  const [fleetParentResult, setFleetParentResult] = useState<FleetFormattedResult | null>(null);
  const [fleetNotice, setFleetNotice] = useState<string | null>(null);
  const visiblePlants = useMemo(() => resolveVisiblePlants(me, plants), [me, plants]);
  const historyJobsRef = useRef(historyJobs);
  const activeJobStatusRef = useRef(activeJobStatus);
  const activeJobIdRef = useRef(jobId);
  const jobPollStartedAtRef = useRef<number | null>(null);
  const jobPollFailureCountRef = useRef(0);

  useEffect(() => {
    historyJobsRef.current = historyJobs;
  }, [historyJobs]);
  useEffect(() => {
    activeJobStatusRef.current = activeJobStatus;
  }, [activeJobStatus]);
  useEffect(() => {
    activeJobIdRef.current = jobId;
    if (jobId) {
      jobPollStartedAtRef.current = Date.now();
      jobPollFailureCountRef.current = 0;
    } else {
      jobPollStartedAtRef.current = null;
      jobPollFailureCountRef.current = 0;
    }
  }, [jobId]);
  const scopeCatalog = useMemo(
    () => buildDeviceScopeCatalog(devices, visiblePlants),
    [devices, visiblePlants],
  );
  const normalizedScopeSelection = useMemo(
    () => normalizeDeviceScopeSelection(scopeSelection, scopeCatalog),
    [scopeCatalog, scopeSelection],
  );
  const selectedDeviceIds = useMemo(
    () => resolveDeviceIdsForSelection(normalizedScopeSelection, scopeCatalog),
    [normalizedScopeSelection, scopeCatalog],
  );
  const selectedScopeSummary = useMemo(
    () => getDeviceScopeSummary(normalizedScopeSelection, scopeCatalog),
    [normalizedScopeSelection, scopeCatalog],
  );
  const selectedHistoryJob = useMemo(
    () => historyJobs.find((job) => job.job_id === selectedHistoryJobId) ?? (
      activeJobStatus?.job_id === selectedHistoryJobId ? activeJobStatus : null
    ),
    [activeJobStatus, historyJobs, selectedHistoryJobId],
  );
  const activeIsFleetWorkflow =
    (activeJobStatus?.workflow_kind === "fleet") ||
    (!activeJobStatus?.workflow_kind && activeWorkflowSelectedCount > 1);
  const activeFleetSummary = getFleetProgressSummary(activeJobStatus?.fleet_progress, activeWorkflowSelectedCount);
  const liveHistoryJobCount = useMemo(() => countLiveAnalyticsJobs(historyJobs), [historyJobs]);

  const loadHistory = useCallback(async (page = 0) => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const jobs = await listAnalyticsJobs({
        limit: HISTORY_PAGE_SIZE + 1,
        offset: page * HISTORY_PAGE_SIZE,
      });
      const visibleJobs = jobs.slice(0, HISTORY_PAGE_SIZE);
      setHistoryPage(page);
      setHasMoreHistory(jobs.length > HISTORY_PAGE_SIZE);
      setHistoryJobs(visibleJobs);
      setSelectedHistoryJobId((current) => resolveSelectedAnalyticsJobId(visibleJobs, current, activeJobIdRef.current));
      return jobs;
    } catch (e: unknown) {
      const message = getErrorMessage(e, "Failed to load analytics history");
      const hadVisibleHistory = historyJobsRef.current.length > 0;
      setHistoryError(hadVisibleHistory ? `${message} Showing last known history.` : message);
      if (!hadVisibleHistory) {
        setHasMoreHistory(false);
        setHistoryJobs([]);
      }
      return historyJobsRef.current;
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const applyActiveStatusToHistory = useCallback((status: AnalyticsJobListItem) => {
    setHistoryJobs((currentJobs) => mergeHistoryJobStatus(currentJobs, status));
  }, []);

  const refreshActiveJobStatus = useCallback(async () => {
    const currentJobId = activeJobIdRef.current;
    if (!currentJobId) {
      return;
    }

    const startedAt = jobPollStartedAtRef.current ?? Date.now();
    jobPollStartedAtRef.current = startedAt;
    const MAX_POLL_MS = 15 * 60 * 1000;
    if (Date.now() - startedAt > MAX_POLL_MS) {
      setError("Analysis is taking too long. It may still be running - check back later.");
      setStep(3);
      setJobId(null);
      return;
    }

    try {
      const status = await getAnalyticsStatus(currentJobId);
      setActiveJobStatus(status);
      applyActiveStatusToHistory(status);
      if (status.workflow_kind === "fleet" && typeof status.fleet_progress?.selected_device_count === "number") {
        setActiveWorkflowSelectedCount(status.fleet_progress.selected_device_count);
      }

      if (status.status === "completed") {
        const results = await getFormattedResults(currentJobId);
        if (isBlockedFormattedResult(results)) {
          setBlockedResult(results);
          setResult(null);
          setFleetParentResult(null);
        } else if (results.analysis_type === "fleet") {
          setBlockedResult(null);
          setResult(results);
          setFleetParentResult(results);
          setActiveWorkflowSelectedCount(
            Number(results.execution_metadata?.selected_device_count ?? results.device_summaries.length),
          );
        } else {
          setBlockedResult(null);
          setResult(results);
          setFleetParentResult(null);
        }
        setFleetNotice(null);
        setAnomalyPage(1);
        setStep(5);
        setJobId(null);
        void loadHistory(0);
        return;
      }

      if (status.status === "failed") {
        if (isNoDataOutcome(status.error_code)) {
          setError(null);
          setStep(5);
        } else {
          setError(status.error_message ?? status.message ?? "Analysis failed.");
          setStep(3);
        }
        setJobId(null);
        void loadHistory(0);
        return;
      }

      jobPollFailureCountRef.current = 0;
    } catch {
      jobPollFailureCountRef.current += 1;
      if (jobPollFailureCountRef.current >= 3) {
        setError("Lost connection. Please refresh and check if results are available.");
        setStep(3);
        setJobId(null);
      }
    }
  }, [applyActiveStatusToHistory, loadHistory]);

  useEffect(() => {
    Promise.all([
      getDevices(),
      getSupportedModels(),
      scopedOrgId ? authApi.listPlants(scopedOrgId) : Promise.resolve([]),
    ])
      .then(([devs, mods, orgPlants]) => {
        setDevices(devs);
        setModels(mods);
        setPlants(orgPlants);
        const qd = search.get("device");
        if (qd && devs.some((device) => device.id === qd)) {
          setScopeSelection({
            mode: "devices",
            plantId: null,
            deviceIds: [qd],
          });
        }
      })
      .catch((e: unknown) => setError(getErrorMessage(e, "Failed to load initial data")));
  }, [scopedOrgId, search]);

  useEffect(() => {
    const cached = readAnalyticsHistorySnapshot(historyCacheKey);
    setHistoryJobs(cached?.jobs ?? []);
    setHistoryPage(cached?.page ?? 0);
    setHasMoreHistory(cached?.hasMore ?? false);
    setSelectedHistoryJobId(cached?.selectedJobId ?? null);
    setHistoryError(null);
    void loadHistory(cached?.page ?? 0);
  }, [historyCacheKey, loadHistory]);

  useEffect(() => {
    writeAnalyticsHistorySnapshot(historyCacheKey, {
      jobs: historyJobs,
      page: historyPage,
      hasMore: hasMoreHistory,
      selectedJobId: selectedHistoryJobId,
    });
  }, [hasMoreHistory, historyCacheKey, historyJobs, historyPage, selectedHistoryJobId]);

  useEffect(() => {
    const selectionChanged =
      normalizedScopeSelection.mode !== scopeSelection.mode ||
      normalizedScopeSelection.plantId !== scopeSelection.plantId ||
      normalizedScopeSelection.deviceIds.length !== scopeSelection.deviceIds.length ||
      normalizedScopeSelection.deviceIds.some((deviceId, index) => deviceId !== scopeSelection.deviceIds[index]);
    if (selectionChanged) {
      setScopeSelection(normalizedScopeSelection);
    }
  }, [normalizedScopeSelection, scopeSelection]);

  const runDays = useMemo(() => {
    const start = new Date(dateRange.start).getTime();
    const end = new Date(dateRange.end).getTime();
    return Math.max(1, Math.round((end - start) / 86400000));
  }, [dateRange]);
  const isAnalyticsRangeValid = useMemo(
    () => getAnalyticsRangeSpanDays(dateRange.start, dateRange.end) <= ANALYTICS_MAX_RANGE_DAYS,
    [dateRange.end, dateRange.start],
  );

  useEffect(() => {
    if (step !== 3 || selectedDeviceIds.length === 0) {
      setPreflight(null);
      setPreflightLoading(false);
      return;
    }

    const startIso = `${dateRange.start}T00:00:00Z`;
    const endIso = `${dateRange.end}T23:59:59Z`;
    let cancelled = false;

    setPreflightLoading(true);
    void preflightAnalytics({
      device_ids: selectedDeviceIds,
      start_time: startIso,
      end_time: endIso,
    })
      .then((response) => {
        if (cancelled) return;
        setPreflight(response);
      })
      .catch(() => {
        if (cancelled) return;
        setPreflight(null);
      })
      .finally(() => {
        if (cancelled) return;
        setPreflightLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [dateRange.end, dateRange.start, selectedDeviceIds, step]);

  useEffect(() => {
    const pollMs = getAnalyticsStatusPollMs(activeJobStatus, typeof document !== "undefined" ? document.hidden : false);
    if (!jobId || pollMs === null) {
      return;
    }

    let cancelled = false;
    let timer: number | null = null;

    const schedule = () => {
      if (cancelled) {
        return;
      }
      const nextDelay = getAnalyticsStatusPollMs(
        activeJobStatusRef.current,
        document.hidden,
      );
      if (nextDelay === null) {
        return;
      }
      timer = window.setTimeout(async () => {
        await refreshActiveJobStatus();
        schedule();
      }, nextDelay);
    };

    schedule();

    const onVisibilityChange = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
      }
      schedule();
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
  }, [activeJobStatus, jobId, refreshActiveJobStatus]);

  const submit = useCallback(async () => {
    if (!analysisType || !models || isSubmittingAnalysis) return;
    if (!isAnalyticsRangeValid) {
      setError(`Analytics supports up to ${ANALYTICS_MAX_RANGE_DAYS} days per run.`);
      setStep(2);
      return;
    }
    if (preflight?.guaranteed_no_data) {
      setError(preflight.message);
      return;
    }

    setIsSubmittingAnalysis(true);
    setError(null);
    setStep(4);
    setActiveJobStatus(null);
    setActiveWorkflowSelectedCount(selectedDeviceIds.length);

    try {
      const modelName = analysisType === "anomaly"
        ? (models.anomaly_detection[0] ?? "isolation_forest")
        : (models.failure_prediction[0] ?? "random_forest");

      if (selectedDeviceIds.length !== 1) {
        const resp = await runFleetAnalytics({
          device_ids: selectedDeviceIds,
          analysis_type: analysisType === "anomaly" ? "anomaly" : "prediction",
          model_name: modelName,
          start_time: `${dateRange.start}T00:00:00Z`,
          end_time: `${dateRange.end}T23:59:59Z`,
          parameters: { sensitivity: "medium", lookback_days: runDays },
        });
        setActiveJobStatus({
          job_id: resp.job_id,
          status: resp.status,
          workflow_kind: "fleet",
          progress: 0,
          message: resp.message,
          result_ready: resp.result_ready,
          artifact_ready: resp.artifact_ready,
          download_ready: resp.download_ready,
          result_url: resp.result_url,
          download_url: resp.download_url,
        });
        setSelectedHistoryJobId(resp.job_id);
        setJobId(resp.job_id);
        return;
      }

      const resp = await runAnalytics({
        device_id: selectedDeviceIds[0],
        analysis_type: analysisType === "anomaly" ? "anomaly" : "prediction",
        model_name: modelName,
        start_time: `${dateRange.start}T00:00:00Z`,
        end_time: `${dateRange.end}T23:59:59Z`,
        parameters: { sensitivity: "medium", lookback_days: runDays },
      });
      setActiveJobStatus({
        job_id: resp.job_id,
        status: resp.status,
        workflow_kind: "single",
        progress: 0,
        message: resp.message,
        result_ready: resp.result_ready,
        artifact_ready: resp.artifact_ready,
        download_ready: resp.download_ready,
        result_url: resp.result_url,
        download_url: resp.download_url,
      });
      setSelectedHistoryJobId(resp.job_id);
      setJobId(resp.job_id);
    } catch (err: unknown) {
      const message = getLongRunningJobErrorMessage(err, "An unexpected error occurred");
      setError(message);
      setStep(3);
    } finally {
      setIsSubmittingAnalysis(false);
    }
  }, [analysisType, models, selectedDeviceIds, dateRange, runDays, isSubmittingAnalysis, preflight, isAnalyticsRangeValid]);

  const reset = () => {
    setScreen("wizard");
    setStep(1);
    setResult(null);
    setFleetParentResult(null);
    setFleetNotice(null);
    setAnomalyPage(1);
    setJobId(null);
    setActiveJobStatus(null);
    setActiveWorkflowSelectedCount(0);
    setIsSubmittingAnalysis(false);
    setError(null);
    setPreflight(null);
    setPreflightLoading(false);
    setBlockedResult(null);
    setScopeSelection({
      mode: "all",
      plantId: null,
      deviceIds: [],
    });
  };

  const openStoredJob = useCallback(async (selectedJobId: string) => {
    setOpeningJobId(selectedJobId);
    setHistoryError(null);
    try {
      const storedResult = await getFormattedResults(selectedJobId);
      const selectedHistoryJobStatus = historyJobsRef.current.find((job) => job.job_id === selectedJobId) ?? null;
      setJobId(selectedJobId);
      setActiveJobStatus(selectedHistoryJobStatus);
      setActiveWorkflowSelectedCount(1);
      setAnomalyPage(1);
      setFleetNotice(null);
      if (isBlockedFormattedResult(storedResult)) {
        setBlockedResult(storedResult);
        setResult(null);
        setFleetParentResult(null);
        setScreen("blocked");
      } else if (storedResult.analysis_type === "fleet") {
        setBlockedResult(null);
        setResult(storedResult);
        setFleetParentResult(storedResult);
        setActiveWorkflowSelectedCount(
          Number(storedResult.execution_metadata?.selected_device_count ?? storedResult.device_summaries.length),
        );
        setScreen("fleet");
      } else if (storedResult.analysis_type === "anomaly_detection") {
        setBlockedResult(null);
        setResult(storedResult);
        setFleetParentResult(null);
        setScreen("anomaly");
      } else if (storedResult.analysis_type === "failure_prediction") {
        setBlockedResult(null);
        setResult(storedResult);
        setFleetParentResult(null);
        setScreen("failure");
      } else {
        setBlockedResult(null);
        setResult(null);
        setFleetParentResult(null);
        setScreen("blocked");
      }
      setStep(5);
    } catch (e: unknown) {
      setHistoryError(getErrorMessage(e, "Unable to open stored analytics result"));
    } finally {
      setOpeningJobId(null);
    }
  }, []);

  const showHistoryPanel = step === 1 || step === 4;

  useEffect(() => {
    const refreshMs = getAnalyticsHistoryRefreshMs({
      liveHistoryJobCount,
      activeJobStatus,
      isDocumentHidden: typeof document !== "undefined" ? document.hidden : false,
    });
    if (!showHistoryPanel || refreshMs === null) {
      return;
    }

    let cancelled = false;
    let timer: number | null = null;

    const schedule = () => {
      if (cancelled) {
        return;
      }
      const nextDelay = getAnalyticsHistoryRefreshMs({
        liveHistoryJobCount: countLiveAnalyticsJobs(historyJobsRef.current),
        activeJobStatus: activeJobStatusRef.current,
        isDocumentHidden: document.hidden,
      });
      if (nextDelay === null) {
        return;
      }
      timer = window.setTimeout(async () => {
        await loadHistory(historyPage);
        schedule();
      }, nextDelay);
    };

    schedule();

    const onVisibilityChange = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
      }
      schedule();
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
  }, [activeJobStatus, historyPage, jobId, liveHistoryJobCount, loadHistory, showHistoryPanel]);

  const historyRefreshingInBackground = historyLoading && historyJobs.length > 0;

  const goDashboard = () => {
    if (blockedResult) {
      setScreen("blocked");
      return;
    }
    if (!result) return;
    if (result.analysis_type === "anomaly_detection") setScreen("anomaly");
    else if (result.analysis_type === "failure_prediction") setScreen("failure");
    else {
      setFleetParentResult(result);
      setFleetNotice(null);
      setScreen("fleet");
    }
  };

  const backToFleetSummary = () => {
    if (!fleetParentResult) return;
    setResult(fleetParentResult);
    setFleetNotice(null);
    setScreen("fleet");
  };

  const openFleetDevice = async (deviceId: string, childJobId?: string) => {
    setFleetNotice(null);
    if (!childJobId) {
      setFleetNotice(`Detailed results are not available yet for ${deviceId}.`);
      return;
    }
    try {
      const childResult = await getFormattedResults(childJobId);
      if (isBlockedFormattedResult(childResult)) {
        setBlockedResult(childResult);
        setResult(null);
        setScreen("blocked");
        return;
      }
      if (childResult.analysis_type === "anomaly_detection") {
        setBlockedResult(null);
        setResult(childResult);
        setAnomalyPage(1);
        setScreen("anomaly");
        return;
      }
      if (childResult.analysis_type === "failure_prediction") {
        setBlockedResult(null);
        setResult(childResult);
        setScreen("failure");
        return;
      }
      setFleetNotice(`Unsupported drilldown result for ${deviceId}.`);
    } catch (err: unknown) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : `Unable to open detailed view for ${deviceId}.`;
      setFleetNotice(message);
    }
  };

  if (screen === "wizard") {
    return (
      <div style={{ minHeight: "100vh", width: "100%", overflowX: "hidden", background: COLORS.bg, color: COLORS.text, fontFamily: "'DM Sans','Segoe UI',sans-serif" }}>
        <div style={{ maxWidth: 700, margin: "0 auto", padding: "16px 16px 24px", boxSizing: "border-box" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
            <div>
              <div style={{ color: COLORS.muted, fontSize: 10, letterSpacing: 1.5, textTransform: "uppercase" }}>Analytics</div>
              <h1 style={{ margin: "4px 0 0", fontSize: 18, color: COLORS.text }}>Run AI-powered analytics on your machine data</h1>
            </div>
            <StepDots step={step} />
          </div>

          <div style={{ background: COLORS.panel, border: `1px solid ${COLORS.panelBorder}`, borderRadius: 10, padding: 14, boxShadow: "0 1px 3px rgba(0,0,0,0.1)" }}>
            {step === 1 && (
              <>
                <h2 style={{ marginTop: 0, marginBottom: 6, fontSize: 14, color: COLORS.text }}>Select Scope</h2>
                <div style={{ color: COLORS.muted, marginBottom: 10, fontSize: 12 }}>Which machines to analyse?</div>

                <DeviceScopeSelector
                  catalog={scopeCatalog}
                  value={normalizedScopeSelection}
                  onChange={setScopeSelection}
                />
                <div style={{ color: COLORS.muted, marginTop: 10, fontSize: 11 }}>{selectedScopeSummary}</div>

                <button
                  onClick={() => setStep(2)}
                  disabled={selectedDeviceIds.length === 0}
                  style={{
                    marginTop: 12,
                    width: "100%",
                    padding: 8,
                    borderRadius: 8,
                    border: "none",
                    background: COLORS.accent,
                    color: "white",
                    fontWeight: 600,
                    fontSize: 13,
                    cursor: selectedDeviceIds.length === 0 ? "not-allowed" : "pointer",
                    opacity: selectedDeviceIds.length === 0 ? 0.55 : 1,
                  }}
                >
                  Continue
                </button>
              </>
            )}

            {step === 2 && (
              <>
                <h2 style={{ marginTop: 0, marginBottom: 6, fontSize: 14, color: COLORS.text }}>Select Date Range</h2>
                <div style={{ color: COLORS.muted, marginBottom: 10, fontSize: 12 }}>How much telemetry data?</div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 10 }}>
                  {(["quick", "recommended", "deep", "custom"] as Preset[]).map((p) => {
                    const range = p === "custom" ? dateRange : getPresetRange(p);
                    return (
                      <button
                        key={p}
                        onClick={() => {
                          setPreset(p);
                          if (p !== "custom") setDateRange(range);
                        }}
                        style={{ textAlign: "left", background: preset === p ? "rgba(99,102,241,0.1)" : "#f1f5f9", color: COLORS.text, border: `1px solid ${preset === p ? "#6366f1" : COLORS.panelBorder}`, borderRadius: 8, padding: 8, cursor: "pointer" }}
                      >
                        <div style={{ color: COLORS.accent, letterSpacing: 1, textTransform: "uppercase", fontSize: 9, fontWeight: 600 }}>{p}</div>
                        <div style={{ marginTop: 3, fontSize: 12, fontWeight: 600 }}>{PRESET_LABELS[p]}</div>
                        <div style={{ color: COLORS.muted, marginTop: 2, fontSize: 10 }}>{range.start} → {range.end}</div>
                      </button>
                    );
                  })}
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 10, color: COLORS.muted, letterSpacing: 1 }}>FROM</div>
                    <input type="date" value={dateRange.start} onChange={(e) => { setPreset("custom"); setDateRange((r) => ({ ...r, start: e.target.value })); }} style={{ marginTop: 4, width: "100%", padding: 6, borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, fontSize: 11 }} />
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: COLORS.muted, letterSpacing: 1 }}>TO</div>
                    <input type="date" value={dateRange.end} onChange={(e) => { setPreset("custom"); setDateRange((r) => ({ ...r, end: e.target.value })); }} style={{ marginTop: 4, width: "100%", padding: 6, borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, fontSize: 11 }} />
                  </div>
                </div>
                {!isAnalyticsRangeValid ? (
                  <div style={{ marginTop: 8, color: COLORS.bad, fontSize: 11, fontWeight: 600 }}>
                    Analytics supports up to {ANALYTICS_MAX_RANGE_DAYS} days per run.
                  </div>
                ) : null}

                <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                  <button onClick={() => setStep(1)} style={{ padding: "8px 12px", borderRadius: 8, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, fontWeight: 600, fontSize: 12, cursor: "pointer" }}>Back</button>
                  <button
                    onClick={() => setStep(3)}
                    disabled={!isAnalyticsRangeValid}
                    style={{
                      flex: 1,
                      padding: "8px 12px",
                      borderRadius: 8,
                      border: "none",
                      background: isAnalyticsRangeValid ? COLORS.accent : "#cbd5e1",
                      color: "white",
                      fontWeight: 600,
                      fontSize: 12,
                      cursor: isAnalyticsRangeValid ? "pointer" : "not-allowed",
                    }}
                  >
                    Continue
                  </button>
                </div>
              </>
            )}

            {step === 3 && (
              <>
                <h2 style={{ marginTop: 0, marginBottom: 6, fontSize: 14, color: COLORS.text }}>Analysis Type</h2>
                <div style={{ color: COLORS.muted, marginBottom: 10, fontSize: 12 }}>What to discover?</div>

                <button onClick={() => setAnalysisType("anomaly")} style={{ width: "100%", textAlign: "left", background: analysisType === "anomaly" ? "rgba(99,102,241,0.1)" : "#f1f5f9", color: COLORS.text, border: `1px solid ${analysisType === "anomaly" ? "#6366f1" : COLORS.panelBorder}`, borderRadius: 8, padding: 10, marginBottom: 6, cursor: "pointer" }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>Anomaly Detection</div>
                  <div style={{ color: COLORS.muted, marginTop: 2, fontSize: 11 }}>Find unusual patterns, spikes, drops.</div>
                </button>

                <button onClick={() => setAnalysisType("failure_prediction")} style={{ width: "100%", textAlign: "left", background: analysisType === "failure_prediction" ? "rgba(99,102,241,0.1)" : "#f1f5f9", color: COLORS.text, border: `1px solid ${analysisType === "failure_prediction" ? "#6366f1" : COLORS.panelBorder}`, borderRadius: 8, padding: 10, cursor: "pointer" }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>Risk Assessment</div>
                  <div style={{ color: COLORS.muted, marginTop: 2, fontSize: 11 }}>Assess risk indicators and condition trends.</div>
                </button>

                {preflightLoading ? (
                  <div style={{ marginTop: 8, color: COLORS.muted, fontSize: 11 }}>
                    Checking whether the selected range has telemetry...
                  </div>
                ) : preflight ? (
                  <div
                    style={{
                      marginTop: 8,
                      borderRadius: 8,
                      padding: 8,
                      fontSize: 11,
                      background: preflight.guaranteed_no_data
                        ? "#fff1f2"
                        : preflight.devices_without_telemetry > 0
                          ? "#fffbeb"
                          : "#eff6ff",
                      color: preflight.guaranteed_no_data
                        ? "#9f1239"
                        : preflight.devices_without_telemetry > 0
                          ? "#b45309"
                          : "#1d4ed8",
                    }}
                  >
                    {preflight.message}
                    {preflight.devices_without_telemetry > 0 && selectedDeviceIds.length > 1
                      ? " Devices without telemetry will be skipped if you continue."
                      : ""}
                  </div>
                ) : null}

                {error && <div style={{ marginTop: 8, color: COLORS.bad, fontSize: 11 }}>{error}</div>}

                <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                  <button onClick={() => setStep(2)} style={{ padding: "8px 12px", borderRadius: 8, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, fontWeight: 600, fontSize: 12, cursor: "pointer" }}>Back</button>
                  <button onClick={submit} disabled={!analysisType || isSubmittingAnalysis || preflightLoading || Boolean(preflight?.guaranteed_no_data)} style={{ flex: 1, padding: "8px 12px", borderRadius: 8, border: "none", background: COLORS.accent, color: "white", fontWeight: 600, fontSize: 12, cursor: !analysisType || isSubmittingAnalysis || preflightLoading || Boolean(preflight?.guaranteed_no_data) ? "not-allowed" : "pointer", opacity: analysisType && !isSubmittingAnalysis && !preflightLoading && !preflight?.guaranteed_no_data ? 1 : 0.55 }}>{isSubmittingAnalysis ? "Submitting..." : "Run Analysis"}</button>
                </div>
              </>
            )}

            {step === 4 && (
              <div style={{ display: "grid", gap: 12 }}>
                <AsyncJobHandoffCard
                  title={activeIsFleetWorkflow ? "Fleet analysis started" : "Analysis started"}
                  backgroundMessage={activeIsFleetWorkflow ? "Fleet processing continues in the background." : "Processing continues in the background."}
                  historyLabel="Analysis History"
                  historyHref="/analytics"
                  summary={
                    activeIsFleetWorkflow
                      ? [
                          `${activeWorkflowSelectedCount} devices selected`,
                          getFleetAcceptedMessage(activeWorkflowSelectedCount),
                          activeFleetSummary,
                        ]
                          .filter(Boolean)
                          .join(" ")
                      : `Selected scope: ${selectedScopeSummary}. Date range: ${dateRange.start} → ${dateRange.end}.`
                  }
                  status={activeJobStatus}
                  statusBadges={activeIsFleetWorkflow ? formatFleetProgressBadges(activeJobStatus?.fleet_progress) : undefined}
                  footerMessage={
                    activeIsFleetWorkflow
                      ? "You can continue using the platform and return to Analysis History at any time to see which devices are queued, running, completed, failed, or skipped."
                      : undefined
                  }
                />

                <div style={{ color: COLORS.muted, fontSize: 10 }}>
                  {selectedScopeSummary} · {dateRange.start} → {dateRange.end}
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <button
                    onClick={() => setStep(1)}
                    style={{ padding: "8px 12px", borderRadius: 8, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, fontWeight: 600, fontSize: 12, cursor: "pointer" }}
                  >
                    Track in History
                  </button>
                  <button
                    onClick={reset}
                    style={{ padding: "8px 12px", borderRadius: 8, border: "none", background: COLORS.accent, color: "white", fontWeight: 600, fontSize: 12, cursor: "pointer" }}
                  >
                    Configure Another Analysis
                  </button>
                </div>

                <div style={{ color: COLORS.muted, fontSize: 11 }}>
                  {activeJobStatus?.result_ready
                    ? activeIsFleetWorkflow
                      ? "Fleet results are ready. Open them from Analysis History below."
                      : "Results are ready. You can open them from Analysis History below."
                    : activeIsFleetWorkflow
                      ? "This fleet analysis may move through queueing, running, and completion in stages. Track it in Analysis History below."
                      : "Track progress in Analysis History below. You do not need to stay on this screen."}
                </div>
              </div>
            )}

            {step === 5 && (
              <div style={{ textAlign: "center", padding: "14px 0 6px" }}>
                <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6, color: COLORS.text }}>
                  {isNoDataOutcome(activeJobStatus?.error_code) ? "No telemetry in selected range" : "Analysis Complete"}
                </div>
                <div style={{ color: COLORS.muted, marginBottom: 12, fontSize: 11 }}>
                  {isNoDataOutcome(activeJobStatus?.error_code)
                    ? "This analysis finished cleanly, but there was no telemetry available for the selected device or date range."
                    : blockedResult
                      ? blockedResult.summary
                    : result?.analysis_type === "anomaly_detection"
                      ? `${result.summary.total_anomalies} anomalies · ${result.summary.health_impact} impact`
                      : result?.analysis_type === "failure_prediction"
                        ? `${result.summary.failure_probability_pct.toFixed(1)}% failure probability · ${result.summary.failure_risk} risk`
                        : result?.analysis_type === "fleet"
                          ? `${result.execution_metadata?.selected_device_count ?? result.device_summaries.length} devices selected · ${result.device_summaries.length} completed with results`
                          : null}
                </div>
                {!isNoDataOutcome(activeJobStatus?.error_code) ? (
                  <button onClick={goDashboard} style={{ width: "100%", padding: 8, borderRadius: 8, border: "none", background: COLORS.accent, color: "white", fontWeight: 600, fontSize: 13, cursor: "pointer", marginBottom: 6 }}>View Dashboard</button>
                ) : null}
                <button onClick={reset} style={{ width: "100%", padding: 8, borderRadius: 8, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, fontWeight: 600, fontSize: 13, cursor: "pointer" }}>Run Another Analysis</button>
              </div>
            )}
          </div>

          {showHistoryPanel && (
            <div style={{ ...panelStyle(), marginTop: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                <div>
                  <h2 style={{ margin: 0, fontSize: 14, color: COLORS.text }}>Analysis History</h2>
                  <div style={{ color: COLORS.muted, fontSize: 11, marginTop: 2 }}>
                    Track recent analytics jobs and reopen completed results when they are ready
                  </div>
                  {historyRefreshingInBackground ? (
                    <div style={{ color: "#1d4ed8", fontSize: 10, marginTop: 4 }}>
                      Showing last known history while refreshing in the background
                    </div>
                  ) : null}
                  {historyJobs.some((job) => job.status === "pending" || job.status === "running") ? (
                    <div style={{ color: "#4338ca", fontSize: 10, marginTop: 4 }}>
                      Auto-refreshing while jobs are still running
                    </div>
                  ) : null}
                </div>
                <button
                  onClick={() => void loadHistory(historyPage)}
                  disabled={historyLoading}
                  style={{
                    padding: "6px 10px",
                    borderRadius: 8,
                    border: `1px solid ${COLORS.panelBorder}`,
                    background: "white",
                    color: COLORS.text,
                    fontWeight: 600,
                    fontSize: 11,
                    cursor: historyLoading ? "not-allowed" : "pointer",
                    opacity: historyLoading ? 0.65 : 1,
                  }}
                >
                  Refresh
                </button>
              </div>

              {historyError && (
                <div style={{ color: COLORS.bad, fontSize: 11, marginBottom: 8 }}>{historyError}</div>
              )}

              {historyLoading && historyJobs.length === 0 ? (
                <div style={{ color: COLORS.muted, fontSize: 11 }}>Loading analytics history...</div>
              ) : historyJobs.length === 0 ? (
                <div style={{ color: COLORS.muted, fontSize: 11 }}>No analytics jobs found yet.</div>
              ) : (
                <>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                      <thead>
                        <tr style={{ textAlign: "left", color: COLORS.muted }}>
                          <th style={{ padding: "8px 6px" }}>Job ID</th>
                          <th style={{ padding: "8px 6px" }}>Status</th>
                          <th style={{ padding: "8px 6px" }}>Progress</th>
                          <th style={{ padding: "8px 6px" }}>Created</th>
                          <th style={{ padding: "8px 6px" }}>Completed</th>
                          <th style={{ padding: "8px 6px" }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {historyJobs.map((job) => {
                          const isCompleted = job.status === "completed";
                          const isOpening = openingJobId === job.job_id;
                          const isSelected = selectedHistoryJob?.job_id === job.job_id;
                          const isFleetWorkflow = job.workflow_kind === "fleet";
                          return (
                            <tr
                              key={job.job_id}
                              onClick={() => setSelectedHistoryJobId(job.job_id)}
                              style={{
                                borderTop: "1px solid #e2e8f0",
                                background: isSelected ? "#f8fafc" : "transparent",
                                cursor: "pointer",
                              }}
                            >
                              <td style={{ padding: "8px 6px", fontFamily: "monospace", fontSize: 10 }}>{job.job_id}</td>
                              <td style={{ padding: "8px 6px" }}>
                                <div style={{ display: "grid", gap: 4 }}>
                                <span
                                  style={{
                                    display: "inline-flex",
                                    alignItems: "center",
                                    borderRadius: 999,
                                    padding: "2px 8px",
                                    background:
                                      job.status === "completed"
                                        ? "#dcfce7"
                                        : job.status === "failed"
                                          ? "#fee2e2"
                                          : "#e0e7ff",
                                    color:
                                      job.status === "completed"
                                        ? "#166534"
                                        : job.status === "failed"
                                          ? "#b91c1c"
                                          : "#4338ca",
                                  }}
                                >
                                  {getUserFacingJobStatusLabelWithCoverage(job)}
                                </span>
                                {isFleetWorkflow ? (
                                  <span style={{ color: COLORS.muted, fontSize: 10 }}>Fleet workflow</span>
                                ) : null}
                                </div>
                              </td>
                              <td style={{ padding: "8px 6px", color: COLORS.muted }}>
                                <div style={{ fontWeight: 600, color: COLORS.text }}>
                                  {typeof job.progress === "number" ? `${Math.round(job.progress)}%` : "—"}
                                </div>
                                <div style={{ marginTop: 2, fontSize: 10 }}>{formatHistoryStatusDetail(job)}</div>
                              </td>
                              <td style={{ padding: "8px 6px", color: COLORS.muted }}>{formatHistoryDate(job.created_at)}</td>
                              <td style={{ padding: "8px 6px", color: COLORS.muted }}>{formatHistoryDate(job.completed_at)}</td>
                              <td style={{ padding: "8px 6px" }}>
                                <button
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setSelectedHistoryJobId(job.job_id);
                                    if (isCompleted && job.result_ready) {
                                      void openStoredJob(job.job_id);
                                    }
                                  }}
                                  style={{
                                    padding: "4px 8px",
                                    borderRadius: 6,
                                    border: "none",
                                    background: isCompleted && job.result_ready ? COLORS.accent : "#e2e8f0",
                                    color: isCompleted && job.result_ready ? "white" : COLORS.text,
                                    fontWeight: 600,
                                    fontSize: 10,
                                    cursor: isCompleted && job.result_ready && !isOpening ? "pointer" : "pointer",
                                    opacity: isOpening ? 0.7 : 1,
                                  }}
                                >
                                  {isOpening
                                    ? "Opening..."
                                    : isCompleted && job.result_ready
                                      ? isFleetWorkflow
                                        ? "Open Fleet Result"
                                        : "View Results"
                                      : isFleetWorkflow
                                        ? "View Fleet Status"
                                        : "View Details"}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                    <div style={{ color: COLORS.muted, fontSize: 11 }}>
                      Page {historyPage + 1}
                    </div>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        onClick={() => void loadHistory(Math.max(0, historyPage - 1))}
                        disabled={historyLoading || historyPage === 0}
                        style={{
                          padding: "6px 10px",
                          borderRadius: 8,
                          border: `1px solid ${COLORS.panelBorder}`,
                          background: "white",
                          color: COLORS.text,
                          fontWeight: 600,
                          fontSize: 11,
                          cursor: historyLoading || historyPage === 0 ? "not-allowed" : "pointer",
                          opacity: historyLoading || historyPage === 0 ? 0.65 : 1,
                        }}
                      >
                        Previous
                      </button>
                      <button
                        onClick={() => void loadHistory(historyPage + 1)}
                        disabled={historyLoading || !hasMoreHistory}
                        style={{
                          padding: "6px 10px",
                          borderRadius: 8,
                          border: `1px solid ${COLORS.panelBorder}`,
                          background: "white",
                          color: COLORS.text,
                          fontWeight: 600,
                          fontSize: 11,
                          cursor: historyLoading || !hasMoreHistory ? "not-allowed" : "pointer",
                          opacity: historyLoading || !hasMoreHistory ? 0.65 : 1,
                        }}
                      >
                        Next
                      </button>
                    </div>
                  </div>

                  <div style={{ marginTop: 12 }}>
                    <div style={{ color: COLORS.text, fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
                      Selected Job Details
                    </div>
                    <AnalyticsHistoryDetailCard
                      job={selectedHistoryJob}
                      isOpening={openingJobId === selectedHistoryJob?.job_id}
                      onOpenResults={(selectedJobId) => void openStoredJob(selectedJobId)}
                    />
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (screen === "anomaly" && result && result.analysis_type === "anomaly_detection") {
    const maxParam = Math.max(...result.parameter_breakdown.map((p) => p.anomaly_count), 1);
    const confidence = result.confidence;
    const anomalyRate = result.summary.anomaly_rate_pct;
    const anomalyPages = Math.max(1, Math.ceil(result.anomaly_list.length / 10));
    const pageStart = (anomalyPage - 1) * 10;
    const anomalyRows = result.anomaly_list.slice(pageStart, pageStart + 10);
    return (
      <div style={{ minHeight: "100vh", background: COLORS.bg, color: COLORS.text, fontFamily: "'DM Sans','Segoe UI',sans-serif", padding: 12 }}>
        <div style={{ maxWidth: 900, margin: "0 auto", display: "grid", gap: 8 }}>
          <div style={{ display: "flex", gap: 8, justifySelf: "start" }}>
            <button onClick={reset} style={{ padding: "5px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: "pointer", fontSize: 11 }}>New Analysis</button>
            {fleetParentResult && (
              <button onClick={backToFleetSummary} style={{ padding: "5px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: "pointer", fontSize: 11 }}>Back to Fleet Summary</button>
            )}
          </div>
          {confidence && (
            <div style={{ background: COLORS.panel, border: `1px solid ${confidence.badge_color}`, borderRadius: 8, padding: 8, color: confidence.badge_color, fontWeight: 600, fontSize: 12 }}>
              {confidence.banner_text}
            </div>
          )}
          <DataQualityBanner flags={result.data_quality_flags} />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: 8 }}>
            <Stat label="Total Anomalies" value={String(result.summary.total_anomalies)} />
            <Stat label="Anomaly Rate" value={`${result.summary.anomaly_rate_pct}%`} />
            <Stat label="Anomaly Score" value={`${result.summary.anomaly_score}/100`} />
            <Stat label="Health Impact" value={result.summary.health_impact} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div style={panelStyle()}>
              <h3 style={titleStyle()}>Anomaly Rate Gauge</h3>
              <RadialGauge
                value={anomalyRate}
                min={0}
                max={10}
                color={anomalyRate < 3 ? COLORS.good : anomalyRate < 7 ? COLORS.warn : COLORS.bad}
                label={`${anomalyRate.toFixed(2)}%`}
                subtitle="0-3% normal · 3-7% watch · >7% critical"
              />
            </div>
            <div style={panelStyle()}>
              <h3 style={titleStyle()}>Period Summary</h3>
              <div style={{ color: COLORS.muted, fontSize: 11, marginBottom: 6 }}>
                Most affected: <b style={{ color: COLORS.text }}>{result.summary.most_affected_parameter}</b>
              </div>
              <div style={{ color: COLORS.muted, fontSize: 11 }}>
                Data points: <b style={{ color: COLORS.text }}>{result.summary.data_points_analyzed}</b>
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 8 }}>
            <div style={panelStyle()}>
              <h3 style={titleStyle()}>Anomalies Over Time</h3>
              {result.anomalies_over_time.map((d) => (
                <div key={d.date} style={{ marginBottom: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: COLORS.muted }}><span>{d.date}</span><span>{d.count}</span></div>
                  <div style={{ height: 8, background: "#e2e8f0", borderRadius: 6, overflow: "hidden", display: "flex" }}>
                    <div style={{ width: `${d.count ? (d.high_count / d.count) * 100 : 0}%`, background: COLORS.bad }} />
                    <div style={{ width: `${d.count ? (d.medium_count / d.count) * 100 : 0}%`, background: COLORS.warn }} />
                    <div style={{ width: `${d.count ? (d.low_count / d.count) * 100 : 0}%`, background: COLORS.good }} />
                  </div>
                </div>
              ))}
            </div>
            <div style={panelStyle()}>
              <h3 style={titleStyle()}>Affected Parameters</h3>
              {result.parameter_breakdown.map((p) => (
                <div key={p.parameter} style={{ marginBottom: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginBottom: 2 }}><span>{p.parameter}</span><b>{p.anomaly_count}</b></div>
                  <div style={{ height: 8, background: "#e2e8f0", borderRadius: 6 }}>
                    <div style={{ height: "100%", width: `${(p.anomaly_count / maxParam) * 100}%`, background: "#3b82f6", borderRadius: 6 }} />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={panelStyle()}>
            <h3 style={titleStyle()}>Anomaly Detail List</h3>
            {anomalyRows.map((a, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "70px 1fr 140px", gap: 8, padding: "6px 0", borderBottom: "1px solid #e2e8f0", fontSize: 11 }}>
                <span style={{ color: a.severity === "high" ? COLORS.bad : a.severity === "medium" ? COLORS.warn : COLORS.good, fontWeight: 600 }}>{a.severity.toUpperCase()}</span>
                <span>{a.context}</span>
                <span style={{ color: COLORS.muted }}>{a.recommended_action}</span>
              </div>
            ))}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
              <span style={{ color: COLORS.muted, fontSize: 10 }}>
                Showing {result.anomaly_list.length === 0 ? 0 : pageStart + 1}-{Math.min(pageStart + 10, result.anomaly_list.length)} of {result.anomaly_list.length}
              </span>
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  onClick={() => setAnomalyPage((p) => Math.max(1, p - 1))}
                  disabled={anomalyPage <= 1}
                  style={{ padding: "4px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: anomalyPage <= 1 ? "not-allowed" : "pointer", opacity: anomalyPage <= 1 ? 0.5 : 1, fontSize: 10 }}
                >
                  Prev
                </button>
                <span style={{ color: COLORS.muted, fontSize: 10, alignSelf: "center" }}>Page {anomalyPage}/{anomalyPages}</span>
                <button
                  onClick={() => setAnomalyPage((p) => Math.min(anomalyPages, p + 1))}
                  disabled={anomalyPage >= anomalyPages}
                  style={{ padding: "4px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: anomalyPage >= anomalyPages ? "not-allowed" : "pointer", opacity: anomalyPage >= anomalyPages ? 0.5 : 1, fontSize: 10 }}
                >
                  Next
                </button>
              </div>
            </div>
          </div>

          <div style={panelStyle()}>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", fontSize: 11 }}>
            <div style={{ color: COLORS.muted }}>
              Type: <b style={{ color: COLORS.text }}>{formatAnalysisLabel(result)}</b>
            </div>
            <div style={{ color: COLORS.muted }}>
              Days Analysed: <b style={{ color: COLORS.text }}>{formatDaysAnalysed(result.summary.days_analyzed)}</b>
            </div>
            <div style={{ color: COLORS.good }}>
                Completion: <b>100%</b>
              </div>
            </div>
          </div>

          <ConfidenceSummaryPanel result={result} />

          {result.reasoning && (
            <div style={{ ...panelStyle(), background: "#f8fafc" }}>
              <h3 style={titleStyle()}>Why is this flagged?</h3>
              <div style={{ fontSize: 11, fontWeight: 600 }}>{result.reasoning.summary ?? "No summary available."}</div>
              {result.reasoning.affected_parameters?.length ? (
                <div style={{ marginTop: 6, fontSize: 11, color: COLORS.muted }}>
                  Affected parameters: {result.reasoning.affected_parameters.join(", ")}
                </div>
              ) : null}
              {result.reasoning.recommended_action ? (
                <div style={{ marginTop: 6, fontSize: 11 }}>→ {result.reasoning.recommended_action}</div>
              ) : null}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (screen === "failure" && result && result.analysis_type === "failure_prediction") {
    const confidence = result.confidence;
    const failurePct = result.summary.failure_probability_pct;
    const safePct = result.summary.safe_probability_pct ?? Math.max(0, 100 - failurePct);
    return (
      <div style={{ minHeight: "100vh", background: COLORS.bg, color: COLORS.text, fontFamily: "'DM Sans','Segoe UI',sans-serif", padding: 12 }}>
        <div style={{ maxWidth: 900, margin: "0 auto", display: "grid", gap: 8 }}>
          <div style={{ display: "flex", gap: 8, justifySelf: "start" }}>
            <button onClick={reset} style={{ padding: "5px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: "pointer", fontSize: 11 }}>New Analysis</button>
            {fleetParentResult && (
              <button onClick={backToFleetSummary} style={{ padding: "5px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: "pointer", fontSize: 11 }}>Back to Fleet Summary</button>
            )}
          </div>
          {confidence && (
            <div style={{ background: COLORS.panel, border: `1px solid ${confidence.badge_color}`, borderRadius: 8, padding: 8, color: confidence.badge_color, fontWeight: 600, fontSize: 12 }}>
              {confidence.banner_text}
            </div>
          )}
          <DataQualityBanner flags={result.data_quality_flags} />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: 8 }}>
            <Stat label="Risk Level" value={result.summary.failure_risk} />
            <Stat label="Failure Probability" value={`${result.summary.failure_probability_pct.toFixed(1)}%`} />
            <Stat label="Remaining Life" value={result.summary.estimated_remaining_life} />
            <Stat label="Maintenance" value={result.summary.maintenance_urgency} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div style={panelStyle()}>
              <h3 style={titleStyle()}>Failure Probability Meter</h3>
              <RadialGauge
                value={failurePct}
                min={0}
                max={100}
                color={failurePct < 35 ? COLORS.good : failurePct < 60 ? COLORS.warn : COLORS.bad}
                label={`${failurePct.toFixed(1)}%`}
                subtitle="0% healthy → 100% imminent"
              />
            </div>
            <div style={panelStyle()}>
              <h3 style={titleStyle()}>Contributing Risk Factors</h3>
              {result.insufficient_trend_signal ? (
                <div style={{ color: COLORS.warn, fontSize: 11 }}>No significant trend signal yet.</div>
              ) : (
                result.risk_factors.slice(0, 6).map((rf, i) => (
                  <div key={`${rf.parameter}-${i}`} style={{ padding: "4px 0", borderBottom: "1px solid #e2e8f0", fontSize: 11 }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <b>{rf.parameter}</b>
                      <span>{rf.contribution_pct}%</span>
                    </div>
                    <div style={{ height: 5, background: "#e2e8f0", borderRadius: 4, margin: "3px 0 4px" }}>
                      <div style={{ height: "100%", width: `${Math.min(100, rf.contribution_pct)}%`, background: "#f59e0b", borderRadius: 4 }} />
                    </div>
                    <div style={{ color: COLORS.muted, fontSize: 10 }}>{rf.context}</div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div style={panelStyle()}>
            <h3 style={titleStyle()}>Failure vs Safe Breakdown</h3>
            <div style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: 10, alignItems: "center" }}>
              <DonutChart
                segments={[
                  { value: failurePct, color: COLORS.bad, label: "Failure" },
                  { value: safePct, color: COLORS.good, label: "Safe" },
                ]}
                size={80}
                inner={35}
              />
              <div style={{ display: "grid", gap: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", color: COLORS.muted, fontSize: 11 }}>
                  <span>Failure</span><b style={{ color: COLORS.bad }}>{failurePct.toFixed(1)}%</b>
                </div>
                <div style={{ height: 6, background: "#e2e8f0", borderRadius: 4 }}>
                  <div style={{ width: `${failurePct}%`, height: "100%", background: COLORS.bad, borderRadius: 4 }} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", color: COLORS.muted, fontSize: 11 }}>
                  <span>Safe</span><b style={{ color: COLORS.good }}>{safePct.toFixed(1)}%</b>
                </div>
                <div style={{ height: 6, background: "#e2e8f0", borderRadius: 4 }}>
                  <div style={{ width: `${safePct}%`, height: "100%", background: COLORS.good, borderRadius: 4 }} />
                </div>
              </div>
            </div>
          </div>

          <div style={panelStyle()}>
            <h3 style={titleStyle()}>Recommended Actions</h3>
            {result.recommended_actions.length === 0 && (
              <div style={{ color: COLORS.muted, fontSize: 11 }}>No immediate actions generated yet.</div>
            )}
            {result.recommended_actions.map((r) => (
              <div key={r.rank} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "6px 0", borderBottom: "1px solid #e2e8f0", fontSize: 11 }}>
                <div>
                  <b>{r.rank}. {r.action}</b>
                  <div style={{ color: COLORS.muted, fontSize: 10 }}>{r.reasoning}</div>
                </div>
                <span style={{ color: COLORS.warn }}>{r.urgency}</span>
              </div>
            ))}
          </div>

          <div style={panelStyle()}>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", fontSize: 11 }}>
            <div style={{ color: COLORS.muted }}>
              Type: <b style={{ color: COLORS.text }}>{formatAnalysisLabel(result)}</b>
            </div>
            <div style={{ color: COLORS.muted }}>
              Days Analysed: <b style={{ color: COLORS.text }}>{formatDaysAnalysed(result.summary.days_analyzed)}</b>
            </div>
            <div style={{ color: COLORS.good }}>
                Completion: <b>100%</b>
              </div>
            </div>
          </div>

          <ConfidenceSummaryPanel result={result} />

          {result.time_to_failure && (
            <div style={panelStyle()}>
              <h3 style={titleStyle()}>Failure Forecast</h3>
              <div style={{ fontSize: 13, fontWeight: 700 }}>{result.time_to_failure.label ?? "No trend forecast"}</div>
              {result.time_to_failure.confidence_interval ? (
                <div style={{ marginTop: 4, color: COLORS.muted, fontSize: 11 }}>
                  Range: {result.time_to_failure.confidence_interval[0]}–{result.time_to_failure.confidence_interval[1]} hours
                </div>
              ) : null}
              <div style={{ marginTop: 4, color: COLORS.muted, fontSize: 10 }}>
                Trend: {result.time_to_failure.trend_type ?? "unknown"}
                {result.time_to_failure.trend_r2 != null ? ` (R²=${result.time_to_failure.trend_r2.toFixed(2)})` : ""}
                {" · "}
                {result.time_to_failure.is_reliable ? "Reliable" : "Low reliability"}
              </div>
            </div>
          )}

          {result.reasoning && (
            <div style={{ ...panelStyle(), background: "#f8fafc" }}>
              <h3 style={titleStyle()}>Why is this flagged?</h3>
              <div style={{ fontSize: 11, fontWeight: 600 }}>{result.reasoning.summary ?? "No summary available."}</div>
              {(result.reasoning.evidence_text || (result.reasoning as { agreement_text?: string }).agreement_text) ? (
                <div style={{ marginTop: 4, color: COLORS.muted, fontSize: 11 }}>
                  {sanitizeAnalyticsNarrative(
                    result.reasoning.evidence_text || (result.reasoning as { agreement_text?: string }).agreement_text,
                    "Evidence strength reflects the consistency of the observed telemetry pattern.",
                  )}
                </div>
              ) : null}
              {result.reasoning.top_risk_factors?.length ? (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 10, color: COLORS.muted, textTransform: "uppercase", letterSpacing: 1 }}>Top contributing factors</div>
                  <ol style={{ margin: "4px 0 0", paddingLeft: 16, fontSize: 11 }}>
                    {result.reasoning.top_risk_factors.map((f, i) => (
                      <li key={`rf-${i}`}>{f}</li>
                    ))}
                  </ol>
                </div>
              ) : null}
              {result.reasoning.recommended_actions?.length ? (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 10, color: COLORS.muted, textTransform: "uppercase", letterSpacing: 1 }}>Recommended actions</div>
                  <div style={{ marginTop: 4, display: "grid", gap: 2, fontSize: 11 }}>
                    {result.reasoning.recommended_actions.map((a, i) => (
                      <div key={`ra-${i}`}>→ {a}</div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          )}

          {result.degradation_series?.length ? (
            <div style={panelStyle()}>
              <h3 style={titleStyle()}>Degradation Trend</h3>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={result.degradation_series.map((v, i) => ({ t: i, score: v }))}>
                  <XAxis dataKey="t" hide />
                  <YAxis domain={[0, 1]} />
                  <Tooltip />
                  <ReferenceLine
                    y={0.85}
                    stroke="red"
                    strokeDasharray="4 2"
                    label={{ value: "Failure threshold", position: "right", fontSize: 11, fill: "red" }}
                  />
                  <Area type="monotone" dataKey="score" stroke="#f97316" fill="#fed7aa" strokeWidth={2} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  if (screen === "fleet" && result && result.analysis_type === "fleet") {
    const exec = (result.execution_metadata ?? {}) as FleetExecMeta;
    const skipped = Array.isArray(exec.devices_skipped) ? exec.devices_skipped : [];
    const failed = Array.isArray(exec.devices_failed) ? exec.devices_failed : [];
    const readyCount = Array.isArray(exec.devices_ready) ? exec.devices_ready.length : result.device_summaries.length;
    const fleetSnapshot = getFleetSnapshotFromExecutionMetadata(exec, readyCount);
    const selectedCount = Number(fleetSnapshot.selected_device_count ?? 0);
    const coverage = Number(fleetSnapshot.coverage_pct ?? 0);
    const fleetOutcome = getFleetOutcomeSummary("completed", fleetSnapshot, { resultReady: true });
    const outcomeTone = fleetOutcome?.tone === "good"
      ? { border: "1px solid #bbf7d0", background: "#f0fdf4", accent: "#166534" }
      : fleetOutcome?.tone === "bad"
        ? { border: "1px solid #fecaca", background: "#fff1f2", accent: "#b91c1c" }
        : { border: "1px solid #fde68a", background: "#fffbeb", accent: "#b45309" };
    return (
      <div style={{ minHeight: "100vh", background: COLORS.bg, color: COLORS.text, fontFamily: "'DM Sans','Segoe UI',sans-serif", padding: 12 }}>
        <div style={{ maxWidth: 900, margin: "0 auto", display: "grid", gap: 8 }}>
          <button onClick={reset} style={{ justifySelf: "start", padding: "5px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: "pointer", fontSize: 11 }}>New Analysis</button>
          <div style={{ ...panelStyle(), background: outcomeTone.background, border: outcomeTone.border }}>
            <div style={{ color: outcomeTone.accent, fontSize: 10, textTransform: "uppercase", letterSpacing: 1.2 }}>
              Fleet Result
            </div>
            <div style={{ marginTop: 4, fontSize: 16, fontWeight: 700, color: COLORS.text }}>
              {fleetOutcome?.headline ?? "Fleet analysis result"}
            </div>
            <div style={{ marginTop: 4, color: COLORS.muted, fontSize: 11 }}>
              {fleetOutcome?.summary ?? `${selectedCount} devices were selected for this run.`}
            </div>
            <div style={{ marginTop: 6, color: COLORS.muted, fontSize: 11 }}>
              {fleetOutcome?.action ?? "Review the coverage summary below to decide whether further action is needed."}
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 8 }}>
            <Stat label="Fleet Health" value={`${result.fleet_health_score}%`} />
            <Stat label="Worst Device" value={result.worst_device_id || "N/A"} />
            <Stat label="Critical Devices" value={String(result.critical_devices.length)} />
            <Stat label="Completed" value={String(readyCount)} />
            <Stat label="Failed" value={String(failed.length)} />
            <Stat label="Skipped" value={String(skipped.length)} />
            <Stat label="Coverage" value={`${coverage.toFixed(1)}%`} />
          </div>
          <div style={panelStyle()}>
            <h3 style={titleStyle()}>Fleet Coverage Summary</h3>
            <div style={{ fontSize: 11, color: COLORS.muted }}>
              Analyzed <b style={{ color: COLORS.text }}>{readyCount}</b> / <b style={{ color: COLORS.text }}>{selectedCount}</b> devices
              {" · "}
              Coverage <b style={{ color: COLORS.text }}>{coverage.toFixed(1)}%</b>
            </div>
            <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
              {[
                `${readyCount} completed`,
                `${failed.length} failed`,
                `${skipped.length} skipped`,
              ].map((item) => (
                <span
                  key={item}
                  style={{
                    borderRadius: 999,
                    padding: "4px 8px",
                    background: "#f8fafc",
                    border: "1px solid #dbeafe",
                    color: "#1e3a8a",
                    fontSize: 10,
                    fontWeight: 600,
                  }}
                >
                  {item}
                </span>
              ))}
            </div>
            <div style={{ marginTop: 8, fontSize: 11, color: COLORS.muted }}>
              {fleetOutcome?.isComplete
                ? "This fleet result includes every selected device."
                : fleetOutcome?.hasUsableCoverage
                  ? "This fleet result is usable now, but some selected devices are not included in the final summary."
                  : "This fleet run did not produce usable device coverage."}
            </div>
            {(skipped.length > 0 || failed.length > 0) && (
              <div style={{ marginTop: 8, display: "grid", gap: 4 }}>
                {[...skipped, ...failed].slice(0, 20).map((item, idx: number) => (
                  <div key={`skip-${idx}`} style={{ fontSize: 11, color: COLORS.bad }}>
                    {item.device_id}: {item.message || item.reason || "Not analyzed"}
                  </div>
                ))}
                {skipped.length + failed.length > 20 && (
                  <div style={{ fontSize: 10, color: COLORS.muted }}>
                    +{skipped.length + failed.length - 20} more devices omitted
                  </div>
                )}
              </div>
            )}
          </div>
          <div style={panelStyle()}>
            <h3 style={titleStyle()}>What To Do Next</h3>
            <div style={{ fontSize: 11, color: COLORS.muted }}>
              {fleetOutcome?.action ?? "Review the fleet summary and reopen device-level results if a machine needs attention."}
            </div>
          </div>
          <div style={panelStyle()}>
            <h3 style={titleStyle()}>Device Summaries</h3>
            {fleetNotice && (
              <div style={{ marginBottom: 8, color: COLORS.bad, fontSize: 11 }}>{fleetNotice}</div>
            )}
            {result.device_summaries.map((d) => (
              <button
                key={d.device_id}
                onClick={() => openFleetDevice(d.device_id, d.child_job_id)}
                style={{
                  width: "100%",
                  display: "grid",
                  gridTemplateColumns: "1fr 100px 100px",
                  gap: 8,
                  padding: "6px 0",
                  border: "none",
                  borderBottom: "1px solid #e2e8f0",
                  background: "transparent",
                  color: COLORS.text,
                  fontSize: 11,
                  textAlign: "left",
                  cursor: "pointer",
                }}
              >
                <b>{d.device_id}</b>
                <span>Health {d.health_score}%</span>
                <span>{d.failure_risk || `${d.total_anomalies || 0} anomalies`}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (screen === "blocked" && blockedResult) {
    const tone = blockedResult.status === "no_data"
      ? { border: "#f59e0b", bg: "#fffbeb", text: "#92400e", title: "No telemetry in selected range" }
      : { border: "#f97316", bg: "#fff7ed", text: "#9a3412", title: "Insufficient telemetry coverage" };
    return (
      <div style={{ minHeight: "100vh", background: COLORS.bg, color: COLORS.text, fontFamily: "'DM Sans','Segoe UI',sans-serif", padding: 12 }}>
        <div style={{ maxWidth: 900, margin: "0 auto", display: "grid", gap: 8 }}>
          <div style={{ display: "flex", gap: 8, justifySelf: "start" }}>
            <button onClick={reset} style={{ padding: "5px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: "pointer", fontSize: 11 }}>New Analysis</button>
            {fleetParentResult && (
              <button onClick={backToFleetSummary} style={{ padding: "5px 8px", borderRadius: 6, border: `1px solid ${COLORS.panelBorder}`, background: "white", color: COLORS.text, cursor: "pointer", fontSize: 11 }}>Back to Fleet Summary</button>
            )}
          </div>
          <div style={{ ...panelStyle(), border: `1px solid ${tone.border}`, background: tone.bg }}>
            <h3 style={{ ...titleStyle(), color: tone.text }}>{tone.title}</h3>
            <div style={{ fontSize: 12 }}>{blockedResult.summary}</div>
            {blockedResult.coverage_result?.message ? (
              <div style={{ marginTop: 8, fontSize: 11, color: COLORS.muted }}>{blockedResult.coverage_result.message}</div>
            ) : null}
            <div style={{ marginTop: 8, fontSize: 11, color: COLORS.muted }}>
              This completed history result is intentionally viewable, but no chart-ready analytics output exists for this run.
            </div>
          </div>
        </div>
      </div>
    );
  }

  return <div style={{ minHeight: "100vh", background: COLORS.bg }} />;
}

function panelStyle(): React.CSSProperties {
  return {
    background: COLORS.panel,
    border: `1px solid ${COLORS.panelBorder}`,
    borderRadius: 8,
    padding: 10,
  };
}

function titleStyle(): React.CSSProperties {
  return {
    margin: "0 0 8px",
    fontSize: 12,
  };
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: COLORS.panel, border: `1px solid ${COLORS.panelBorder}`, borderRadius: 8, padding: 8 }}>
      <div style={{ fontSize: 10, color: COLORS.muted }}>{label}</div>
      <div style={{ marginTop: 1, fontSize: 14, fontWeight: 700, color: badgeColor(value) }}>{value}</div>
    </div>
  );
}

function RadialGauge({
  value,
  min,
  max,
  color,
  label,
  subtitle,
}: {
  value: number;
  min: number;
  max: number;
  color: string;
  label: string;
  subtitle: string;
}) {
  const clamped = Math.min(max, Math.max(min, value));
  const ratio = (clamped - min) / Math.max(1e-6, max - min);
  const sweep = 270;
  const rotate = -225;
  const dash = 235;
  const filled = dash * (ratio * (sweep / 360));
  return (
    <div style={{ display: "grid", placeItems: "center", padding: "4px 0 6px" }}>
      <div style={{ position: "relative", width: 90, height: 90 }}>
        <svg width="90" height="90">
          <g transform={`rotate(${rotate} 45 45)`}>
            <circle cx="45" cy="45" r="37" fill="none" stroke="#e2e8f0" strokeWidth="7" strokeDasharray={`${dash * (sweep / 360)} ${dash}`} strokeLinecap="round" />
            <circle cx="45" cy="45" r="37" fill="none" stroke={color} strokeWidth="7" strokeDasharray={`${filled} ${dash}`} strokeLinecap="round" />
          </g>
        </svg>
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ fontSize: 14, fontWeight: 700, color }}>{label}</div>
        </div>
      </div>
      <div style={{ color: COLORS.muted, fontSize: 9, marginTop: -6 }}>{subtitle}</div>
    </div>
  );
}

function DonutChart({
  segments,
  size,
  inner,
}: {
  segments: Array<{ value: number; color: string; label: string }>;
  size: number;
  inner: number;
}) {
  const total = segments.reduce((s, x) => s + Math.max(0, x.value), 0);
  const r = size / 2 - 8;
  const c = size / 2;
  const paths = segments.reduce<{ start: number; nodes: React.ReactNode[] }>(
    (acc, seg, i) => {
      const part = total > 0 ? seg.value / total : 0;
      const end = acc.start + part;
      const a0 = acc.start * Math.PI * 2 - Math.PI / 2;
      const a1 = end * Math.PI * 2 - Math.PI / 2;
      const x0 = c + r * Math.cos(a0);
      const y0 = c + r * Math.sin(a0);
      const x1 = c + r * Math.cos(a1);
      const y1 = c + r * Math.sin(a1);
      const large = end - acc.start > 0.5 ? 1 : 0;
      acc.nodes.push(
        <path
          key={`${seg.label}-${i}`}
          d={`M ${c} ${c} L ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} Z`}
          fill={seg.color}
        />
      );
      return { start: end, nodes: acc.nodes };
    },
    { start: 0, nodes: [] }
  ).nodes;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {paths}
      <circle cx={c} cy={c} r={inner / 2} fill={COLORS.panel} />
    </svg>
  );
}

export default function AnalyticsPage() {
  return (
    <Suspense fallback={<div style={{ minHeight: "100vh", background: COLORS.bg, color: COLORS.muted, padding: 24 }}>Loading analytics...</div>}>
      <AnalyticsPageContent />
    </Suspense>
  );
}
