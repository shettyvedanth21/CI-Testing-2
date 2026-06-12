import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const analyticsPagePath = path.resolve(__dirname, "../../app/(protected)/analytics/page.tsx");
const energyReportPagePath = path.resolve(__dirname, "../../app/(protected)/reports/energy/page.tsx");
const reportsPagePath = path.resolve(__dirname, "../../app/(protected)/reports/page.tsx");
const reportPresentationPath = path.resolve(__dirname, "../../lib/reportPresentation.ts");
const wastePagePath = path.resolve(__dirname, "../../app/(protected)/waste-analysis/page.tsx");
const asyncJobHandoffCardPath = path.resolve(__dirname, "../../components/reports/AsyncJobHandoffCard.tsx");
const asyncJobPresentationPath = path.resolve(__dirname, "../../lib/asyncJobPresentation.ts");
const reportProgressPath = path.resolve(__dirname, "../../components/reports/ReportProgress.tsx");
const dateRangeSelectorPath = path.resolve(__dirname, "../../components/reports/DateRangeSelector.tsx");

const analyticsPageSource = readFileSync(analyticsPagePath, "utf-8");
const energyReportPageSource = readFileSync(energyReportPagePath, "utf-8");
const reportsPageSource = readFileSync(reportsPagePath, "utf-8");
const reportPresentationSource = readFileSync(reportPresentationPath, "utf-8");
const wastePageSource = readFileSync(wastePagePath, "utf-8");
const asyncJobHandoffCardSource = readFileSync(asyncJobHandoffCardPath, "utf-8");
const asyncJobPresentationSource = readFileSync(asyncJobPresentationPath, "utf-8");
const reportProgressSource = readFileSync(reportProgressPath, "utf-8");
const dateRangeSelectorSource = readFileSync(dateRangeSelectorPath, "utf-8");

test("analytics submit state explains the background job handoff clearly", () => {
  assert.equal(analyticsPageSource.includes("Analysis started"), true);
  assert.equal(analyticsPageSource.includes("Fleet analysis started"), true);
  assert.equal(analyticsPageSource.includes("Processing continues in the background"), true);
  assert.equal(
    asyncJobPresentationSource.includes("Some devices may run immediately while others wait in queue"),
    true,
  );
  assert.equal(
    analyticsPageSource.includes("Track progress in Analysis History below. You do not need to stay on this screen."),
    true,
  );
  assert.equal(
    analyticsPageSource.includes("This fleet analysis may move through queueing, running, and completion in stages."),
    true,
  );
});

test("analytics history detail keeps completed-but-finalizing jobs truthful", () => {
  assert.equal(
    analyticsPageSource.includes("Analysis completed successfully. Results are still being finalized for this history view."),
    true,
  );
  assert.equal(analyticsPageSource.includes('"Results Finalizing"'), true);
});

test("analytics no-data outcome is treated as a clean terminal state", () => {
  assert.equal(analyticsPageSource.includes("No telemetry in selected range"), true);
  assert.equal(
    analyticsPageSource.includes("This analysis finished cleanly, but there was no telemetry available for the selected device or date range."),
    true,
  );
  assert.equal(asyncJobPresentationSource.includes('if (status === "failed" && errorCode === "NO_TELEMETRY_IN_RANGE") return "No data";'), true);
  assert.equal(asyncJobPresentationSource.includes('if (code === "NO_TELEMETRY_IN_RANGE") {'), true);
});

test("analytics page preflights telemetry availability before submission", () => {
  assert.equal(analyticsPageSource.includes("Checking whether the selected range has telemetry..."), true);
  assert.equal(analyticsPageSource.includes("Devices without telemetry will be skipped if you continue."), true);
  assert.equal(analyticsPageSource.includes("preflight?.guaranteed_no_data"), true);
});

test("analytics page blocks custom ranges longer than 30 days", () => {
  assert.equal(analyticsPageSource.includes("ANALYTICS_MAX_RANGE_DAYS = 30"), true);
  assert.equal(analyticsPageSource.includes("Analytics supports up to ${ANALYTICS_MAX_RANGE_DAYS} days per run."), true);
  assert.equal(analyticsPageSource.includes("disabled={!isAnalyticsRangeValid}"), true);
});

