import { ApiError } from "./analyticsApi";
import { ReportApiError } from "./reportApi";
import type { TelemetryCoverageResult } from "./telemetryCoverage";
import { getTelemetryCoverageLabel, getTelemetryCoverageSummary } from "./telemetryCoverage";

export type FleetProgress = {
  selected_device_count?: number | null;
  child_jobs_total?: number | null;
  queued_devices?: number | null;
  running_devices?: number | null;
  completed_devices?: number | null;
  failed_devices?: number | null;
  skipped_devices?: number | null;
  coverage_pct?: number | null;
};

export type FleetOutcomeTone = "info" | "good" | "warn" | "bad";

export type FleetOutcomeSummary = {
  headline: string;
  summary: string;
  action: string;
  tone: FleetOutcomeTone;
  isPartial: boolean;
  isComplete: boolean;
  hasUsableCoverage: boolean;
};

export function formatJobSeconds(seconds?: number | null): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} sec`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins > 0 ? `${hours} hr ${mins} min` : `${hours} hr`;
}

export function formatJobStatusSummary(status: {
  status: string;
  message?: string | null;
  phase_label?: string | null;
  queue_position?: number | null;
  estimated_wait_seconds?: number | null;
  estimated_completion_seconds?: number | null;
  activity_state?: "active" | "stalled" | "unknown" | null;
  eta_reliable?: boolean | null;
}): string {
  const phaseText = status.phase_label?.trim() || status.message?.trim() || "Processing";
  if (status.status === "pending") {
    const queue = typeof status.queue_position === "number" ? `Queue position ${status.queue_position + 1}` : "Queued";
    const waitText = formatJobSeconds(status.estimated_wait_seconds);
    return waitText ? `${queue} · Estimated wait ${waitText}` : queue;
  }
  if (status.status === "running") {
    const truthfulnessNote = getRunningJobTruthfulnessNote(status);
    const completion = formatJobSeconds(status.estimated_completion_seconds);
    if (completion && status.eta_reliable !== false) {
      return `${phaseText} · Estimated completion ${completion}`;
    }
    return truthfulnessNote ? `${phaseText} · ${truthfulnessNote}` : phaseText;
  }
  return phaseText;
}

export function shouldShowRunningJobEta(status: {
  status: string;
  estimated_completion_seconds?: number | null;
  eta_reliable?: boolean | null;
}): boolean {
  return (
    status.status === "running"
    && status.eta_reliable !== false
    && typeof status.estimated_completion_seconds === "number"
    && Number.isFinite(status.estimated_completion_seconds)
    && status.estimated_completion_seconds >= 0
  );
}

export function getRunningJobTruthfulnessNote(status: {
  status: string;
  activity_state?: "active" | "stalled" | "unknown" | null;
  eta_reliable?: boolean | null;
}): string | null {
  if (status.status !== "running") return null;
  if (status.activity_state === "stalled") {
    return "No recent worker heartbeat detected";
  }
  if (status.activity_state === "active" && status.eta_reliable === false) {
    return "Worker is still active. Final timing can vary";
  }
  if (status.activity_state === "active") {
    return "Worker heartbeat received recently";
  }
  if (status.eta_reliable === false) {
    return "Timing is being recalculated";
  }
  return null;
}

export function formatCountLabel(count: number | null | undefined, singular: string, plural = `${singular}s`): string | null {
  if (typeof count !== "number" || !Number.isFinite(count) || count < 0) return null;
  return `${count} ${count === 1 ? singular : plural}`;
}

export function formatFleetProgressBadges(fleet?: FleetProgress | null): string[] {
  if (!fleet) return [];
  const badges = [
    formatCountLabel(fleet.selected_device_count, "device", "devices selected"),
    formatCountLabel(fleet.queued_devices, "queued device", "queued devices"),
    formatCountLabel(fleet.running_devices, "running device", "running devices"),
    formatCountLabel(fleet.completed_devices, "completed device", "completed devices"),
    formatCountLabel(fleet.failed_devices, "failed device", "failed devices"),
    formatCountLabel(fleet.skipped_devices, "skipped device", "skipped devices"),
  ].filter((value): value is string => Boolean(value));

  if (typeof fleet.coverage_pct === "number" && Number.isFinite(fleet.coverage_pct)) {
    badges.push(`${fleet.coverage_pct.toFixed(1)}% coverage`);
  }
  return badges;
}

export function getFleetAcceptedMessage(selectedDevices: number): string {
  return selectedDevices === 1
    ? "Analysis continues in the background while you keep using the platform."
    : "Some devices may run immediately while others wait in queue as capacity becomes available. You can continue using the platform while this fleet analysis progresses.";
}

export function getFleetHistorySummary(fleet?: FleetProgress | null): string | null {
  if (!fleet) return null;
  const selected = typeof fleet.selected_device_count === "number" ? fleet.selected_device_count : null;
  const completed = typeof fleet.completed_devices === "number" ? fleet.completed_devices : null;
  const running = typeof fleet.running_devices === "number" ? fleet.running_devices : null;
  const queued = typeof fleet.queued_devices === "number" ? fleet.queued_devices : null;
  const failed = typeof fleet.failed_devices === "number" ? fleet.failed_devices : null;
  const skipped = typeof fleet.skipped_devices === "number" ? fleet.skipped_devices : null;

  const parts: string[] = [];
  if (selected !== null) parts.push(`${selected} devices selected`);
  if (completed !== null) parts.push(`${completed} completed`);
  if (running) parts.push(`${running} running`);
  if (queued) parts.push(`${queued} queued`);
  if (failed) parts.push(`${failed} failed`);
  if (skipped) parts.push(`${skipped} skipped`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function getFleetOutcomeSummary(
  status: string,
  fleet?: FleetProgress | null,
  options?: {
    resultReady?: boolean;
  },
): FleetOutcomeSummary | null {
  if (!fleet) return null;

  const selected = Math.max(0, Number(fleet.selected_device_count ?? 0));
  const completed = Math.max(0, Number(fleet.completed_devices ?? 0));
  const failed = Math.max(0, Number(fleet.failed_devices ?? 0));
  const skipped = Math.max(0, Number(fleet.skipped_devices ?? 0));
  const running = Math.max(0, Number(fleet.running_devices ?? 0));
  const queued = Math.max(0, Number(fleet.queued_devices ?? 0));
  const knownCoverage =
    typeof fleet.coverage_pct === "number" && Number.isFinite(fleet.coverage_pct)
      ? Math.max(0, fleet.coverage_pct)
      : selected > 0
        ? (completed / selected) * 100
        : 0;
  const incompleteCount = failed + skipped;

  if (status === "pending") {
    return {
      headline: "Queued for staged processing",
      summary:
        selected > 0
          ? `${selected} selected devices are waiting for fleet processing capacity.`
          : "This fleet analysis is waiting for processing capacity.",
      action: "You can leave this page and return from Analysis History as devices begin processing.",
      tone: "info",
      isPartial: false,
      isComplete: false,
      hasUsableCoverage: false,
    };
  }

  if (status === "running") {
    return {
      headline: "Fleet result is still building",
      summary:
        selected > 0
          ? `${completed} of ${selected} selected devices have completed so far, while other devices may still be queued or running.`
          : "Some fleet devices may still be queued or running.",
      action: "You can continue using the platform and return here as more devices finish.",
      tone: completed > 0 ? "warn" : "info",
      isPartial: completed > 0,
      isComplete: false,
      hasUsableCoverage: completed > 0,
    };
  }

  if (status === "failed") {
    return {
      headline: "Fleet result is not available yet",
      summary:
        completed > 0
          ? `${completed} devices completed before this fleet workflow stopped, but the overall fleet result could not be finalized.`
          : "This fleet workflow did not produce a usable fleet result.",
      action: "Review the coverage details below, then rerun the fleet analysis if you still need a consolidated result.",
      tone: completed > 0 ? "warn" : "bad",
      isPartial: completed > 0,
      isComplete: false,
      hasUsableCoverage: completed > 0,
    };
  }

  if (status === "completed" && options?.resultReady === false) {
    return {
      headline: "Fleet aggregation is finalizing",
      summary:
        selected > 0
          ? `${completed} of ${selected} selected devices are already reflected in the fleet workflow, but the result is still being finalized.`
          : "This fleet workflow has finished processing and is finalizing the result.",
      action: "Check back in a moment. The fleet result will appear here as soon as aggregation is complete.",
      tone: "info",
      isPartial: completed > 0 && knownCoverage < 100,
      isComplete: false,
      hasUsableCoverage: completed > 0,
    };
  }

  if (completed <= 0) {
    return {
      headline: "No usable fleet coverage",
      summary:
        selected > 0
          ? `None of the ${selected} selected devices completed with a usable analytics result.`
          : "This fleet workflow did not produce a usable analytics result.",
      action: "Review the failed or skipped devices, adjust the date range if needed, and rerun the fleet analysis.",
      tone: "bad",
      isPartial: false,
      isComplete: false,
      hasUsableCoverage: false,
    };
  }

  if (selected > 0 && completed >= selected && incompleteCount === 0) {
    return {
      headline: "Complete fleet result",
      summary: `All ${selected} selected devices completed successfully and are included in this fleet result.`,
      action: "Review the fleet summary, then reopen device-level results if any machines need attention.",
      tone: "good",
      isPartial: false,
      isComplete: true,
      hasUsableCoverage: true,
    };
  }

  return {
    headline: "Partial fleet result",
    summary:
      selected > 0
        ? `${completed} of ${selected} selected devices completed successfully. ${incompleteCount} devices were skipped or failed.`
        : `${completed} devices completed successfully, while some other devices were skipped or failed.`,
    action:
      running > 0 || queued > 0
        ? "This fleet result is still growing as more devices finish. Check back again if you need broader coverage."
        : `This result is usable now, but coverage is ${knownCoverage.toFixed(1)}%. Review skipped or failed devices before deciding whether to rerun.`,
    tone: "warn",
    isPartial: true,
    isComplete: false,
    hasUsableCoverage: true,
  };
}

export function getUserFacingJobStatusLabel(status?: string | null, errorCode?: string | null): string {
  return getUserFacingJobStatusLabelWithCoverage({ status, error_code: errorCode });
}

export function getUserFacingJobStatusLabelWithCoverage(job?: {
  status?: string | null;
  error_code?: string | null;
  coverage_result?: TelemetryCoverageResult | null;
} | null): string {
  const coverageLabel = getTelemetryCoverageLabel(job?.coverage_result);
  if (coverageLabel && job?.coverage_result?.level !== "full_coverage") return coverageLabel;
  const status = job?.status;
  const errorCode = job?.error_code;
  if (status === "failed" && errorCode === "NO_TELEMETRY_IN_RANGE") return "No data";
  if (status === "pending") return "Queued";
  if (status === "running" || status === "processing") return "In progress";
  if (status === "completed") return "Ready";
  if (status === "failed") return "Needs attention";
  return "Preparing";
}

export function getJobFailureSummary(
  error: {
    error_code?: string | null;
    error_message?: string | null;
    message?: string | null;
  },
  fallback = "This job could not be completed. Please try again.",
): string {
  const coverageSummary = getTelemetryCoverageSummary((error as { coverage_result?: TelemetryCoverageResult | null }).coverage_result);
  if (coverageSummary) return coverageSummary;
  const code = error.error_code?.trim() || "";
  const message = error.error_message?.trim() || error.message?.trim() || "";
  const normalized = `${code} ${message}`.toLowerCase();

  if (code === "FEATURE_DISABLED") {
    return "This feature is not enabled for your organisation.";
  }
  if (code === "WORKER_UNAVAILABLE") {
    return "Processing is temporarily unavailable right now. Please try again in a moment.";
  }
  if (code === "QUEUE_OVERLOADED" || code === "TENANT_QUEUE_LIMIT_REACHED" || code === "QUEUE_UNAVAILABLE") {
    return "The processing queue is busy right now. Please try again shortly.";
  }
  if (code === "RESULT_NOT_READY") {
    return "This job is still finishing up. Check back in a moment.";
  }
  if (code === "NO_TELEMETRY_IN_RANGE") {
    return "No telemetry was available for the selected period.";
  }
  if (code === "DEVICE_NOT_FOUND") {
    return "The selected device could not be found for this analytics run.";
  }
  if (code === "DATASET_NOT_READY_TIMEOUT") {
    return "Telemetry preparation timed out for the selected period. Please try again shortly.";
  }
  if (code === "JOB_EXECUTION_TIMEOUT" || code === "STALE_WORKER_LEASE") {
    return "Analytics processing did not finish cleanly. Please try again.";
  }
  if (normalized.includes("no telemetry data")) {
    return "Not enough telemetry was available for the selected period.";
  }
  if (normalized.includes("no telemetry found in selected time range")) {
    return "No telemetry was available for the selected period.";
  }
  if (normalized.includes("feature") && normalized.includes("not enabled")) {
    return "This feature is not enabled for your organisation.";
  }
  if (normalized.includes("active analytics capacity limit")) {
    return "Analytics is temporarily at capacity for this organisation. Please try again shortly.";
  }
  if (normalized.includes("not completed")) {
    return "This job is still running. Check back again in a moment.";
  }

  return message || fallback;
}

export function getLongRunningJobErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError || error instanceof ReportApiError) {
    const structured = error.body as Record<string, unknown> | null;
    const errorCode =
      typeof structured?.error === "string"
        ? structured.error
        : typeof structured?.detail === "object" && structured.detail !== null && typeof (structured.detail as Record<string, unknown>).error === "string"
          ? String((structured.detail as Record<string, unknown>).error)
          : null;

    if (errorCode === "WORKER_UNAVAILABLE") {
      return "Processing is temporarily unavailable right now. Please try again in a moment.";
    }
    if (errorCode === "QUEUE_OVERLOADED" || errorCode === "TENANT_QUEUE_LIMIT_REACHED") {
      return "The processing queue is busy right now. Please try again shortly.";
    }
    if (errorCode === "QUEUE_UNAVAILABLE") {
      return "Processing is temporarily unavailable right now. Please try again shortly.";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : fallback;
}
