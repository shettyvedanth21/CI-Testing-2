"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  getReportHistory,
  ReportHistoryItem,
  getSchedules,
  deleteSchedule,
  createSchedule,
  ScheduleListItem,
  ScheduleParams,
  updateSchedule,
  getReportDownload,
  getReportResult,
  getReportStatus,
  type ReportStatus,
} from "@/lib/reportApi";
import { authApi, type PlantProfile } from "@/lib/authApi";
import { getDevices, Device } from "@/lib/deviceApi";
import { formatIST } from "@/lib/utils";
import { PageHeader } from "@/components/ui/page-scaffold";
import { DeviceScopeSelector } from "@/components/reports/DeviceScopeSelector";
import { usePermissions } from "@/hooks/usePermissions";
import { ReadOnlyBanner } from "@/components/auth/ReadOnlyBanner";
import { useAuth } from "@/lib/authContext";
import { useTenantStore } from "@/lib/tenantStore";
import { resolveScopedTenantId, resolveVisiblePlants } from "@/lib/orgScope";
import {
  getEmptyReportHistoryMessage,
  getEmptyScheduleMessage,
  getReportPageSubtitle,
  getReportScopeLabel,
  getReportScopeHint,
  isPlantScopedReportRole,
} from "@/lib/reportScope";
import {
  formatJobSeconds,
} from "@/lib/asyncJobPresentation";
import {
  buildDeviceScopeCatalog,
  getDeviceScopeSummary,
  normalizeDeviceScopeSelection,
  resolveDeviceIdsForSelection,
  type DeviceScopeSelection,
} from "@/lib/deviceScopeSelection";
import { buildReportScheduleParams } from "@/lib/reportScheduleScope";
import { getReportStatePresentation } from "@/lib/reportPresentation";
import { getTelemetryCoverageTone } from "@/lib/telemetryCoverage";