test("fleet result helpers distinguish complete, partial, and unusable coverage", () => {
  assert.equal(asyncJobPresentationSource.includes('headline: "Partial fleet result"'), true);
  assert.equal(asyncJobPresentationSource.includes('headline: "Complete fleet result"'), true);
  assert.equal(asyncJobPresentationSource.includes('headline: "No usable fleet coverage"'), true);
  assert.equal(asyncJobPresentationSource.includes("This result is usable now, but coverage is"), true);
  assert.equal(asyncJobPresentationSource.includes("getUserFacingJobStatusLabelWithCoverage"), true);
  assert.equal(asyncJobPresentationSource.includes("getTelemetryCoverageLabel"), true);
});

test("analytics fleet history and result surfaces use result-destination wording", () => {
  assert.equal(analyticsPageSource.includes("Fleet Result State"), true);
  assert.equal(analyticsPageSource.includes("Fleet Coverage Summary"), true);
  assert.equal(analyticsPageSource.includes("What To Do Next"), true);
  assert.equal(analyticsPageSource.includes("Open Fleet Result"), true);
});

test("analytics submit path protects against duplicate submissions", () => {
  assert.equal(
    analyticsPageSource.includes("if (!analysisType || !models || isSubmittingAnalysis) return;"),
    true,
  );
  assert.equal(analyticsPageSource.includes('const [isSubmittingAnalysis, setIsSubmittingAnalysis] = useState(false);'), true);
  assert.equal(analyticsPageSource.includes('{isSubmittingAnalysis ? "Submitting..." : "Run Analysis"}'), true);
});

test("energy report submit state explains the background job handoff clearly", () => {
  assert.equal(reportProgressSource.includes("Report started"), true);
  assert.equal(
    reportProgressSource.includes("Processing continues in the background. You can continue using the platform while this runs."),
    true,
  );
  assert.equal(energyReportPageSource.includes("Go to Report History"), true);
  assert.equal(
    energyReportPageSource.includes("Your report is now running in the background"),
    true,
  );
});

test("waste analysis submit state explains the background job handoff clearly", () => {
  assert.equal(wastePageSource.includes("Waste analysis started"), true);
  assert.equal(
    wastePageSource.includes("Processing continues in the background. You can continue using the platform while this runs."),
    true,
  );
  assert.equal(wastePageSource.includes("Track in Waste Analysis History"), true);
  assert.equal(
    wastePageSource.includes("You do not need to stay on this page. Track progress in Waste Analysis History below."),
    true,
  );
  assert.equal(wastePageSource.includes("if (isSubmitting) {"), true);
  assert.equal(wastePageSource.includes('{isSubmitting ? "Starting waste analysis..." : "Run Waste Analysis"}'), true);
  assert.equal(wastePageSource.includes("Your waste analysis was accepted. Track the live status here or jump straight to the history section below."), true);
  assert.equal(wastePageSource.includes('historyHref="#waste-analysis-history"'), true);
  assert.equal(wastePageSource.includes("acceptedHandoffRef.current.scrollIntoView({ behavior: \"smooth\", block: \"start\" });"), true);
  assert.equal(wastePageSource.includes("Configure another analysis"), true);
});

test("waste analysis keeps result truth when PDF artifact preparation fails", () => {
  assert.equal(wastePageSource.includes("Result ready, download recovering"), true);
  assert.equal(wastePageSource.includes("Result ready, PDF unavailable"), true);
  assert.equal(
    wastePageSource.includes("The waste analysis finished successfully. Stored artifact upload failed, but a fresh PDF can still be generated from the saved result."),
    true,
  );
  assert.equal(
    wastePageSource.includes("A fresh PDF can be generated from the saved result on demand."),
    true,
  );
  assert.equal(wastePageSource.includes("ARTIFACT_UPLOAD_FAILED"), true);
  assert.equal(asyncJobHandoffCardSource.includes("Completed with issues"), true);
  assert.equal(asyncJobHandoffCardSource.includes("Needs attention"), true);
});

