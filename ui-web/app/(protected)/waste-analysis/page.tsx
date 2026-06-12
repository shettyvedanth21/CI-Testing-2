"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AsyncJobHandoffCard } from "@/components/reports/AsyncJobHandoffCard";
import { DateRangeSelector } from "@/components/reports/DateRangeSelector";
import { DeviceScopeSelector } from "@/components/reports/DeviceScopeSelector";
import { authApi, type PlantProfile } from "@/lib/authApi";
import { useAuth } from "@/lib/authContext";
import { getDevices, type Device } from "@/lib/deviceApi";
import {
  buildDeviceScopeCatalog,
  getDeviceScopeSummary,
  normalizeDeviceScopeSelection,
  resolveDeviceIdsForSelection,
  type DeviceScopeSelection,
} from "@/lib/deviceScopeSelection";
import {
  formatJobStatusSummary,
  getJobFailureSummary,
  getUserFacingJobStatusLabelWithCoverage,
} from "@/lib/asyncJobPresentation";
import { resolveScopedTenantId, resolveVisiblePlants } from "@/lib/orgScope";
import { formatCurrencyValue, formatEnergyKwh } from "@/lib/presentation";
import { getWasteDefaultRange } from "@/lib/reportDateRange";
import { getTariffConfig } from "@/lib/settingsApi";
import { useTenantStore } from "@/lib/tenantStore";
import { formatIST } from "@/lib/utils";
import {
  downloadWastePdf,
  getWasteHistory,
  getWasteResult,
  getWasteStatus,
  runWasteAnalysis,
  type WasteGranularity,
  type WasteHistoryItem,
  type WasteJobSummary,
  type WasteStatus,
  WasteApiError,
} from "@/lib/wasteApi";
import { buildWasteRunParams } from "@/lib/wasteScopeRequest";
import { EXCLUSIVE_LOSS_BUCKET_HELP, WASTE_ANALYSIS_POLICY_HELP } from "@/lib/wasteSemantics";

const WASTE_HISTORY_PAGE_SIZE = 5;

interface WastageCategoryView {
  duration_sec?: number | null;
  energy_kwh?: number | null;
  cost?: number | null;
  skipped_reason?: string | null;
  pf_estimated?: boolean;
  config_source?: string | null;
}

interface WasteResultDevice {
  device_id: string;
  device_name?: string;
  idle?: WastageCategoryView;
  off_hours?: WastageCategoryView;
  overconsumption?: WastageCategoryView;
  idle_duration_sec?: number | null;
  idle_energy_kwh?: number | null;
  idle_cost?: number | null;
  offhours_duration_sec?: number | null;
  offhours_energy_kwh?: number | null;
  offhours_cost?: number | null;
  offhours_skipped_reason?: string | null;
  offhours_pf_estimated?: boolean;
  overconsumption_duration_sec?: number | null;
  overconsumption_kwh?: number | null;
  overconsumption_cost?: number | null;
  overconsumption_skipped_reason?: string | null;
  overconsumption_pf_estimated?: boolean;
}

interface WasteResultPayload {
  total_waste_cost?: number | null;
  total_energy_cost?: number | null;
  total_energy_kwh?: number | null;
  total_idle_kwh?: number | null;
  warnings?: string[];
  insights?: string[];
  quality_gate_passed?: boolean;
  quality_failures?: Array<{ device_id?: string; message?: string; code?: string }>;
  skipped_devices?: Array<{ device_id?: string; reason?: string }>;
  device_summaries?: WasteResultDevice[];
}