type TabType = "history" | "schedules";
type UserFacingReportStatus = ReportStatus["status"];
const REPORT_HISTORY_PAGE_SIZE = 5;
type ReportHistoryDetail = Omit<ReportHistoryItem, "status"> & {
  status: UserFacingReportStatus;
  progress?: number | null;
  phase?: string | null;
  phase_label?: string | null;
  phase_progress?: number | null;
  queue_position?: number | null;
  estimated_wait_seconds?: number | null;
  estimated_completion_seconds?: number | null;
  estimate_quality?: "low" | "medium" | "high" | null;
  result_ready?: boolean;
  artifact_ready?: boolean;
  download_ready?: boolean;
  result_url?: string | null;
  download_url?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

type ReportPreviewModel = {
  headline: string;
  metrics: Array<{ label: string; value: string }>;
  notes: string[];
  warnings: string[];
};

function formatCompactNumber(value: number | null | undefined, suffix = ""): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}${suffix}`;
}

function buildReportPreview(result: unknown): ReportPreviewModel | null {
  if (!result || typeof result !== "object") return null;
  const data = result as Record<string, unknown>;

  if (typeof data.summary === "object" && data.summary !== null) {
    const summary = data.summary as Record<string, unknown>;
    const currency = typeof summary.currency === "string" ? summary.currency : "INR";
    const insights = Array.isArray(data.insights) ? data.insights.filter((item): item is string => typeof item === "string") : [];
    const warnings = Array.isArray(data.warnings) ? data.warnings.filter((item): item is string => typeof item === "string") : [];

    return {
      headline: "Report summary",
      metrics: [
        { label: "Total energy", value: formatCompactNumber(summary.total_kwh as number | null | undefined, " kWh") },
        { label: "Peak demand", value: formatCompactNumber(summary.peak_demand_kw as number | null | undefined, " kW") },
        { label: "Load factor", value: formatCompactNumber(summary.load_factor_pct as number | null | undefined, "%") },
        { label: "Estimated cost", value: typeof summary.total_cost === "number" ? `${currency} ${formatCompactNumber(summary.total_cost)}` : "—" },
      ],
      notes: insights.slice(0, 3),
      warnings,
    };
  }

  if (Array.isArray(data.devices)) {
    const devices = data.devices.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
    const totalKwh = devices.reduce((sum, device) => sum + (typeof device.total_kwh === "number" ? device.total_kwh : 0), 0);
    const telemetryReadyCount = devices.filter((device) => typeof device.total_kwh === "number").length;
    const warningSet = new Set<string>();
    devices.forEach((device) => {
      if (Array.isArray(device.warnings)) {
        device.warnings.forEach((warning) => {
          if (typeof warning === "string" && warning.trim()) warningSet.add(warning);
        });
      }
      if (typeof device.error === "string" && device.error.trim()) {
        warningSet.add(device.error);
      }
    });

    return {
      headline: "Report output",
      metrics: [
        { label: "Devices in report", value: String(devices.length) },
        { label: "Devices with telemetry", value: String(telemetryReadyCount) },
        { label: "Measured energy", value: formatCompactNumber(totalKwh, " kWh") },
      ],
      notes: telemetryReadyCount > 0 ? ["Detailed report data is ready for download from this history view."] : [],
      warnings: Array.from(warningSet).slice(0, 4),
    };
  }

  return null;
}

function normalizeReportStatus(status?: string | null): UserFacingReportStatus {
  if (status === "completed" || status === "failed" || status === "running") {
    return status;
  }
  return "pending";
}

function isReportDownloadReady(item: {
  artifact_ready?: boolean | null;
  download_ready?: boolean | null;
  download_url?: string | null;
}): boolean {
  return Boolean(item.artifact_ready || item.download_ready || item.download_url);
}

function mergeReportDetail(item: ReportHistoryItem, status?: ReportStatus | null): ReportHistoryDetail {
  return {
    ...item,
    ...(status ?? {}),
    report_id: status?.report_id ?? item.report_id,
    status: normalizeReportStatus(status?.status ?? item.status),
    report_type: item.report_type,
    progress: status?.progress ?? item.progress,
    phase: status?.phase ?? item.phase,
    phase_label: status?.phase_label ?? item.phase_label,
    phase_progress: status?.phase_progress ?? item.phase_progress,
    queue_position: status?.queue_position ?? item.queue_position,
    estimated_wait_seconds: status?.estimated_wait_seconds ?? item.estimated_wait_seconds,
    estimated_completion_seconds: status?.estimated_completion_seconds ?? item.estimated_completion_seconds,
    estimate_quality: status?.estimate_quality ?? item.estimate_quality,
    result_ready: status?.result_ready ?? item.result_ready,
    artifact_ready: status?.artifact_ready ?? item.artifact_ready,
    download_ready: status?.download_ready ?? item.download_ready,
    result_url: status?.result_url ?? item.result_url,
    download_url: status?.download_url ?? item.download_url,
    error_code: status?.error_code ?? item.error_code,
    error_message: status?.error_message ?? item.error_message,
    created_at: status?.created_at ?? item.created_at,
    started_at: status?.started_at ?? item.started_at,
    completed_at: status?.completed_at ?? item.completed_at,
  };
}

function ReportHistoryMobileCard({
  item,
  isSelected,
  onOpen,
  onDownload,
  downloading,
  statusBadge,
  createdAt,
  progressSummary,
}: {
  item: ReportHistoryItem;
  isSelected: boolean;
  onOpen: () => void;
  onDownload: () => void;
  downloading: boolean;
  statusBadge: React.ReactNode;
  createdAt: string;
  progressSummary: string;
}) {
  const itemDownloadReady = isReportDownloadReady(item);
  return (
    <article
      onClick={onOpen}
      className={`rounded-2xl border p-4 shadow-sm transition ${
        isSelected ? "border-blue-200 bg-blue-50/40" : "border-slate-200 bg-white"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Report</p>
          <p className="mt-1 text-base font-semibold capitalize text-slate-900">{item.report_type}</p>
          <p className="mt-1 text-xs text-slate-500">{createdAt}</p>
        </div>
        {statusBadge}
      </div>
      <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-medium text-slate-900">
            {item.progress != null ? `${item.progress}% complete` : "Progress unavailable"}
          </div>
          <div className="text-xs text-slate-500">{progressSummary}</div>
        </div>
      </div>
      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <button
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
          className="inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          View details
        </button>
        {itemDownloadReady ? (
          <button
            onClick={(event) => {
              event.stopPropagation();
              onDownload();
            }}
            disabled={downloading}
            className="inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {downloading ? "Downloading..." : "Download"}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function ReportScheduleMobileCard({
  schedule,
  canGenerateReport,
  onEdit,
  onDeactivate,
  statusBadge,
  nextRun,
}: {
  schedule: ScheduleListItem;
  canGenerateReport: boolean;
  onEdit: () => void;
  onDeactivate: () => void;
  statusBadge: React.ReactNode;
  nextRun: string;
}) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Schedule</p>
          <p className="mt-1 text-base font-semibold capitalize text-slate-900">{schedule.report_type}</p>
        </div>
        {statusBadge}
      </div>
      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-xl bg-slate-50 p-3">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Frequency</dt>
          <dd className="mt-1 text-sm font-semibold capitalize text-slate-900">{schedule.frequency}</dd>
        </div>
        <div className="rounded-xl bg-slate-50 p-3">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Devices</dt>
          <dd className="mt-1 text-sm font-semibold text-slate-900">{schedule.params_template?.device_ids?.length || 0} devices</dd>
        </div>
        <div className="rounded-xl bg-slate-50 p-3 sm:col-span-2">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Next Run</dt>
          <dd className="mt-1 text-sm font-semibold text-slate-900">{nextRun}</dd>
        </div>
      </dl>
      {canGenerateReport && schedule.is_active ? (
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <button
            onClick={onEdit}
            className="inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Edit
          </button>
          <button
            onClick={onDeactivate}
            className="inline-flex min-h-11 items-center justify-center rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm font-medium text-rose-700 hover:bg-rose-100"
          >
            Deactivate
          </button>
        </div>
      ) : null}
    </article>
  );
}

