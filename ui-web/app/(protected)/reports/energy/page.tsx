"use client";

import Link from "next/link";
import { useState, useCallback, useEffect, useMemo } from "react";
import { DateRangeSelector } from "@/components/reports/DateRangeSelector";
import { DeviceScopeSelector } from "@/components/reports/DeviceScopeSelector";
import { ReportProgress } from "@/components/reports/ReportProgress";
import { ErrorPanel } from "@/components/reports/ErrorPanel";
import { HiddenOverconsumptionInsightSection } from "@/components/reports/HiddenOverconsumptionInsightSection";
import { formatCurrencyCodeValue, formatCo2Kg, formatEmissionFactorUnit, formatFactorSource } from "@/lib/presentation";
import {
  type HiddenOverconsumptionInsight,
} from "@/lib/hiddenOverconsumptionPresentation";
import { authApi, type PlantProfile } from "@/lib/authApi";
import { getDevices, type Device } from "@/lib/deviceApi";
import { resolveScopedTenantId, resolveVisiblePlants } from "@/lib/orgScope";
import {
  submitConsumptionReport,
  getReportDownload,
  ReportApiError,
  type ReportStatus,
} from "@/lib/reportApi";
import { useAuth } from "@/lib/authContext";
import { useTenantStore } from "@/lib/tenantStore";
import { getReportScopeHint, getReportScopeLabel, isPlantScopedReportRole } from "@/lib/reportScope";
import { getLongRunningJobErrorMessage } from "@/lib/asyncJobPresentation";
import {
  buildDeviceScopeCatalog,
  getDeviceScopeSummary,
  normalizeDeviceScopeSelection,
  resolveDeviceIdsForSelection,
  type DeviceScopeSelection,
} from "@/lib/deviceScopeSelection";

type ViewState = "empty" | "accepted" | "completed" | "failed";

interface ReportResult {
  summary: {
    total_kwh: number;
    peak_demand_kw: number | null;
    load_factor_pct: number | null;
    total_cost: number | null;
    currency: string;
  };
  insights: string[];
  warnings?: string[];
  hidden_overconsumption_insight?: HiddenOverconsumptionInsight | null;
  co2_overview?: {
    available: boolean;
    reason?: string | null;
    total_co2_kg?: number | null;
    off_shift_co2_kg?: number | null;
    off_shift_available?: boolean;
    factor?: {
      value: number;
      unit: string;
      source: string;
      factor_year?: string | null;
    } | null;
    factor_source?: string;
  } | null;
}

