export type TelemetryCoverageLevel =
  | "full_coverage"
  | "partial_coverage"
  | "insufficient_coverage"
  | "no_coverage";

export type TelemetryCoverageResult = {
  level?: TelemetryCoverageLevel | null;
  coverage_pct?: number | null;
  selected_window_days?: number | null;
  covered_days?: number | null;
  selected_window_hours?: number | null;
  covered_duration_hours?: number | null;
  warnings?: string[] | null;
  minimum_requirements?: Record<string, unknown> | null;
  usable_devices?: string[] | null;
  skipped_devices?: Array<Record<string, unknown>> | null;
  usable_for_business_decisions?: boolean | null;
  artifact_generation_allowed?: boolean | null;
  terminal_status?: "business_complete" | "business_blocked" | string | null;
  message?: string | null;
};

export function isTelemetryCoverageLevel(value: unknown): value is TelemetryCoverageLevel {
  return (
    value === "full_coverage" ||
    value === "partial_coverage" ||
    value === "insufficient_coverage" ||
    value === "no_coverage"
  );
}

export function getTelemetryCoverageLabel(coverage?: TelemetryCoverageResult | null): string | null {
  switch (coverage?.level) {
    case "full_coverage":
      return "Full coverage";
    case "partial_coverage":
      return "Partial result";
    case "insufficient_coverage":
      return "Insufficient coverage";
    case "no_coverage":
      return "No data";
    default:
      return null;
  }
}

export function getTelemetryCoverageSummary(coverage?: TelemetryCoverageResult | null): string | null {
  if (!coverage || !isTelemetryCoverageLevel(coverage.level)) return null;
  if (coverage.message?.trim()) return coverage.message.trim();
  switch (coverage.level) {
    case "full_coverage":
      return "Telemetry coverage is sufficient for the selected range.";
    case "partial_coverage":
      return "This result is usable, but telemetry coverage is partial. Review coverage metadata before making business decisions.";
    case "insufficient_coverage":
      return "Telemetry coverage is insufficient for a trustworthy business result.";
    case "no_coverage":
      return "No telemetry was available for the selected range.";
  }
}

export function getTelemetryCoverageTone(coverage?: TelemetryCoverageResult | null): "good" | "warn" | "bad" | "info" {
  switch (coverage?.level) {
    case "full_coverage":
      return "good";
    case "partial_coverage":
      return "warn";
    case "insufficient_coverage":
    case "no_coverage":
      return "bad";
    default:
      return "info";
  }
}