export default function ReportsPage() {
  const { canGenerateReport } = usePermissions();
  const { me } = useAuth();
  const { selectedTenantId } = useTenantStore();
  const [activeTab, setActiveTab] = useState<TabType>("history");
  const [history, setHistory] = useState<ReportHistoryItem[]>([]);
  const [schedules, setSchedules] = useState<ScheduleListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<ScheduleListItem | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [plants, setPlants] = useState<PlantProfile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyPage, setHistoryPage] = useState(0);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [selectedReportDetail, setSelectedReportDetail] = useState<ReportHistoryDetail | null>(null);
  const [selectedReportResult, setSelectedReportResult] = useState<ReportPreviewModel | null>(null);
  const [selectedReportResultLoading, setSelectedReportResultLoading] = useState(false);
  const [selectedReportResultError, setSelectedReportResultError] = useState<string | null>(null);
  const [detailJumpTargetId, setDetailJumpTargetId] = useState<string | null>(null);
  const selectedReportPanelRef = useRef<HTMLDivElement | null>(null);
  const isPlantScopedRole = isPlantScopedReportRole(me?.user.role);
  const reportScopeHint = getReportScopeHint(me?.user.role);
  const scopedOrgId = resolveScopedTenantId(me, selectedTenantId);
  const visiblePlants = useMemo(() => resolveVisiblePlants(me, plants), [me, plants]);
  const scopeCatalog = useMemo(
    () => buildDeviceScopeCatalog(devices, visiblePlants),
    [devices, visiblePlants],
  );
  const [scopeSelection, setScopeSelection] = useState<DeviceScopeSelection>({
    mode: "all",
    plantId: null,
    deviceIds: [],
  });
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
  const selectedReport = useMemo(
    () => history.find((item) => item.report_id === selectedReportId) ?? history[0] ?? null,
    [history, selectedReportId],
  );

  const openReportDetails = useCallback((reportId: string) => {
    setSelectedReportId(reportId);
    setDetailJumpTargetId(reportId);
  }, []);

  const [formData, setFormData] = useState<{
    report_type: "consumption" | "comparison";
    frequency: "daily" | "weekly" | "monthly";
    group_by: "daily" | "weekly";
  }>({
    report_type: "consumption",
    frequency: "daily",
    group_by: "daily",
  });

  const resetScheduleComposer = useCallback(() => {
    setShowModal(false);
    setEditingSchedule(null);
    setFormData({
      report_type: "consumption",
      frequency: "daily",
      group_by: "daily",
    });
    setScopeSelection({
      mode: "all",
      plantId: null,
      deviceIds: [],
    });
  }, []);

  const loadHistory = useCallback(async (tenantId: string, page = 0) => {
    setHistoryLoading(true);
    try {
      const historyData = await getReportHistory(tenantId, {
        limit: REPORT_HISTORY_PAGE_SIZE + 1,
        offset: page * REPORT_HISTORY_PAGE_SIZE,
      });
      setHistoryPage(page);
      setHasMoreHistory(historyData.reports.length > REPORT_HISTORY_PAGE_SIZE);
      setHistory(historyData.reports.slice(0, REPORT_HISTORY_PAGE_SIZE));
      return historyData.reports;
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedTenantId) {
      setHistory([]);
      setHistoryPage(0);
      setHasMoreHistory(false);
      setSchedules([]);
      setDevices([]);
      setPlants([]);
      setLoading(false);
      return;
    }

    async function fetchData() {
      const tenantId = selectedTenantId;
      if (!tenantId) {
        return;
      }
      try {
        const [, schedulesData, devicesData] = await Promise.all([
          loadHistory(tenantId, 0),
          getSchedules(tenantId),
          getDevices(),
        ]);
        setSchedules(schedulesData.schedules);
        setDevices(devicesData);
        if (scopedOrgId) {
          setPlants(await authApi.listPlants(scopedOrgId));
        } else {
          setPlants([]);
        }
      } catch {
        setToast({ message: "Failed to load reports data", type: "error" });
        setTimeout(() => setToast(null), 3000);
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [loadHistory, scopedOrgId, selectedTenantId]);

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
    if (history.length === 0) {
      if (selectedReportId !== null) {
        setSelectedReportId(null);
      }
      setSelectedReportDetail(null);
      return;
    }

    const hasSelected = selectedReportId ? history.some((item) => item.report_id === selectedReportId) : false;
    if (!hasSelected) {
      setSelectedReportId(history[0].report_id);
    }
  }, [history, selectedReportId]);

  useEffect(() => {
    if (!selectedReportId || !selectedTenantId) {
      setSelectedReportDetail(null);
      return;
    }

    const item = history.find((entry) => entry.report_id === selectedReportId);
    if (!item) {
      setSelectedReportDetail(null);
      return;
    }

    let cancelled = false;
    setSelectedReportDetail(mergeReportDetail(item));

    const loadStatus = async () => {
      try {
        const status = await getReportStatus(selectedReportId, selectedTenantId);
        if (!cancelled) {
          setSelectedReportDetail(mergeReportDetail(item, status));
        }
      } catch {
        if (!cancelled) {
          setSelectedReportDetail(mergeReportDetail(item));
        }
      }
    };

    void loadStatus();
    return () => {
      cancelled = true;
    };
  }, [history, selectedReportId, selectedTenantId]);

  useEffect(() => {
    if (!selectedTenantId || !selectedReportDetail?.report_id || selectedReportDetail.status !== "completed" || !selectedReportDetail.result_ready) {
      setSelectedReportResult(null);
      setSelectedReportResultError(null);
      setSelectedReportResultLoading(false);
      return;
    }

    let cancelled = false;
    setSelectedReportResultLoading(true);
    setSelectedReportResultError(null);

    void getReportResult(selectedReportDetail.report_id, selectedTenantId)
      .then((result) => {
        if (cancelled) return;
        setSelectedReportResult(buildReportPreview(result));
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setSelectedReportResult(null);
        setSelectedReportResultError(error instanceof Error ? error.message : "Unable to load report details");
      })
      .finally(() => {
        if (!cancelled) {
          setSelectedReportResultLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedReportDetail, selectedTenantId]);

  useEffect(() => {
    if (!detailJumpTargetId || selectedReportDetail?.report_id !== detailJumpTargetId || !selectedReportPanelRef.current) {
      return;
    }

    selectedReportPanelRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    selectedReportPanelRef.current.focus({ preventScroll: true });
    setDetailJumpTargetId(null);
  }, [detailJumpTargetId, selectedReportDetail]);

  useEffect(() => {
    if (activeTab !== "history" || !selectedTenantId) {
      return;
    }

    const hasLiveJobs = history.some((item) => item.status === "pending" || item.status === "running");
    if (!hasLiveJobs) {
      return;
    }

    const interval = setInterval(() => {
      void loadHistory(selectedTenantId, historyPage).catch(() => {
        // Keep the last successful history state if a background refresh fails.
      });
    }, 5000);

    return () => clearInterval(interval);
  }, [activeTab, history, historyPage, loadHistory, selectedTenantId]);

  const showToast = (message: string, type: "success" | "error") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const handleCreateSchedule = async () => {
    if (selectedDeviceIds.length === 0) {
      showToast("Please select a scope with at least one accessible device", "error");
      return;
    }

    setSubmitting(true);
    try {
      const params: ScheduleParams = buildReportScheduleParams(formData, normalizedScopeSelection, scopeCatalog);
      if (!selectedTenantId) {
        throw new Error("Select an organisation before creating a schedule");
      }
      if (editingSchedule) {
        await updateSchedule(editingSchedule.schedule_id, selectedTenantId, {
          report_type: params.report_type,
          frequency: params.frequency,
          params_template: params.params_template,
        });
      } else {
        await createSchedule(selectedTenantId, params);
      }
      const schedulesData = await getSchedules(selectedTenantId);
      setSchedules(schedulesData.schedules);
      resetScheduleComposer();
      showToast(editingSchedule ? "Schedule updated successfully" : "Schedule created successfully", "success");
    } catch (error) {
      console.error("Failed to save schedule:", error);
      showToast(error instanceof Error ? error.message : "Failed to save schedule", "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleEditSchedule = (schedule: ScheduleListItem) => {
    setEditingSchedule(schedule);
    setFormData({
      report_type: schedule.report_type as "consumption" | "comparison",
      frequency: schedule.frequency as "daily" | "weekly" | "monthly",
      group_by: schedule.params_template?.group_by ?? "daily",
    });
    setScopeSelection({
      mode: "devices",
      plantId: null,
      deviceIds: schedule.params_template?.device_ids ?? [],
    });
    setShowModal(true);
  };

  const handleDeleteSchedule = async (scheduleId: string) => {
    if (!confirm("Are you sure you want to deactivate this schedule?")) return;
    
    try {
      if (!selectedTenantId) {
        throw new Error("Select an organisation before managing schedules");
      }
      await deleteSchedule(scheduleId, selectedTenantId);
      const schedulesData = await getSchedules(selectedTenantId);
      setSchedules(schedulesData.schedules);
      showToast("Schedule deactivated", "success");
    } catch (error) {
      console.error("Failed to delete schedule:", error);
      showToast(error instanceof Error ? error.message : "Failed to deactivate schedule", "error");
    }
  };

  const handleDownload = async (reportId: string) => {
    try {
      setDownloadingId(reportId);
      if (!selectedTenantId) {
        throw new Error("Select an organisation before downloading reports");
      }
      const blob = await getReportDownload(reportId, selectedTenantId);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `energy_report_${reportId}.pdf`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      showToast("Download started", "success");
    } catch (error) {
      console.error("Failed to download report:", error);
      showToast(error instanceof Error ? error.message : "Failed to download report", "error");
    } finally {
      setDownloadingId(null);
    }
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return "-";
    return formatIST(dateStr, "-");
  };

  const getStatusBadge = (item: {
    status?: string | null;
    error_code?: string | null;
    error_message?: string | null;
    coverage_result?: ReportHistoryItem["coverage_result"];
  }) => {
    const statusLabel = getReportStatePresentation(item).statusLabel;
    const styles: Record<string, string> = {
      pending: "bg-gray-100 text-gray-800",
      processing: "bg-blue-100 text-blue-800",
      running: "bg-blue-100 text-blue-800",
      completed: "bg-green-100 text-green-800",
      failed: "bg-red-100 text-red-800",
      skipped: "bg-yellow-100 text-yellow-800",
    };
    const coverageTone = getTelemetryCoverageTone(item.coverage_result);
    const toneClassName =
      coverageTone === "bad"
        ? "bg-red-100 text-red-800"
        : coverageTone === "warn"
          ? "bg-amber-100 text-amber-800"
          : styles[item.status || "pending"] || styles.pending;
    return (
      <span className={`px-2 py-1 text-xs font-medium rounded-full ${toneClassName}`}>
        {statusLabel}
      </span>
    );
  };

  const selectedReportPresentation = selectedReportDetail ? getReportStatePresentation(selectedReportDetail) : null;

  return (
    <div className="section-spacing">
      <ReadOnlyBanner />
      {toast && (
        <div className={`fixed top-4 right-4 px-4 py-2 rounded-lg shadow-lg z-50 ${
          toast.type === "success" ? "bg-green-600" : "bg-red-600"
        } text-white`}>
          {toast.message}
        </div>
      )}

      <PageHeader title="Reports" subtitle={getReportPageSubtitle(me?.user.role)} />

      {me?.user.role === "super_admin" && !selectedTenantId ? (
        <div className="surface-panel border-amber-200 bg-amber-50 p-6 text-amber-900">
          <h2 className="text-lg font-semibold">Select organisation</h2>
          <p className="mt-2 text-sm text-amber-800">
            Reports are tenant-scoped. Choose an organisation first so the app can send the correct tenant header.
          </p>
        </div>
      ) : null}

      {reportScopeHint ? (
        <div className="surface-panel border-amber-200 bg-amber-50 p-4 text-amber-900">
          <h2 className="text-sm font-semibold">Assigned plant scope</h2>
          <p className="mt-1 text-sm text-amber-800">{reportScopeHint}</p>
        </div>
      ) : null}

      <div className={`grid md:grid-cols-1 gap-6 ${me?.user.role === "super_admin" && !selectedTenantId ? "pointer-events-none opacity-50" : ""}`}>
        <Link
          href="/reports/energy"
          className="surface-panel block p-6 transition-shadow hover:shadow-lg"
        >
          <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center mb-4">
            <svg className="w-6 h-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-gray-900">Energy Consumption Report</h2>
          <p className="text-sm text-gray-600 mt-1">
            kWh breakdown, demand analysis, load factor, cost estimation
          </p>
          {canGenerateReport ? (
            <button className="mt-4 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700">
              Generate Report
            </button>
          ) : null}
        </Link>
      </div>

      <div className="border-b border-[var(--border-subtle)]">
        <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <nav className="responsive-tab-strip -mb-px">
          <button
            onClick={() => setActiveTab("history")}
            className={`responsive-tab-link border-b-2 px-2 py-4 font-medium text-sm ${
              activeTab === "history"
                ? "border-blue-500 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
            }`}
          >
            Report History
          </button>
          {canGenerateReport ? (
            <button
              onClick={() => setActiveTab("schedules")}
              className={`responsive-tab-link border-b-2 px-2 py-4 font-medium text-sm ${
                activeTab === "schedules"
                  ? "border-blue-500 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
              }`}
            >
              Schedules
            </button>
          ) : null}
        </nav>
        </div>
      </div>

      {activeTab === "history" && (
        <div>
          {loading ? (
            <div className="text-center py-8 text-gray-500">Loading...</div>
          ) : history.length === 0 ? (
            <div className="surface-panel text-center py-8 text-gray-500">
              {getEmptyReportHistoryMessage(me?.user.role)}
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="text-sm text-slate-600">
                  Return here any time to check progress, understand failures, and open finished report outputs.
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  {history.some((item) => item.status === "pending" || item.status === "running") ? (
                    <div className="text-xs font-medium text-blue-700">Auto-refreshing while reports are still running</div>
                  ) : null}
                  <button
                    onClick={() => {
                      if (!selectedTenantId) return;
                      void loadHistory(selectedTenantId, historyPage).catch(() => {
                        showToast("Failed to refresh report history", "error");
                      });
                    }}
                    disabled={historyLoading || !selectedTenantId}
                    className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {historyLoading ? "Refreshing..." : "Refresh"}
                  </button>
                </div>
              </div>

              <div className="space-y-3 md:hidden">
                {history.map((item) => {
                  const isSelected = selectedReport?.report_id === item.report_id;
                  return (
                    <ReportHistoryMobileCard
                      key={item.report_id}
                      item={item}
                      isSelected={isSelected}
                      onOpen={() => openReportDetails(item.report_id)}
                      onDownload={() => void handleDownload(item.report_id)}
                      downloading={downloadingId === item.report_id}
                      statusBadge={getStatusBadge(item)}
                      createdAt={formatDate(item.created_at)}
                      progressSummary={getReportStatePresentation(item).historyDetail}
                    />
                  );
                })}
              </div>

              <div className="hidden w-full overflow-x-auto -mx-0 surface-panel overflow-hidden md:block">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Report Type</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Progress</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {history.map((item) => {
                      const isSelected = selectedReport?.report_id === item.report_id;
                      const itemDownloadReady = isReportDownloadReady(item);
                      return (
                        <tr
                          key={item.report_id}
                          onClick={() => openReportDetails(item.report_id)}
                          className={`${isSelected ? "bg-slate-50" : ""} cursor-pointer`}
                        >
                          <td className="px-6 py-4 text-sm text-gray-900 capitalize">
                            {item.report_type}
                          </td>
                          <td className="px-6 py-4">{getStatusBadge(item)}</td>
                          <td className="px-6 py-4 text-sm text-gray-500">
                            <div className="font-medium text-slate-700">{item.progress != null ? `${item.progress}%` : "—"}</div>
                            <div className="mt-1 text-xs text-slate-500">{getReportStatePresentation(item).historyDetail}</div>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-500">
                            {formatDate(item.created_at)}
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex flex-wrap items-center gap-3">
                              <button
                                onClick={(event) => {
                                  event.stopPropagation();
                                  openReportDetails(item.report_id);
                                }}
                                className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                              >
                                View details
                              </button>
                              {itemDownloadReady ? (
                                <button
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    void handleDownload(item.report_id);
                                  }}
                                  disabled={downloadingId === item.report_id}
                                  className="text-slate-700 hover:text-slate-900 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                  {downloadingId === item.report_id ? "Downloading..." : "Download"}
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
                    onClick={() => {
                      if (!selectedTenantId) return;
                      void loadHistory(selectedTenantId, Math.max(0, historyPage - 1)).catch(() => {
                        showToast("Failed to load report history", "error");
                      });
                    }}
                    disabled={historyLoading || historyPage === 0 || !selectedTenantId}
                    className="rounded-lg border border-slate-200 px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => {
                      if (!selectedTenantId) return;
                      void loadHistory(selectedTenantId, historyPage + 1).catch(() => {
                        showToast("Failed to load report history", "error");
                      });
                    }}
                    disabled={historyLoading || !hasMoreHistory || !selectedTenantId}
                    className="rounded-lg border border-slate-200 px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Next
                  </button>
                </div>
              </div>

              <div
                ref={selectedReportPanelRef}
                tabIndex={-1}
                className="surface-panel p-4 outline-none sm:p-5"
              >
                {selectedReportDetail ? (
                  <div className="space-y-5">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Selected report</div>
                        <h3 className="mt-2 text-lg font-semibold text-slate-900 capitalize">
                          {selectedReportDetail.report_type} report
                        </h3>
                        <p className="mt-1 font-mono text-xs text-slate-500">{selectedReportDetail.report_id}</p>
                      </div>
                      {getStatusBadge(selectedReportDetail)}
                    </div>

                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          <div className="text-sm font-semibold text-slate-900">{selectedReportPresentation?.historyDetail}</div>
                          <p className="mt-1 text-sm text-slate-600">
                            {selectedReportPresentation?.detailSummary}
                          </p>
                        </div>
                        <div className="text-2xl font-semibold text-slate-900">
                          {selectedReportDetail.progress != null ? `${Math.round(selectedReportDetail.progress)}%` : "—"}
                        </div>
                      </div>
                      <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-slate-200">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${selectedReportDetail.status === "failed" ? "bg-red-500" : "bg-[linear-gradient(135deg,#2563eb,#0f766e)]"}`}
                          style={{ width: `${Math.max(0, Math.min(100, Number(selectedReportDetail.progress ?? 0)))}%` }}
                        />
                      </div>
                      <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-600">
                        {selectedReportDetail.phase_label ? <span>Current step: {selectedReportDetail.phase_label}</span> : null}
                        {selectedReportDetail.queue_position != null ? <span>Queue position: {selectedReportDetail.queue_position + 1}</span> : null}
                        {selectedReportDetail.estimated_completion_seconds != null ? (
                          <span>ETA: {formatJobSeconds(selectedReportDetail.estimated_completion_seconds)}</span>
                        ) : null}
                        {selectedReportDetail.result_ready ? <span>Result ready</span> : null}
                        {isReportDownloadReady(selectedReportDetail) ? <span>Download ready</span> : null}
                      </div>
                    </div>

                    {selectedReportPresentation?.coverageCallout ? (
                      <div
                        className={`rounded-xl border p-4 ${
                          selectedReportPresentation.coverageCallout.tone === "bad"
                            ? "border-red-200 bg-red-50 text-red-900"
                            : selectedReportPresentation.coverageCallout.tone === "warn"
                              ? "border-amber-200 bg-amber-50 text-amber-900"
                              : "border-blue-200 bg-blue-50 text-blue-900"
                        }`}
                      >
                        <div className="text-xs font-semibold uppercase tracking-[0.16em]">Coverage state</div>
                        <div className="mt-2 text-sm font-semibold">
                          {selectedReportPresentation.coverageCallout.label}
                        </div>
                        <p className="mt-1 text-sm">
                          {selectedReportPresentation.coverageCallout.summary}
                        </p>
                      </div>
                    ) : null}

                    <div className="grid gap-3 md:grid-cols-3">
                      <div className="rounded-lg border border-slate-200 p-3">
                        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Created</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">{formatDate(selectedReportDetail.created_at)}</div>
                      </div>
                      <div className="rounded-lg border border-slate-200 p-3">
                        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Started</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">{formatDate(selectedReportDetail.started_at ?? null)}</div>
                      </div>
                      <div className="rounded-lg border border-slate-200 p-3">
                        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Completed</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">{formatDate(selectedReportDetail.completed_at ?? null)}</div>
                      </div>
                    </div>

                    <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
                      {isReportDownloadReady(selectedReportDetail) ? (
                        <button
                          onClick={() => handleDownload(selectedReportDetail.report_id)}
                          disabled={downloadingId === selectedReportDetail.report_id}
                          className="inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-400"
                        >
                          {downloadingId === selectedReportDetail.report_id ? "Downloading..." : "Download artifact"}
                        </button>
                      ) : null}
                    </div>

                    {selectedReportResultLoading ? (
                      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                        Loading report output details...
                      </div>
                    ) : selectedReportResult ? (
                      <div className="rounded-xl border border-slate-200 p-4">
                        <h4 className="text-sm font-semibold text-slate-900">{selectedReportResult.headline}</h4>
                        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                          {selectedReportResult.metrics.map((metric) => (
                            <div key={metric.label} className="rounded-lg bg-slate-50 p-3">
                              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{metric.label}</div>
                              <div className="mt-1 text-sm font-semibold text-slate-900">{metric.value}</div>
                            </div>
                          ))}
                        </div>
                        {selectedReportResult.notes.length > 0 ? (
                          <div className="mt-4">
                            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Highlights</div>
                            <ul className="mt-2 space-y-2 text-sm text-slate-700">
                              {selectedReportResult.notes.map((note) => (
                                <li key={note}>• {note}</li>
                              ))}
                            </ul>
                          </div>
                        ) : null}
                        {selectedReportResult.warnings.length > 0 ? (
                          <details className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3">
                            <summary className="cursor-pointer text-sm font-medium text-amber-900">Warnings and details</summary>
                            <ul className="mt-2 space-y-2 text-sm text-amber-900">
                              {selectedReportResult.warnings.map((warning) => (
                                <li key={warning}>• {warning}</li>
                              ))}
                            </ul>
                          </details>
                        ) : null}
                      </div>
                    ) : selectedReportResultError ? (
                      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                        {selectedReportResultError}
                      </div>
                    ) : null}

                    {selectedReportDetail.status === "failed" && selectedReportDetail.error_message ? (
                      <details className="rounded-lg border border-rose-200 bg-rose-50 p-3">
                        <summary className="cursor-pointer text-sm font-medium text-rose-900">Technical details</summary>
                        <p className="mt-2 text-sm text-rose-900">{selectedReportDetail.error_message}</p>
                      </details>
                    ) : null}
                  </div>
                ) : (
                  <div className="text-sm text-slate-500">
                    Select a report from history to view its current status, readiness, and available actions.
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === "schedules" && (
        <div>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-gray-900">Scheduled Reports</h2>
            {canGenerateReport ? (
              <button
                onClick={() => {
                  setEditingSchedule(null);
                  setShowModal(true);
                }}
                className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700"
              >
                New Schedule
              </button>
            ) : null}
          </div>

          {loading ? (
            <div className="text-center py-8 text-gray-500">Loading...</div>
          ) : schedules.length === 0 ? (
            <div className="surface-panel text-center py-8 text-gray-500">
              {getEmptyScheduleMessage(me?.user.role)}
            </div>
          ) : (
            <div className="space-y-3 md:hidden">
              {schedules.map((schedule) => (
                <ReportScheduleMobileCard
                  key={schedule.schedule_id}
                  schedule={schedule}
                  canGenerateReport={canGenerateReport}
                  onEdit={() => handleEditSchedule(schedule)}
                  onDeactivate={() => void handleDeleteSchedule(schedule.schedule_id)}
                  statusBadge={getStatusBadge({ status: schedule.last_status || "pending" })}
                  nextRun={formatDate(schedule.next_run_at)}
                />
              ))}
            </div>
          )}
          {loading ? null : schedules.length === 0 ? null : (
            <div className="hidden w-full overflow-x-auto -mx-0 surface-panel overflow-hidden md:block">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Frequency</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Devices</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Next Run</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Last Status</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {schedules.map((schedule) => (
                    <tr key={schedule.schedule_id}>
                      <td className="px-6 py-4 text-sm text-gray-900 capitalize">
                        {schedule.report_type}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 capitalize">
                        {schedule.frequency}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500">
                        {schedule.params_template?.device_ids?.length || 0} devices
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500">
                        {formatDate(schedule.next_run_at)}
                      </td>
                      <td className="px-6 py-4">
                        {getStatusBadge({ status: schedule.last_status || "pending" })}
                      </td>
                      <td className="px-6 py-4">
                        {canGenerateReport && schedule.is_active ? (
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => handleEditSchedule(schedule)}
                              className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => handleDeleteSchedule(schedule.schedule_id)}
                              className="text-red-600 hover:text-red-800 text-sm font-medium"
                            >
                              Deactivate
                            </button>
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4">
          <div className="surface-panel max-h-[90vh] w-full max-w-md overflow-y-auto p-5 sm:p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              {editingSchedule ? "Edit Schedule" : "Create Schedule"}
            </h3>
            
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Report Type</label>
                <select
                  value={formData.report_type}
                  onChange={(e) => setFormData({ ...formData, report_type: e.target.value as "consumption" | "comparison" })}
                  className="w-full border rounded-lg px-3 py-2"
                >
                  <option value="consumption">Energy Consumption</option>
                  <option value="comparison">Comparison</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Frequency</label>
                <select
                  value={formData.frequency}
                  onChange={(e) => setFormData({ ...formData, frequency: e.target.value as "daily" | "weekly" | "monthly" })}
                  className="w-full border rounded-lg px-3 py-2"
                >
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Group By</label>
                <select
                  value={formData.group_by}
                  onChange={(e) => setFormData({ ...formData, group_by: e.target.value as "daily" | "weekly" })}
                  className="w-full border rounded-lg px-3 py-2"
                >
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Scope</label>
                {isPlantScopedRole ? (
                  <p className="mb-2 text-xs text-amber-700">
                    Only devices from your assigned plants are available for scheduling.
                  </p>
                ) : null}
                <DeviceScopeSelector
                  catalog={scopeCatalog}
                  value={normalizedScopeSelection}
                  onChange={setScopeSelection}
                  disabled={submitting}
                  helperText={reportScopeHint}
                  allModeTitle={getReportScopeLabel(me?.user.role)}
                />
                <p className="mt-2 text-xs text-slate-600">{selectedScopeSummary}</p>
              </div>
            </div>

            <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end sm:space-x-0">
              <button
                onClick={resetScheduleComposer}
                className="min-h-11 rounded-lg border px-4 py-2 text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              {canGenerateReport ? (
                <button
                  onClick={handleCreateSchedule}
                  disabled={submitting}
                  className="min-h-11 rounded-lg bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {submitting ? (editingSchedule ? "Saving..." : "Creating...") : (editingSchedule ? "Save Changes" : "Create")}
                </button>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