test("waste analysis history detail keeps result and download readiness truthful", () => {
  assert.equal(wastePageSource.includes("Waste Analysis History"), true);
  assert.equal(wastePageSource.includes("Selected Waste Analysis"), true);
  assert.equal(
    wastePageSource.includes("Waste analysis completed successfully. Results are still being finalized for this history view."),
    true,
  );
  assert.equal(wastePageSource.includes("You can open the waste analysis result now. The PDF is still being prepared."), true);
  assert.equal(wastePageSource.includes("PDF download will appear here as soon as it is ready."), true);
  assert.equal(wastePageSource.includes("This waste analysis is waiting for processing capacity and has not started running yet."), true);
  assert.equal(wastePageSource.includes("This waste analysis could not be completed."), true);
  assert.equal(wastePageSource.includes("{selectedIsDownloadReady ? ("), true);
  assert.equal(wastePageSource.includes("View details"), true);
  assert.equal(wastePageSource.includes("Download PDF"), true);
  assert.equal(wastePageSource.includes("Selected job"), true);
  assert.equal(wastePageSource.includes("Analysis"), true);
  assert.equal(wastePageSource.includes("Created"), true);
  assert.equal(wastePageSource.includes("Page {historyPage + 1}"), true);
  assert.equal(wastePageSource.includes("Previous"), true);
  assert.equal(wastePageSource.includes("Next"), true);
});

test("waste analysis result details render all three waste categories including idle", () => {
  assert.equal(wastePageSource.includes('label="Idle Running"'), true);
  assert.equal(wastePageSource.includes('duration={device.idle?.duration_sec ?? device.idle_duration_sec}'), true);
  assert.equal(wastePageSource.includes('kwh={device.idle?.energy_kwh ?? device.idle_energy_kwh}'), true);
  assert.equal(wastePageSource.includes('cost={device.idle?.cost ?? device.idle_cost}'), true);
  assert.equal(wastePageSource.includes('label="Off-Hours Running"'), true);
  assert.equal(wastePageSource.includes('label="Overconsumption"'), true);
});

test("waste analysis explains when coverage blocks PDF generation for an otherwise viewable result", () => {
  assert.equal(wastePageSource.includes("Result is ready, PDF unavailable for this run"), true);
  assert.equal(
    wastePageSource.includes("The waste analysis result is available below, but this run did not qualify for a downloadable PDF because one or more selected devices did not have enough usable telemetry coverage."),
    true,
  );
  assert.equal(
    wastePageSource.includes("This run completed with coverage warnings, so a PDF artifact was not generated."),
    true,
  );
});

test("waste analysis polling avoids duplicate status fetches for the same selected job", () => {
  assert.equal(
    wastePageSource.includes("const jobsToRefresh = Array.from(new Set([submittedJobId, selectedJobId].filter((jobId): jobId is string => Boolean(jobId))));"),
    true,
  );
  assert.equal(wastePageSource.includes("for (const jobId of jobsToRefresh) {"), true);
});

test("waste analysis history jumps selected details into view and keeps detail actions below the table", () => {
  assert.equal(wastePageSource.includes("const [detailJumpRequest, setDetailJumpRequest] = useState<{ jobId: string; nonce: number } | null>(null);"), true);
  assert.equal(wastePageSource.includes("const WASTE_HISTORY_PAGE_SIZE = 5;"), true);
  assert.equal(wastePageSource.includes("selectedJobPanelRef.current.scrollIntoView({ behavior: \"smooth\", block: \"start\" });"), true);
  assert.equal(wastePageSource.includes("selectedJobPanelRef.current.focus({ preventScroll: true });"), true);
  assert.equal(wastePageSource.includes("openWasteJobDetails"), true);
  assert.equal(wastePageSource.includes("setDetailJumpRequest({ jobId, nonce: Date.now() });"), true);
  assert.equal(wastePageSource.includes("Download PDF"), true);
  assert.equal(wastePageSource.includes("Loading waste analysis result..."), true);
  assert.equal(wastePageSource.includes("void onOpenResult(selectedStatus);"), true);
});