export default function EnergyReportPage() {
  const { me } = useAuth();
  const { selectedTenantId } = useTenantStore();
  const [viewState, setViewState] = useState<ViewState>("empty");
  const [reportId, setReportId] = useState<string | null>(null);
  const [submittedStatus, setSubmittedStatus] = useState<ReportStatus | null>(null);
  const [result, setResult] = useState<ReportResult | null>(null);
  const [error, setError] = useState<{ error_code: string; error_message: string } | null>(null);

  const [startDate, setStartDate] = useState<string>("");
  const [endDate, setEndDate] = useState<string>("");
  const [devices, setDevices] = useState<Device[]>([]);
  const [plants, setPlants] = useState<PlantProfile[]>([]);
  const [scopeSelection, setScopeSelection] = useState<DeviceScopeSelection>({
    mode: "all",
    plantId: null,
    deviceIds: [],
  });
  const [groupBy, setGroupBy] = useState<"daily" | "weekly">("daily");
  const [submitting, setSubmitting] = useState(false);
  const [isDateRangeValid, setIsDateRangeValid] = useState(true);
  const isPlantScopedRole = isPlantScopedReportRole(me?.user.role);
  const reportScopeHint = getReportScopeHint(me?.user.role);
  const scopedOrgId = resolveScopedTenantId(me, selectedTenantId);
  const visiblePlants = useMemo(() => resolveVisiblePlants(me, plants), [me, plants]);
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

  useEffect(() => {
    let active = true;
    Promise.all([
      getDevices(),
      scopedOrgId ? authApi.listPlants(scopedOrgId) : Promise.resolve([]),
    ])
      .then(([deviceRows, plantRows]) => {
        if (!active) {
          return;
        }
        setDevices(deviceRows);
        setPlants(plantRows);
      })
      .catch(() => {
        if (!active) {
          return;
        }
        setDevices([]);
        setPlants([]);
      });
    return () => {
      active = false;
    };
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

  const handleRangeChange = useCallback((start: string, end: string) => {
    setStartDate(start);
    setEndDate(end);
  }, []);

  const handleSubmit = async () => {
    if (submitting || !startDate || !endDate || selectedDeviceIds.length === 0 || !selectedTenantId || !isDateRangeValid) return;

    setSubmitting(true);
    setError(null);

    try {
      const response = await submitConsumptionReport({
        tenant_id: selectedTenantId,
        device_id: selectedDeviceIds.length > 1 ? "ALL" : selectedDeviceIds[0],
        start_date: startDate,
        end_date: endDate,
        report_name: "Energy Consumption Report",
      });

      setReportId(response.report_id);
      setSubmittedStatus(response);
      setViewState("accepted");
    } catch (err) {
      const apiBody = err instanceof ReportApiError && typeof err.body === "object" && err.body !== null
        ? err.body as { error?: string; error_code?: string; message?: string }
        : null;
      setError({
        error_code: apiBody?.error_code || apiBody?.error || "SUBMIT_ERROR",
        error_message: apiBody?.message || getLongRunningJobErrorMessage(err, "Failed to submit report"),
      });
      setViewState("failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleComplete = (reportResult: unknown) => {
    setResult(reportResult as ReportResult);
    setViewState("completed");
  };

  const handleError = (err: { error_code: string; error_message: string }) => {
    setError(err);
    setViewState("failed");
  };

  const handleRetry = () => {
    setViewState("empty");
    setReportId(null);
    setSubmittedStatus(null);
    setResult(null);
    setError(null);
  };

  const handleDownload = async () => {
    if (!reportId) {
      alert("No report ID");
      return;
    }
    try {
      console.log("Starting download for report:", reportId);
      if (!selectedTenantId) {
        throw new Error("Select an organisation before downloading reports");
      }
      const blob = await getReportDownload(reportId, selectedTenantId);
      console.log("Got blob:", blob.type, blob.size);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `energy_report_${reportId}.pdf`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      console.log("Download triggered");
    } catch (err) {
      console.error("Failed to download report:", err);
      alert(`Failed to download report: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  };

  const isFormValid = startDate && endDate && selectedDeviceIds.length > 0 && !submitting && isDateRangeValid;
  const hiddenInsight = result?.hidden_overconsumption_insight ?? null;
  const hiddenCurrency = result?.summary?.currency || "INR";
  const isCompletedView = viewState === "completed" && result != null;

  const configurationPanel = (
    <div className="space-y-6">
      {viewState === "empty" && (
        <>
          <div className="bg-white p-4 rounded-lg border">
            <h3 className="font-medium text-gray-900 mb-3">Date Range</h3>
            <DateRangeSelector
              onRangeChange={handleRangeChange}
              disabled={submitting}
              maxDays={90}
              maxDaysMessage="Maximum allowed range is 90 days."
              onValidationChange={setIsDateRangeValid}
            />
          </div>

          <div className="bg-white p-4 rounded-lg border">
            <h3 className="font-medium text-gray-900 mb-3">Scope</h3>
            <DeviceScopeSelector
              catalog={scopeCatalog}
              value={normalizedScopeSelection}
              onChange={setScopeSelection}
              disabled={submitting || !selectedTenantId}
              helperText={reportScopeHint}
              allModeTitle={getReportScopeLabel(me?.user.role)}
            />
            <p className="mt-3 text-sm text-gray-500">{selectedScopeSummary}</p>
          </div>

          <div className="bg-white p-4 rounded-lg border">
            <h3 className="font-medium text-gray-900 mb-3">Group By</h3>
            <div className="flex gap-4">
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="groupBy"
                  value="daily"
                  checked={groupBy === "daily"}
                  onChange={() => setGroupBy("daily")}
                  disabled={submitting}
                  className="w-4 h-4"
                />
                <span className="text-sm text-gray-700">Daily</span>
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="groupBy"
                  value="weekly"
                  checked={groupBy === "weekly"}
                  onChange={() => setGroupBy("weekly")}
                  disabled={submitting}
                  className="w-4 h-4"
                />
                <span className="text-sm text-gray-700">Weekly</span>
              </label>
            </div>
          </div>

          <button
            onClick={handleSubmit}
            disabled={!isFormValid || !selectedTenantId}
            className={`w-full py-3 rounded-lg font-medium transition-colors ${
              isFormValid && selectedTenantId
                ? "bg-blue-600 text-white hover:bg-blue-700"
                : "bg-gray-200 text-gray-500 cursor-not-allowed"
            }`}
          >
            {submitting ? "Submitting..." : "Generate Report"}
          </button>
        </>
      )}

      {viewState === "accepted" && reportId && (
        <ReportProgress
          reportId={reportId}
          tenantId={selectedTenantId ?? ""}
          onComplete={handleComplete}
          onError={handleError}
          onConfigureAnother={handleRetry}
          onStatusChange={setSubmittedStatus}
        />
      )}

      {viewState === "failed" && error && (
        <ErrorPanel
          errorCode={error.error_code}
          errorMessage={error.error_message}
          onRetry={handleRetry}
        />
      )}
    </div>
  );

  const completedResultPanel = isCompletedView && result ? (
    <div className="space-y-6">
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Report Result</h2>
            <p className="mt-1 text-sm text-slate-600">
              Completed reports use a full-width layout so detailed sections like Hidden Overconsumption stay readable.
            </p>
          </div>
          <div className="grid gap-3 text-sm text-slate-600 sm:grid-cols-3 lg:min-w-[420px]">
            <div className="rounded-lg bg-slate-50 p-3">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Date Range</div>
              <div className="mt-1 text-sm font-semibold text-slate-900">
                {startDate && endDate ? `${startDate} to ${endDate}` : "Selected range"}
              </div>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Scope</div>
              <div className="mt-1 text-sm font-semibold text-slate-900">{selectedScopeSummary}</div>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Group By</div>
              <div className="mt-1 text-sm font-semibold capitalize text-slate-900">{groupBy}</div>
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <div className="bg-blue-50 p-4 rounded-lg text-center">
              <div className="text-2xl font-bold text-blue-600">
                {result.summary?.total_kwh?.toFixed(1) ?? "—"}
              </div>
              <div className="text-sm text-gray-600">Total kWh</div>
            </div>
            <div className="bg-green-50 p-4 rounded-lg text-center">
              <div className="text-2xl font-bold text-green-600">
                {result.summary?.peak_demand_kw != null ? result.summary.peak_demand_kw.toFixed(1) : "—"}
              </div>
              <div className="text-sm text-gray-600">Peak kW</div>
            </div>
            <div className="bg-purple-50 p-4 rounded-lg text-center">
              <div className="text-2xl font-bold text-purple-600">
                {result.summary?.load_factor_pct != null
                  ? `${result.summary.load_factor_pct.toFixed(1)}%`
                  : "—"}
              </div>
              <div className="text-sm text-gray-600">Load Factor</div>
            </div>
            <div className="bg-orange-50 p-4 rounded-lg text-center">
              <div className="text-2xl font-bold text-orange-600">
                {formatCurrencyCodeValue(result.summary?.total_cost, result.summary?.currency || "INR")}
              </div>
              <div className="text-sm text-gray-600">Est. Cost</div>
            </div>
          </div>

          {result.co2_overview?.available && (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="bg-teal-50 p-4 rounded-lg text-center">
                  <div className="text-2xl font-bold text-teal-600">
                    {formatCo2Kg(result.co2_overview.total_co2_kg)}
                  </div>
                  <div className="text-sm text-gray-600">Total CO₂</div>
                </div>
                <div className="bg-cyan-50 p-4 rounded-lg text-center">
                  <div className="text-2xl font-bold text-cyan-600">
                    {result.co2_overview.off_shift_available && result.co2_overview.off_shift_co2_kg != null
                      ? formatCo2Kg(result.co2_overview.off_shift_co2_kg)
                      : "—"}
                  </div>
                  <div className="text-sm text-gray-600">Off-Shift CO₂</div>
                </div>
              </div>
              <div className="text-xs text-slate-500">
                {(() => {
                  const unit = formatEmissionFactorUnit(result.co2_overview.factor?.unit ?? "");
                  const srcName = result.co2_overview.factor?.source?.trim() || "";
                  const srcClass = formatFactorSource(result.co2_overview.factor_source);
                  const parts = [srcName, srcClass].filter(Boolean);
                  const attr = parts.length > 0 ? ` (${parts.join(", ")})` : "";
                  return `CO₂ estimated using emission factor ${result.co2_overview.factor?.value} ${unit}${attr}.`;
                })()}
              </div>
            </>
          )}
          {result.co2_overview && !result.co2_overview.available && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
              CO₂ emissions estimation is unavailable because an emission factor has not been configured for this organisation.
            </div>
          )}

          <HiddenOverconsumptionInsightSection
            insight={hiddenInsight}
            currency={hiddenCurrency}
            renderMode="snapshot"
          />

          {result.insights && result.insights.length > 0 && (
            <div>
              <h3 className="font-medium text-gray-900 mb-3">Key Insights</h3>
              <ul className="space-y-2">
                {result.insights.map((insight, idx) => (
                  <li key={idx} className="text-sm text-gray-600 bg-gray-50 p-2 rounded">
                    {insight}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex flex-col gap-3 sm:flex-row">
            <button
              onClick={handleRetry}
              className="w-full rounded-lg border border-slate-200 bg-white py-2 text-slate-700 hover:bg-slate-50 sm:w-auto sm:px-5"
            >
              Configure Another Report
            </button>
            {(submittedStatus?.artifact_ready || submittedStatus?.download_ready) ? (
              <button
                onClick={handleDownload}
                className="w-full rounded-lg bg-blue-600 py-2 text-white hover:bg-blue-700 sm:w-auto sm:px-5"
              >
                Download PDF
              </button>
            ) : (
              <div className="w-full rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-sm text-slate-600 sm:w-auto">
                Download PDF will be ready from Report History shortly.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Energy Consumption Report</h1>
        <p className="text-gray-600 mt-1">
          {isPlantScopedRole
            ? "Generate detailed energy analysis for your accessible plants"
            : "Generate detailed energy analysis report"}
        </p>
      </div>

      {me?.user.role === "super_admin" && !selectedTenantId ? (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-900">
          <h2 className="font-semibold">Select organisation</h2>
          <p className="mt-1 text-sm text-amber-800">
            Energy reports need a tenant scope. Choose an organisation first, then generate the report.
          </p>
        </div>
      ) : null}

      {reportScopeHint ? (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-900">
          <h2 className="font-semibold">Assigned plant scope</h2>
          <p className="mt-1 text-sm text-amber-800">{reportScopeHint}</p>
        </div>
      ) : null}

      {isCompletedView ? (
        <div className="space-y-6">
          {completedResultPanel}
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(320px,420px)_minmax(0,1fr)]">
          {configurationPanel}

          <div className="bg-white rounded-lg border min-h-[400px] p-6">
            {viewState === "empty" && (
              <div className="h-full flex flex-col items-center justify-center text-gray-400">
                <svg className="w-16 h-16 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p>Configure your report and click Generate</p>
              </div>
            )}
            {viewState === "accepted" && (
              <div className="flex h-full flex-col justify-between gap-6">
                <div>
                  <div className="inline-flex rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-emerald-800">
                    What happens next
                  </div>
                  <h2 className="mt-4 text-xl font-semibold text-slate-900">Your report is now running in the background</h2>
                  <p className="mt-2 text-sm text-slate-600">
                    You can leave this page and come back later. The latest progress and downloads will stay available from Report History.
                  </p>
                  <div className="mt-5 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-lg bg-slate-50 p-4">
                      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Selected range</div>
                      <div className="mt-1 text-sm font-semibold text-slate-900">
                        {startDate && endDate ? `${startDate} to ${endDate}` : "Selected range"}
                      </div>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-4">
                      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Scope</div>
                      <div className="mt-1 text-sm font-semibold text-slate-900">{selectedScopeSummary}</div>
                    </div>
                  </div>
                  {submittedStatus?.queue_position != null || submittedStatus?.estimated_completion_seconds != null ? (
                    <div className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
                      {submittedStatus.queue_position != null ? (
                        <div>Queue position: {submittedStatus.queue_position + 1}</div>
                      ) : null}
                      {submittedStatus.estimated_completion_seconds != null ? (
                        <div className="mt-1">
                          Estimated completion: about {Math.max(1, Math.round(submittedStatus.estimated_completion_seconds / 60))} minute(s)
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>

                <div className="flex flex-col gap-3 sm:flex-row">
                  <Link
                    href="/reports"
                    className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
                  >
                    Go to Report History
                  </Link>
                  <button
                    onClick={handleRetry}
                    className="inline-flex items-center justify-center rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    Configure another report
                  </button>
                </div>
              </div>
            )}
            {viewState === "failed" && (
              <div className="flex h-full flex-col items-center justify-center text-center text-slate-500">
                <p>Review the message on the left, then update the inputs and try again.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
