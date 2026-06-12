import {
  formatJobSeconds,
  getJobFailureSummary,
  getUserFacingJobStatusLabelWithCoverage,
} from "./asyncJobPresentation.ts";
import {
  getTelemetryCoverageLabel,
  getTelemetryCoverageSummary,
  getTelemetryCoverageTone,
  type TelemetryCoverageResult,
} from "./telemetryCoverage.ts";

type ReportPresentationJob = {
  status?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  message?: string | null;
  coverage_result?: TelemetryCoverageResult | null;
  result_ready?: boolean | null;
  artifact_ready?: boolean | null;
  download_ready?: boolean | null;
  queue_position?: number | null;
  estimated_wait_seconds?: number | null;
  estimated_completion_seconds?: number | null;
  phase_label?: string | null;
};

type ReportCoverageCallout = {
  label: string;
  summary: string;
  tone: "good" | "warn" | "bad" | "info";
};

export type ReportStatePresentation = {
  statusLabel: string;
  historyDetail: string;
  detailSummary: string;
  coverageCallout: ReportCoverageCallout | null;
};

function isBusinessBlockedCoverage(coverage?: TelemetryCoverageResult | null): boolean {
  if (!coverage) return false;
  if (coverage.usable_for_business_decisions === false) return true;
  return coverage.level === "no_coverage" || coverage.level === "insufficient_coverage";
}

function isPartialCoverage(coverage?: TelemetryCoverageResult | null): boolean {
  return coverage?.level === "partial_coverage";
}

function getCoverageCallout(coverage?: TelemetryCoverageResult | null): ReportCoverageCallout | null {
  const label = getTelemetryCoverageLabel(coverage);
  const summary = getTelemetryCoverageSummary(coverage);
  if (!label || !summary || coverage?.level === "full_coverage") {
    return null;
  }

  return {
    label,
    summary,
    tone: getTelemetryCoverageTone(coverage),
  };
}

function getCompletedHistoryDetail(job: ReportPresentationJob): string {
  const coverage = job.coverage_result;
  const coverageSummary = getTelemetryCoverageSummary(coverage);

  if (isBusinessBlockedCoverage(coverage)) {
    return coverageSummary ?? "This report is not usable for business decisions.";
  }

  if (isPartialCoverage(coverage)) {
    return coverageSummary ?? "This result is usable with coverage warnings.";
  }

  if (job.artifact_ready || job.download_ready) {
    return "Ready to download";
  }

  if (job.result_ready) {
    return "Result ready to review";
  }

  return "Completed";
}

function getCompletedDetailSummary(job: ReportPresentationJob): string {
  const coverage = job.coverage_result;
  const coverageSummary = getTelemetryCoverageSummary(coverage);

  if (isBusinessBlockedCoverage(coverage)) {
    if (job.artifact_ready || job.download_ready) {
      return `${coverageSummary ?? "This report is not usable for business decisions."} This artifact remains available for audit, but this report is not usable for business decisions.`;
    }
    if (job.result_ready) {
      return `${coverageSummary ?? "This report is not usable for business decisions."} The current output is available to review, but this report is not usable for business decisions.`;
    }
    return `${coverageSummary ?? "This report is not usable for business decisions."} This report is not usable for business decisions.`;
  }

  if (isPartialCoverage(coverage)) {
    if (job.artifact_ready || job.download_ready) {
      return `${coverageSummary ?? "This result is usable with coverage warnings."} Review the coverage warnings before using the downloaded artifact for business decisions.`;
    }
    if (job.result_ready) {
      return `${coverageSummary ?? "This result is usable with coverage warnings."} Review the coverage warnings before using this output for business decisions.`;
    }
    return coverageSummary ?? "This result is usable with coverage warnings.";
  }

  if (!job.result_ready) {
    return "This report completed successfully. Results are still being finalized for this history view.";
  }

  if (job.artifact_ready || job.download_ready) {
    return "This report completed successfully. The output is ready to review or download.";
  }

  return "This report completed successfully. The output is ready to review while the download artifact is still being prepared.";
}

export function getReportStatePresentation(job: ReportPresentationJob): ReportStatePresentation {
  const status = job.status;

  if (status === "pending") {
    const queueText = job.queue_position != null ? `Queue position ${job.queue_position + 1}` : "Queued";
    const waitText = formatJobSeconds(job.estimated_wait_seconds);
    return {
      statusLabel: getUserFacingJobStatusLabelWithCoverage(job),
      historyDetail: waitText ? `${queueText} · estimated wait ${waitText}` : queueText,
      detailSummary: "This report is waiting for processing capacity and has not started running yet.",
      coverageCallout: null,
    };
  }

  if (status === "running" || status === "processing") {
    const phaseText = job.phase_label?.trim() || "Processing";
    const etaText = formatJobSeconds(job.estimated_completion_seconds);
    return {
      statusLabel: getUserFacingJobStatusLabelWithCoverage(job),
      historyDetail: etaText ? `${phaseText} · ETA ${etaText}` : phaseText,
      detailSummary: "This report is still running in the background. The latest backend status is shown here.",
      coverageCallout: getCoverageCallout(job.coverage_result),
    };
  }

  if (status === "failed") {
    return {
      statusLabel: getUserFacingJobStatusLabelWithCoverage(job),
      historyDetail: getJobFailureSummary(job, "Processing could not be completed"),
      detailSummary: getJobFailureSummary(job, "This report could not be completed."),
      coverageCallout: getCoverageCallout(job.coverage_result),
    };
  }

  return {
    statusLabel: getUserFacingJobStatusLabelWithCoverage(job),
    historyDetail: getCompletedHistoryDetail(job),
    detailSummary: getCompletedDetailSummary(job),
    coverageCallout: getCoverageCallout(job.coverage_result),
  };
}