test("shared report date selector visibly highlights the chosen quick preset", () => {
  assert.equal(dateRangeSelectorSource.includes("const selectedPresetLabel = presets.find"), true);
  assert.equal(dateRangeSelectorSource.includes("aria-pressed={selectedPresetLabel === p.label}"), true);
  assert.equal(
    dateRangeSelectorSource.includes('"border-blue-500 bg-blue-50 text-blue-700 shadow-sm"'),
    true,
  );
});

test("energy report completed view keeps artifact download tied to backend readiness", () => {
  assert.equal(reportProgressSource.includes("onStatusChange?: (status: ReportStatus) => void;"), true);
  assert.equal(reportProgressSource.includes("onStatusChange?.(data);"), true);
  assert.equal(
    energyReportPageSource.includes("Download PDF will be ready from Report History shortly."),
    true,
  );
  assert.equal(
    energyReportPageSource.includes("(submittedStatus?.artifact_ready || submittedStatus?.download_ready) ?"),
    true,
  );
});

test("report history supports refresh and pagination for growing job lists", () => {
  assert.equal(reportsPageSource.includes("const REPORT_HISTORY_PAGE_SIZE = 5;"), true);
  assert.equal(reportsPageSource.includes('const [historyPage, setHistoryPage] = useState(0);'), true);
  assert.equal(reportsPageSource.includes('const [hasMoreHistory, setHasMoreHistory] = useState(false);'), true);
  assert.equal(reportsPageSource.includes("Refresh"), true);
  assert.equal(reportsPageSource.includes("Page {historyPage + 1}"), true);
  assert.equal(reportsPageSource.includes("Previous"), true);
  assert.equal(reportsPageSource.includes("Next"), true);
});

test("report history jumps selected details into view and exposes row-level download actions", () => {
  assert.equal(reportsPageSource.includes("const [detailJumpTargetId, setDetailJumpTargetId] = useState<string | null>(null);"), true);
  assert.equal(reportsPageSource.includes("selectedReportPanelRef.current.scrollIntoView({ behavior: \"smooth\", block: \"start\" });"), true);
  assert.equal(reportsPageSource.includes("selectedReportPanelRef.current.focus({ preventScroll: true });"), true);
  assert.equal(reportsPageSource.includes("Download ready"), true);
  assert.equal(reportsPageSource.includes('{downloadingId === item.report_id ? \"Downloading...\" : \"Download\"}'), true);
  assert.equal(reportsPageSource.includes("isReportDownloadReady(selectedReportDetail)"), true);
});

test("reports history/detail no longer treats blocked coverage as a clean success", () => {
  assert.equal(
    reportPresentationSource.includes("This artifact remains available for audit, but this report is not usable for business decisions."),
    true,
  );
  assert.equal(
    reportPresentationSource.includes("Review the coverage warnings before using the downloaded artifact for business decisions."),
    true,
  );
  assert.equal(reportsPageSource.includes("Coverage state"), true);
  assert.equal(
    reportsPageSource.includes("This report finished successfully. Use the actions below to review the output or download the artifact when it is ready."),
    false,
  );
});

test("job summary helpers are wired for queue position, eta, and readiness wording", () => {
  assert.equal(asyncJobPresentationSource.includes("Queue position ${status.queue_position + 1}"), true);
  assert.equal(asyncJobPresentationSource.includes("Estimated wait"), true);
  assert.equal(asyncJobPresentationSource.includes("Estimated completion"), true);
  assert.equal(asyncJobPresentationSource.includes("return `${minutes} min`;"), true);
});

test("queue and worker admission errors map to clean customer-facing messages", () => {
  assert.equal(asyncJobPresentationSource.includes('errorCode === "WORKER_UNAVAILABLE"'), true);
  assert.equal(
    asyncJobPresentationSource.includes(
      'return "Processing is temporarily unavailable right now. Please try again in a moment."',
    ),
    true,
  );
  assert.equal(asyncJobPresentationSource.includes('errorCode === "QUEUE_OVERLOADED" || errorCode === "TENANT_QUEUE_LIMIT_REACHED"'), true);
  assert.equal(
    asyncJobPresentationSource.includes(
      'return "The processing queue is busy right now. Please try again shortly."',
    ),
    true,
  );
});