function formatDurationSeconds(seconds?: number | null): string {
  if (seconds == null) return "—";
  const mins = Math.round(seconds / 60);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h} hr ${m} min` : `${m} min`;
}

function WastageRow({
  label,
  duration,
  kwh,
  cost,
  skippedReason,
  pfEstimated,
  configSource,
}: {
  label: string;
  duration?: number | null;
  kwh?: number | null;
  cost?: number | null;
  skippedReason?: string | null;
  pfEstimated?: boolean;
  configSource?: string | null;
}) {
  if (skippedReason) {
    return (
      <tr className="text-gray-400">
        <td className="py-2 pr-4">{label}</td>
        <td colSpan={3} className="py-2 pr-4 text-sm italic">{skippedReason}</td>
      </tr>
    );
  }

  return (
    <tr>
      <td className="py-2 pr-4">
        {label}
        {pfEstimated ? <span className="ml-1 text-xs text-amber-700" title="Power factor estimated at 0.85">*</span> : null}
        {configSource ? (
          <span className="ml-2 text-[10px] uppercase tracking-wide text-slate-500">({configSource})</span>
        ) : null}
      </td>
      <td className="py-2 pr-4">{formatDurationSeconds(duration)}</td>
      <td className="py-2 pr-4">{kwh != null ? formatEnergyKwh(Number(kwh)) : "—"}</td>
      <td className="py-2 pr-4">{cost != null ? formatCurrencyValue(Number(cost), "INR") : "—"}</td>
    </tr>
  );
}

function WastageMobileItem({
  label,
  duration,
  kwh,
  cost,
  skippedReason,
  pfEstimated,
  configSource,
}: {
  label: string;
  duration?: number | null;
  kwh?: number | null;
  cost?: number | null;
  skippedReason?: string | null;
  pfEstimated?: boolean;
  configSource?: string | null;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-slate-900">{label}</p>
        {pfEstimated ? <span className="text-xs text-amber-700" title="Power factor estimated at 0.85">*</span> : null}
        {configSource ? (
          <span className="text-[10px] uppercase tracking-wide text-slate-500">({configSource})</span>
        ) : null}
      </div>
      {skippedReason ? (
        <p className="mt-2 text-sm italic text-slate-500">{skippedReason}</p>
      ) : (
        <dl className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Duration</dt>
            <dd className="mt-1 text-sm font-semibold text-slate-900">{formatDurationSeconds(duration)}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Energy</dt>
            <dd className="mt-1 text-sm font-semibold text-slate-900">{kwh != null ? formatEnergyKwh(Number(kwh)) : "—"}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Cost</dt>
            <dd className="mt-1 text-sm font-semibold text-slate-900">{cost != null ? formatCurrencyValue(Number(cost), "INR") : "—"}</dd>
          </div>
        </dl>
      )}
    </div>
  );
}

function WasteHistoryMobileCard({
  job,
  isSelected,
  onOpen,
  onDownload,
  isDownloading,
}: {
  job: WasteHistoryItem;
  isSelected: boolean;
  onOpen: () => void;
  onDownload: () => void;
  isDownloading: boolean;
}) {
  const summary = job.phase_label?.trim() || job.stage?.trim() || job.status;
  return (
    <article
      onClick={onOpen}
      className={`rounded-2xl border p-4 shadow-sm transition ${
        isSelected ? "border-blue-200 bg-blue-50/40" : "border-slate-200 bg-white"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Analysis</p>
          <p className="mt-1 text-base font-semibold text-slate-900">{job.job_name || `Waste analysis ${job.job_id.slice(0, 8)}`}</p>
          <p className="mt-1 text-xs text-slate-500">
            {job.requested_device_count ? `${job.requested_device_count} devices` : "All accessible devices"}
          </p>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusTone(job.status)}`}>
          {getUserFacingJobStatusLabelWithCoverage(job)}
        </span>
      </div>
      <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
        <div className="text-sm font-medium text-slate-900">{job.progress_pct}% complete</div>
        <div className="mt-1 text-xs text-slate-500">{summary}</div>
        <div className="mt-2 text-xs text-slate-500">{job.created_at ? formatIST(job.created_at, "—") : "—"}</div>
      </div>
      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
          className="inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          View details
        </button>
        {job.download_ready ? (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onDownload();
            }}
            disabled={isDownloading}
            className="inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            {isDownloading ? "Preparing download..." : "Download PDF"}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function normalizeWasteApiError(error: unknown, fallback: string): string {
  if (error instanceof WasteApiError) {
    return getJobFailureSummary(error.body as {
      error_code?: string | null;
      error_message?: string | null;
      message?: string | null;
    }, error.message || fallback);
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  return fallback;
}

function statusTone(status?: string | null): string {
  if (status === "completed") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (status === "failed") return "border-rose-200 bg-rose-50 text-rose-800";
  if (status === "running") return "border-blue-200 bg-blue-50 text-blue-800";
  return "border-amber-200 bg-amber-50 text-amber-800";
}

function isWasteJobTerminal(status?: string | null): boolean {
  return status === "completed" || status === "failed";
}

type WasteJobStatePresentation = {
  headline: string;
  summary: string;
  nextAction: string;
  resultStateLabel: string;
  resultStateSummary: string;
  downloadStateLabel: string;
  downloadStateSummary: string;
  toneClassName: string;
  technicalDetail?: string | null;
};

function getWasteJobStatePresentation(
  job: WasteJobSummary,
  failureSummary: string | null,
): WasteJobStatePresentation {
  const phaseText = job.phase_label?.trim() || job.stage?.trim() || "Processing";
  const technicalDetail = job.error_message?.trim() || null;
  const status = job.status;
  const resultReady = Boolean(job.result_ready);
  const downloadReady = Boolean(job.download_ready);
  const artifactGenerationAllowed = job.coverage_result?.artifact_generation_allowed !== false;

  if (status === "pending") {
    return {
      headline: "Queued for processing",
      summary: "This waste analysis is waiting for processing capacity and has not started running yet.",
      nextAction: "You can leave this page and return from Waste Analysis History when processing begins.",
      resultStateLabel: "Queued",
      resultStateSummary: "Result data will appear here as soon as processing starts and completes.",
      downloadStateLabel: "Waiting for result",
      downloadStateSummary: "PDF generation has not started yet.",
      toneClassName: "border-amber-200 bg-amber-50 text-amber-900",
    };
  }

  if (status === "running") {
    return {
      headline: "Analysis is in progress",
      summary: `This waste analysis is currently running. Current step: ${phaseText}.`,
      nextAction: "You can continue using the platform and return here later to open the result or download the PDF.",
      resultStateLabel: "Processing in background",
      resultStateSummary: "Result data is not ready yet.",
      downloadStateLabel: "Waiting for result",
      downloadStateSummary: "The PDF will only become available after the result is ready.",
      toneClassName: "border-blue-200 bg-blue-50 text-blue-900",
    };
  }

  if (
    status === "completed" &&
    resultReady &&
    (job.error_code === "ARTIFACT_GENERATION_FAILED" || job.error_code === "ARTIFACT_UPLOAD_FAILED")
  ) {
    return {
      headline: downloadReady ? "Result ready, download recovering" : "Result ready, PDF unavailable",
      summary: downloadReady
        ? "The waste analysis finished successfully. Stored artifact upload failed, but a fresh PDF can still be generated from the saved result."
        : "The waste analysis finished successfully and the result is available below, but the PDF artifact could not be prepared for this run.",
      nextAction: downloadReady
        ? "Open the result below or download a freshly generated PDF now."
        : "Open the result below now. You only need to rerun if you specifically need a fresh PDF after storage is fixed.",
      resultStateLabel: "Result ready",
      resultStateSummary: "Result data is available to open now.",
      downloadStateLabel: downloadReady ? "Download available" : "PDF unavailable",
      downloadStateSummary: downloadReady
        ? "A fresh PDF can be generated from the saved result on demand."
        : "This run completed, but the PDF artifact could not be prepared.",
      toneClassName: "border-amber-200 bg-amber-50 text-amber-900",
      technicalDetail: technicalDetail,
    };
  }

  if (status === "failed") {
    const usableResult = resultReady ? "An analysis result is still available for review." : "No analysis result is available yet.";
    const usableDownload = downloadReady ? "A PDF is available to download." : "A PDF is not available for this run.";
    return {
      headline: resultReady ? "Completed with issues" : "Waste analysis could not be completed",
      summary: failureSummary || "This waste analysis could not be completed.",
      nextAction: resultReady
        ? "Open the available result below to review what completed successfully before rerunning."
        : "Review the issue below, adjust the inputs if needed, and rerun the analysis when you are ready.",
      resultStateLabel: resultReady ? "Result available" : "Result unavailable",
      resultStateSummary: usableResult,
      downloadStateLabel: downloadReady ? "PDF ready" : "PDF unavailable",
      downloadStateSummary: usableDownload,
      toneClassName: resultReady ? "border-amber-200 bg-amber-50 text-amber-900" : "border-rose-200 bg-rose-50 text-rose-900",
      technicalDetail: technicalDetail && technicalDetail !== failureSummary ? technicalDetail : null,
    };
  }

  if (!resultReady) {
    return {
      headline: "Result is finalizing",
      summary: "Waste analysis completed successfully. Results are still being finalized for this history view.",
      nextAction: "Check back shortly. The result will appear here as soon as finalization completes.",
      resultStateLabel: "Finalizing",
      resultStateSummary: "Result data is not ready yet.",
      downloadStateLabel: "Waiting for result",
      downloadStateSummary: "PDF generation will begin after the result is ready.",
      toneClassName: "border-amber-200 bg-amber-50 text-amber-900",
    };
  }

  if (!downloadReady && !artifactGenerationAllowed) {
    return {
      headline: "Result is ready, PDF unavailable for this run",
      summary: "The waste analysis result is available below, but this run did not qualify for a downloadable PDF because one or more selected devices did not have enough usable telemetry coverage.",
      nextAction: "Review the result and warnings below now. If you need a downloadable PDF, rerun with a date range or device selection that has usable telemetry for all included devices.",
      resultStateLabel: "Result ready",
      resultStateSummary: "Result data is available to open now.",
      downloadStateLabel: "PDF unavailable",
      downloadStateSummary: "This run completed with coverage warnings, so a PDF artifact was not generated.",
      toneClassName: "border-amber-200 bg-amber-50 text-amber-900",
    };
  }

  if (!downloadReady) {
    return {
      headline: "Result is ready",
      summary: "You can open the waste analysis result now. The PDF is still being prepared.",
      nextAction: "Open the result below now, or return later if you also need the downloadable PDF.",
      resultStateLabel: "Result ready",
      resultStateSummary: "Result data is available to open now.",
      downloadStateLabel: "PDF in progress",
      downloadStateSummary: "PDF download will appear here as soon as it is ready.",
      toneClassName: "border-blue-200 bg-blue-50 text-blue-900",
    };
  }

  return {
    headline: "Result and PDF ready",
    summary: "This waste analysis finished successfully. You can open the result or download the PDF now.",
    nextAction: "Open the result for review, or download the PDF if you need a shareable artifact.",
    resultStateLabel: "Result ready",
    resultStateSummary: "Result data is available to open now.",
    downloadStateLabel: "PDF ready",
    downloadStateSummary: "The PDF artifact is ready to download.",
    toneClassName: "border-emerald-200 bg-emerald-50 text-emerald-900",
  };
}

export default function WasteAnalysisPage() {
  const { me } = useAuth();
  const { selectedTenantId } = useTenantStore();

  const [devices, setDevices] = useState<Device[]>([]);
  const [plants, setPlants] = useState<PlantProfile[]>([]);
  const [scopeSelection, setScopeSelection] = useState<DeviceScopeSelection>({
    mode: "all",
    plantId: null,
    deviceIds: [],
  });
  const [defaultRange, setDefaultRange] = useState<{ start: string; end: string } | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [granularity, setGranularity] = useState<WasteGranularity>("daily");
  const [jobName, setJobName] = useState("");

  const [history, setHistory] = useState<WasteHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyPage, setHistoryPage] = useState(0);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedJobStatus, setSelectedJobStatus] = useState<WasteStatus | null>(null);
  const [submittedJobId, setSubmittedJobId] = useState<string | null>(null);
  const [submittedJobStatus, setSubmittedJobStatus] = useState<WasteStatus | null>(null);
  const [resultData, setResultData] = useState<WasteResultPayload | null>(null);
  const [resultJobId, setResultJobId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDateRangeValid, setIsDateRangeValid] = useState(true);
  const [isOpeningResult, setIsOpeningResult] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tariffBanner, setTariffBanner] = useState<string | null>(null);
  const [thresholdNotice] = useState(
    "Missing threshold no longer blocks report generation. Overconsumption will be marked as skipped with reason.",
  );
  const activeResultRequest = useRef(0);
  const acceptedHandoffRef = useRef<HTMLDivElement | null>(null);
  const selectedJobPanelRef = useRef<HTMLDivElement | null>(null);
  const [detailJumpRequest, setDetailJumpRequest] = useState<{ jobId: string; nonce: number } | null>(null);

  const scopedOrgId = resolveScopedTenantId(me, selectedTenantId);
  const visiblePlants = useMemo(() => resolveVisiblePlants(me, plants), [me, plants]);
  const scopeCatalog = useMemo(() => buildDeviceScopeCatalog(devices, visiblePlants), [devices, visiblePlants]);
  const normalizedScopeSelection = useMemo(
    () => normalizeDeviceScopeSelection(scopeSelection, scopeCatalog),
    [scopeSelection, scopeCatalog],
  );
  const selectedDeviceIds = useMemo(
    () => resolveDeviceIdsForSelection(normalizedScopeSelection, scopeCatalog),
    [normalizedScopeSelection, scopeCatalog],
  );
  const selectedScopeSummary = useMemo(
    () => getDeviceScopeSummary(normalizedScopeSelection, scopeCatalog),
    [normalizedScopeSelection, scopeCatalog],
  );

  const selectedJob = useMemo(
    () => history.find((item) => item.job_id === selectedJobId) ?? (selectedJobStatus && selectedJobStatus.job_id === selectedJobId ? selectedJobStatus : null),
    [history, selectedJobId, selectedJobStatus],
  );

  const liveHistoryJobs = useMemo(
    () => history.some((item) => item.status === "pending" || item.status === "running"),
    [history],
  );

  const openWasteJobDetails = useCallback((jobId: string) => {
    setSelectedJobId(jobId);
    setDetailJumpRequest({ jobId, nonce: Date.now() });
  }, []);

  useEffect(() => {
    const range = getWasteDefaultRange();
    setDefaultRange(range);
    setStartDate(range.start);
    setEndDate(range.end);
  }, []);

  async function loadHistory(page = historyPage, preserveSelection = true) {
    setHistoryLoading(true);
    try {
      const payload = await getWasteHistory(WASTE_HISTORY_PAGE_SIZE + 1, page * WASTE_HISTORY_PAGE_SIZE);
      const items = payload.items || [];
      const pageItems = items.slice(0, WASTE_HISTORY_PAGE_SIZE);
      setHistory(pageItems);
      setHasMoreHistory(items.length > WASTE_HISTORY_PAGE_SIZE);
      setHistoryPage(page);
      setSelectedJobId((current) => {
        if (preserveSelection && current && pageItems.some((item) => item.job_id === current)) {
          return current;
        }
        return pageItems[0]?.job_id ?? null;
      });
    } catch (loadError) {
      setError(normalizeWasteApiError(loadError, "Failed to load waste analysis history"));
    } finally {
      setHistoryLoading(false);
    }
  }

  useEffect(() => {
    async function bootstrap() {
      try {
        const [deviceRows, tariff, plantRows] = await Promise.all([
          getDevices(),
          getTariffConfig(),
          scopedOrgId ? authApi.listPlants(scopedOrgId) : Promise.resolve([]),
        ]);
        setDevices(deviceRows);
        setPlants(plantRows);
        if (!tariff?.rate) {
          setTariffBanner("Tariff not configured. Cost calculations will be unavailable. Configure this in Settings > Tariff Configuration.");
        } else {
          setTariffBanner(null);
        }
      } catch (bootstrapError) {
        setError(normalizeWasteApiError(bootstrapError, "Failed to initialize waste analysis page"));
      }
      await loadHistory(0, false);
    }

    void bootstrap();
  }, [scopedOrgId]);

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

  useEffect(() => {
    if (!selectedJobId) {
      setSelectedJobStatus(null);
      setResultData(null);
      setResultJobId(null);
      return;
    }

    let cancelled = false;
    const currentJobId = selectedJobId;
    async function loadSelectedStatus() {
      try {
        const status = await getWasteStatus(currentJobId);
        if (!cancelled) {
          setSelectedJobStatus(status);
        }
      } catch (statusError) {
        if (!cancelled) {
          setError(normalizeWasteApiError(statusError, "Failed to load waste analysis job status"));
        }
      }
    }

    void loadSelectedStatus();
    return () => {
      cancelled = true;
    };
  }, [selectedJobId]);

  useEffect(() => {
    const liveSubmitted = submittedJobStatus?.status === "pending" || submittedJobStatus?.status === "running";
    const liveSelected = selectedJobStatus?.status === "pending" || selectedJobStatus?.status === "running";
    if (!liveSubmitted && !liveSelected && !liveHistoryJobs) {
      return;
    }

    const timer = setInterval(() => {
      void loadHistory(historyPage);

      const jobsToRefresh = Array.from(new Set([submittedJobId, selectedJobId].filter((jobId): jobId is string => Boolean(jobId))));
      for (const jobId of jobsToRefresh) {
        void getWasteStatus(jobId)
          .then((status) => {
            if (jobId === submittedJobId) {
              setSubmittedJobStatus(status);
            }
            if (jobId === selectedJobId) {
              setSelectedJobStatus(status);
            }
          })
          .catch(() => {});
      }
    }, 4000);

    return () => clearInterval(timer);
  }, [liveHistoryJobs, selectedJobId, selectedJobStatus, submittedJobId, submittedJobStatus]);

  useEffect(() => {
    if (!submittedJobId) return;
    if (!history.some((item) => item.job_id === submittedJobId)) {
      return;
    }
    setSelectedJobId(submittedJobId);
  }, [history, submittedJobId]);

  useEffect(() => {
    if (!submittedJobStatus || !acceptedHandoffRef.current) {
      return;
    }

    acceptedHandoffRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    acceptedHandoffRef.current.focus({ preventScroll: true });
  }, [submittedJobStatus?.job_id]);

  useEffect(() => {
    if (!detailJumpRequest || !selectedJobPanelRef.current || selectedJobId !== detailJumpRequest.jobId) {
      return;
    }

    selectedJobPanelRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    selectedJobPanelRef.current.focus({ preventScroll: true });
    setDetailJumpRequest(null);
  }, [detailJumpRequest, selectedJobId]);

  async function onRun() {
    setError(null);
    if (isSubmitting) {
      return;
    }
    if (!isDateRangeValid) {
      setError("Maximum allowed range is 90 days.");
      return;
    }
    if (selectedDeviceIds.length === 0) {
      setError("Select a scope with at least one accessible device.");
      return;
    }

    try {
      setIsSubmitting(true);
      setResultData(null);
      setResultJobId(null);
      const response = await runWasteAnalysis(
        buildWasteRunParams(
          {
            job_name: jobName || undefined,
            start_date: startDate,
            end_date: endDate,
            granularity,
          },
          normalizedScopeSelection,
          scopeCatalog,
        ),
      );
      setSubmittedJobId(response.job_id);
      setSubmittedJobStatus(response);
      setSelectedJobId(response.job_id);
      await loadHistory(0);
    } catch (submitError) {
      setError(normalizeWasteApiError(submitError, "Waste analysis could not be started."));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function onOpenResult(job: WasteJobSummary) {
    setError(null);
    setIsOpeningResult(true);
    const requestId = activeResultRequest.current + 1;
    activeResultRequest.current = requestId;
    try {
      const result = await getWasteResult(job.job_id);
      if (activeResultRequest.current === requestId) {
        setResultData((result || null) as WasteResultPayload | null);
        setResultJobId(job.job_id);
      }
    } catch (resultError) {
      setError(normalizeWasteApiError(resultError, "Failed to load waste analysis result."));
    } finally {
      if (activeResultRequest.current === requestId) {
        setIsOpeningResult(false);
      }
    }
  }

  async function onDownload(job: WasteJobSummary) {
    setError(null);
    setIsDownloading(true);
    try {
      const download = await downloadWastePdf(job.job_id);
      const objectUrl = URL.createObjectURL(download.blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = download.filename;
      link.rel = "noopener noreferrer";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objectUrl);
    } catch (downloadError) {
      setError(normalizeWasteApiError(downloadError, "Failed to download waste analysis PDF."));
    } finally {
      setIsDownloading(false);
    }
  }

  const acceptedStatus = submittedJobStatus;
  const acceptedStatusIsLive = acceptedStatus ? !isWasteJobTerminal(acceptedStatus.status) : false;
  const liveAcceptedStatus = acceptedStatusIsLive ? acceptedStatus : null;
  const acceptedSummary = [
    selectedScopeSummary,
    `${startDate} → ${endDate}`,
  ].join(" · ");

  const selectedStatus = selectedJobStatus ?? selectedJob;
  const selectedFailureSummary = selectedStatus
    ? getJobFailureSummary(selectedStatus, "This waste analysis could not be completed.")
    : null;
  const selectedStatusSummary = selectedStatus ? formatJobStatusSummary(selectedStatus) : "";
  const selectedStatusLabel = getUserFacingJobStatusLabelWithCoverage(selectedStatus);
  const selectedIsResultReady = Boolean(selectedStatus?.result_ready);
  const selectedIsDownloadReady = Boolean(selectedStatus?.download_ready);
  const selectedStatePresentation = selectedStatus
    ? getWasteJobStatePresentation(selectedStatus, selectedFailureSummary)
    : null;
  const selectedResultIsLoading = Boolean(
    selectedStatus?.result_ready &&
      (isOpeningResult || resultJobId !== selectedStatus.job_id),
  );

  useEffect(() => {
    if (!selectedStatus?.job_id) return;

    if (!selectedStatus.result_ready) {
      if (resultJobId && resultJobId !== selectedStatus.job_id) {
        setResultData(null);
        setResultJobId(null);
      }
      return;
    }

    if (resultJobId === selectedStatus.job_id || isOpeningResult) {
      return;
    }

    void onOpenResult(selectedStatus);
  }, [isOpeningResult, resultJobId, selectedStatus]);

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Waste Energy Analysis</h1>
        <p className="text-gray-600">Run waste analysis in the background and return to a durable history when results are ready.</p>
      </div>

      {tariffBanner ? (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {tariffBanner}
        </div>
      ) : null}

      {error ? (
        <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      ) : null}

      <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        {thresholdNotice}
      </div>

      <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
        <p>{EXCLUSIVE_LOSS_BUCKET_HELP}</p>
        <p className="mt-1">{WASTE_ANALYSIS_POLICY_HELP}</p>
      </div>

      <div className="space-y-4 rounded-xl border bg-white p-4 sm:p-5">
        {liveAcceptedStatus ? (
          <div
            ref={acceptedHandoffRef}
            tabIndex={-1}
            className="space-y-4 outline-none"
          >
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Waste Analysis Running</h2>
              <p className="mt-1 text-sm text-gray-600">
                Your waste analysis was accepted. Track the live status here or jump straight to the history section below.
              </p>
            </div>
          <AsyncJobHandoffCard
              title="Waste analysis started"
              backgroundMessage="Processing continues in the background. You can continue using the platform while this runs."
              historyLabel="Track in Waste Analysis History"
              historyHref="#waste-analysis-history"
              summary={acceptedSummary}
              status={{
                status: liveAcceptedStatus.status,
                progress: liveAcceptedStatus.progress_pct,
                phase_label: liveAcceptedStatus.phase_label ?? liveAcceptedStatus.stage,
                estimated_completion_seconds: liveAcceptedStatus.estimated_completion_seconds ?? undefined,
                result_ready: liveAcceptedStatus.result_ready,
                artifact_ready: liveAcceptedStatus.artifact_ready,
                download_ready: liveAcceptedStatus.download_ready,
                error_code: liveAcceptedStatus.error_code,
                error_message: liveAcceptedStatus.error_message,
              }}
              statusBadges={[
                liveAcceptedStatus.requested_device_count ? `${liveAcceptedStatus.requested_device_count} devices selected` : "",
                liveAcceptedStatus.created_at ? `Started ${formatIST(liveAcceptedStatus.created_at, "just now")}` : "",
              ].filter(Boolean)}
              primaryActionLabel="Configure another analysis"
              onPrimaryAction={() => {
                setSubmittedJobId(null);
                setSubmittedJobStatus(null);
              }}
              footerMessage={
                liveAcceptedStatus.download_ready
                  ? "Results and PDF actions are available from Waste Analysis History below."
                  : liveAcceptedStatus.result_ready
                  ? "Results are ready. Open them from Waste Analysis History below."
                  : "You do not need to stay on this page. Track progress in Waste Analysis History below."
              }
            />
          </div>
        ) : (
          <>
            <h2 className="text-lg font-semibold text-gray-900">Configure Analysis</h2>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm text-gray-700">Granularity</label>
                <select
                  value={granularity}
                  onChange={(e) => setGranularity(e.target.value as WasteGranularity)}
                  className="w-full rounded-lg border px-3 py-2"
                >
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>
            </div>

            <div>
              <label className="mb-2 block text-sm text-gray-700">Date Range</label>
              <DateRangeSelector
                onRangeChange={(start, end) => { setStartDate(start); setEndDate(end); }}
                initialRange={defaultRange}
                maxDays={90}
                maxDaysMessage="Maximum allowed range is 90 days."
                onValidationChange={setIsDateRangeValid}
              />
            </div>

            <div>
              <label className="mb-1 block text-sm text-gray-700">Scope</label>
              <DeviceScopeSelector
                catalog={scopeCatalog}
                value={normalizedScopeSelection}
                onChange={setScopeSelection}
                disabled={isSubmitting}
              />
              <p className="mt-2 text-xs text-slate-600">{selectedScopeSummary}</p>
            </div>

            <div>
              <label className="mb-1 block text-sm text-gray-700">Job Name (optional)</label>
              <input
                value={jobName}
                onChange={(e) => setJobName(e.target.value)}
                className="w-full rounded-lg border px-3 py-2"
                placeholder="Weekly Waste Review"
              />
            </div>

            <button
              onClick={onRun}
              disabled={isSubmitting || !isDateRangeValid}
              className="rounded-lg bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:bg-blue-300"
            >
              {isSubmitting ? "Starting waste analysis..." : "Run Waste Analysis"}
            </button>
          </>
        )}
      </div>

      <div id="waste-analysis-history" className="space-y-6 scroll-mt-6">
        <div className="rounded-xl border bg-white p-4 sm:p-5">
          <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Waste Analysis History</h2>
              <p className="mt-1 text-sm text-gray-500">Recent jobs stay available here while they queue, run, complete, or fail.</p>
            </div>
            <button
              type="button"
              onClick={() => void loadHistory(historyPage)}
              disabled={historyLoading}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
            >
              {historyLoading ? "Refreshing..." : "Refresh"}
            </button>
          </div>

          {history.length === 0 ? (
            <div className="text-sm text-gray-500">No waste analysis jobs yet.</div>
          ) : (
            <div className="space-y-5">
              <div className="space-y-3 md:hidden">
                {history.map((job) => (
                  <WasteHistoryMobileCard
                    key={job.job_id}
                    job={job}
                    isSelected={selectedJobId === job.job_id}
                    onOpen={() => openWasteJobDetails(job.job_id)}
                    onDownload={() => void onDownload(job)}
                    isDownloading={isDownloading}
                  />
                ))}
              </div>

              <div className="hidden overflow-x-auto rounded-xl border border-slate-200 md:block">
                <table className="min-w-full divide-y divide-slate-200">
                  <thead className="bg-slate-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium uppercase text-slate-500">Analysis</th>
                      <th className="px-6 py-3 text-left text-xs font-medium uppercase text-slate-500">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-medium uppercase text-slate-500">Progress</th>
                      <th className="px-6 py-3 text-left text-xs font-medium uppercase text-slate-500">Created</th>
                      <th className="px-6 py-3 text-left text-xs font-medium uppercase text-slate-500">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 bg-white">
                    {history.map((job) => {
                      const isSelected = selectedJobId === job.job_id;
                      const summary = job.phase_label?.trim() || job.stage?.trim() || job.status;
                      return (
                        <tr
                          key={job.job_id}
                          onClick={() => openWasteJobDetails(job.job_id)}
                          className={`${isSelected ? "bg-slate-50" : ""} cursor-pointer`}
                        >
                          <td className="px-6 py-4 text-sm text-slate-900">
                            <div className="font-semibold">{job.job_name || `Waste analysis ${job.job_id.slice(0, 8)}`}</div>
                            <div className="mt-1 text-xs text-slate-500">
                              {job.requested_device_count ? `${job.requested_device_count} devices` : "All accessible devices"}
                            </div>
                          </td>
                          <td className="px-6 py-4">
                            <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusTone(job.status)}`}>
                              {getUserFacingJobStatusLabelWithCoverage(job)}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-sm text-slate-500">
                            <div className="font-medium text-slate-700">{job.progress_pct}%</div>
                            <div className="mt-1 text-xs text-slate-500">{summary}</div>
                          </td>
                          <td className="px-6 py-4 text-sm text-slate-500">
                            {job.created_at ? formatIST(job.created_at, "—") : "—"}
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex flex-wrap items-center gap-3">
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  openWasteJobDetails(job.job_id);
                                }}
                                className="text-sm font-medium text-blue-700 hover:text-blue-900"
                              >
                                View details
                              </button>
                              {job.download_ready ? (
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    void onDownload(job);
                                  }}
                                  disabled={isDownloading}
                                  className="text-sm font-medium text-slate-700 hover:text-slate-900 disabled:opacity-60"
                                >
                                  {isDownloading ? "Preparing download..." : "Download PDF"}
                                </button>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="flex flex-col gap-3 text-sm text-slate-600 sm:flex-row sm:items-center sm:justify-between">
                <div>Page {historyPage + 1}</div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => void loadHistory(Math.max(0, historyPage - 1))}
                    disabled={historyLoading || historyPage === 0}
                    className="rounded-lg border border-slate-200 px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    onClick={() => void loadHistory(historyPage + 1)}
                    disabled={historyLoading || !hasMoreHistory}
                    className="rounded-lg border border-slate-200 px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Next
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        <div
          ref={selectedJobPanelRef}
          tabIndex={-1}
          className="rounded-xl border bg-white p-4 outline-none sm:p-5"
        >
          <h2 className="text-lg font-semibold text-gray-900">Selected Waste Analysis</h2>
          {!selectedStatus ? (
            <p className="mt-3 text-sm text-gray-500">Select a job from Waste Analysis History to review progress, result readiness, and downloads.</p>
          ) : (
            <div className="mt-4 space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Selected job</div>
                  <div className="text-sm font-semibold text-slate-900">{selectedStatus.job_name || `Waste analysis ${selectedStatus.job_id.slice(0, 8)}`}</div>
                  <div className="mt-1 font-mono text-xs text-slate-500">{selectedStatus.job_id}</div>
                  <div className="mt-1 text-xs text-slate-500">
                    {selectedStatus.scope === "selected" && selectedStatus.requested_device_count
                      ? `${selectedStatus.requested_device_count} devices selected`
                      : "All accessible devices"}
                    {selectedStatus.start_date && selectedStatus.end_date ? ` · ${selectedStatus.start_date} → ${selectedStatus.end_date}` : ""}
                  </div>
                </div>
                <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusTone(selectedStatus.status)}`}>
                  {selectedStatusLabel}
                </span>
              </div>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="text-sm font-medium text-slate-900">{selectedStatusSummary || "Waiting for the latest status"}</div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-200">
                  <div
                    className="h-full rounded-full bg-[linear-gradient(135deg,#0ea5e9,#2563eb)] transition-all duration-500"
                    style={{ width: `${Math.max(0, Math.min(100, selectedStatus.progress_pct ?? 0))}%` }}
                  />
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-600">
                  {selectedStatus.phase_label ? <span>Current step: {selectedStatus.phase_label}</span> : null}
                  {selectedStatus.estimated_completion_seconds != null ? <span>ETA: {Math.round(selectedStatus.estimated_completion_seconds / 60)} min</span> : null}
                  {selectedStatus.result_ready ? <span>Result ready</span> : null}
                  {selectedStatus.download_ready ? <span>Download ready</span> : null}
                  {selectedStatus.created_at ? <span>Created {formatIST(selectedStatus.created_at, "-")}</span> : null}
                  {selectedStatus.started_at ? <span>Started {formatIST(selectedStatus.started_at, "-")}</span> : null}
                  {selectedStatus.completed_at ? <span>Completed {formatIST(selectedStatus.completed_at, "-")}</span> : null}
                </div>
              </div>

              {selectedStatePresentation ? (
                <div className={`rounded-lg border px-4 py-4 ${selectedStatePresentation.toneClassName}`}>
                  <div className="text-sm font-semibold">{selectedStatePresentation.headline}</div>
                  <div className="mt-2 text-sm">{selectedStatePresentation.summary}</div>
                  <div className="mt-3 text-sm">{selectedStatePresentation.nextAction}</div>
                  {selectedStatePresentation.technicalDetail ? (
                    <div className="mt-3 border-t border-current/15 pt-3 text-xs opacity-80">
                      Technical detail: {selectedStatePresentation.technicalDetail}
                    </div>
                  ) : null}
                </div>
              ) : null}

              {selectedIsDownloadReady ? (
                <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
                  <button
                    type="button"
                    onClick={() => void onDownload(selectedStatus)}
                    disabled={isDownloading}
                    className="min-h-11 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-400"
                  >
                    {isDownloading ? "Preparing download..." : "Download PDF"}
                  </button>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>

      {selectedResultIsLoading ? (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
          Loading waste analysis result...
        </div>
      ) : resultData && resultJobId === selectedStatus?.job_id ? (
        <div className="space-y-4 rounded-xl border bg-white p-4 sm:p-5">
          <h2 className="text-lg font-semibold text-gray-900">Waste Analysis Result</h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-lg border border-slate-200 p-3">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Total Waste Cost</div>
              <div className="mt-2 text-lg font-semibold text-slate-900">
                {resultData.total_waste_cost != null ? formatCurrencyValue(Number(resultData.total_waste_cost), "INR") : "N/A"}
              </div>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Total Energy Cost</div>
              <div className="mt-2 text-lg font-semibold text-slate-900">
                {resultData.total_energy_cost != null ? formatCurrencyValue(Number(resultData.total_energy_cost), "INR") : "N/A"}
              </div>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Total Energy</div>
              <div className="mt-2 text-lg font-semibold text-slate-900">
                {resultData.total_energy_kwh != null ? formatEnergyKwh(Number(resultData.total_energy_kwh)) : "N/A"}
              </div>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Idle Energy</div>
              <div className="mt-2 text-lg font-semibold text-slate-900">
                {resultData.total_idle_kwh != null ? formatEnergyKwh(Number(resultData.total_idle_kwh)) : "N/A"}
              </div>
            </div>
          </div>

          {resultData.quality_gate_passed === false ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              This result is available, but quality checks flagged issues for one or more devices. Review the notes below before acting on the summary.
            </div>
          ) : null}

          {resultData.insights && resultData.insights.length > 0 ? (
            <div className="rounded-lg border border-slate-200 p-4">
              <div className="text-sm font-semibold text-slate-900">Key Insights</div>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                {resultData.insights.map((insight) => (
                  <li key={insight}>{insight}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {resultData.warnings && resultData.warnings.length > 0 ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <div className="text-sm font-semibold text-amber-900">Warnings</div>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-800">
                {resultData.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="space-y-4">
            {(resultData.device_summaries || []).map((device) => (
              <div key={device.device_id} className="rounded-lg border p-3">
                <div className="mb-2 font-medium text-gray-900">{device.device_name || device.device_id}</div>
                <div className="space-y-3 md:hidden">
                  <WastageMobileItem
                    label="Idle Running"
                    duration={device.idle?.duration_sec ?? device.idle_duration_sec}
                    kwh={device.idle?.energy_kwh ?? device.idle_energy_kwh}
                    cost={device.idle?.cost ?? device.idle_cost}
                    skippedReason={device.idle?.skipped_reason}
                    pfEstimated={Boolean(device.idle?.pf_estimated)}
                    configSource={device.idle?.config_source}
                  />
                  <WastageMobileItem
                    label="Off-Hours Running"
                    duration={device.off_hours?.duration_sec ?? device.offhours_duration_sec}
                    kwh={device.off_hours?.energy_kwh ?? device.offhours_energy_kwh}
                    cost={device.off_hours?.cost ?? device.offhours_cost}
                    skippedReason={device.off_hours?.skipped_reason ?? device.offhours_skipped_reason}
                    pfEstimated={Boolean(device.off_hours?.pf_estimated ?? device.offhours_pf_estimated)}
                    configSource={device.off_hours?.config_source}
                  />
                  <WastageMobileItem
                    label="Overconsumption"
                    duration={device.overconsumption?.duration_sec ?? device.overconsumption_duration_sec}
                    kwh={device.overconsumption?.energy_kwh ?? device.overconsumption_kwh}
                    cost={device.overconsumption?.cost ?? device.overconsumption_cost}
                    skippedReason={device.overconsumption?.skipped_reason ?? device.overconsumption_skipped_reason}
                    pfEstimated={Boolean(device.overconsumption?.pf_estimated ?? device.overconsumption_pf_estimated)}
                    configSource={device.overconsumption?.config_source}
                  />
                </div>
                <div className="hidden overflow-x-auto md:block">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-gray-500">
                        <th className="py-2 pr-4">Category</th>
                        <th className="py-2 pr-4">Duration</th>
                        <th className="py-2 pr-4">Energy</th>
                        <th className="py-2 pr-4">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      <WastageRow
                        label="Idle Running"
                        duration={device.idle?.duration_sec ?? device.idle_duration_sec}
                        kwh={device.idle?.energy_kwh ?? device.idle_energy_kwh}
                        cost={device.idle?.cost ?? device.idle_cost}
                        skippedReason={device.idle?.skipped_reason}
                        pfEstimated={Boolean(device.idle?.pf_estimated)}
                        configSource={device.idle?.config_source}
                      />
                      <WastageRow
                        label="Off-Hours Running"
                        duration={device.off_hours?.duration_sec ?? device.offhours_duration_sec}
                        kwh={device.off_hours?.energy_kwh ?? device.offhours_energy_kwh}
                        cost={device.off_hours?.cost ?? device.offhours_cost}
                        skippedReason={device.off_hours?.skipped_reason ?? device.offhours_skipped_reason}
                        pfEstimated={Boolean(device.off_hours?.pf_estimated ?? device.offhours_pf_estimated)}
                        configSource={device.off_hours?.config_source}
                      />
                      <WastageRow
                        label="Overconsumption"
                        duration={device.overconsumption?.duration_sec ?? device.overconsumption_duration_sec}
                        kwh={device.overconsumption?.energy_kwh ?? device.overconsumption_kwh}
                        cost={device.overconsumption?.cost ?? device.overconsumption_cost}
                        skippedReason={device.overconsumption?.skipped_reason ?? device.overconsumption_skipped_reason}
                        pfEstimated={Boolean(device.overconsumption?.pf_estimated ?? device.overconsumption_pf_estimated)}
                        configSource={device.overconsumption?.config_source}
                      />
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
