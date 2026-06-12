import type {
  AnomalyFormattedResult,
  FailureFormattedResult,
} from "./analyticsApi";

type CustomerFacingAnalyticsResult =
  | AnomalyFormattedResult
  | FailureFormattedResult;

export interface AnalyticsConfidenceSummaryView {
  title: string;
  level: string;
  evidenceStrength: string;
  summary: string;
  interpretation: string;
  recommendedAction: string;
  factors: string[];
}

const INTERNAL_ANALYTICS_TEXT = [
  /\bxgboost\b/i,
  /\blstm(?:_autoencoder|_classifier)?\b/i,
  /\bisolation_forest\b/i,
  /\bdegrade_tracker\b/i,
  /\bcusum\b/i,
  /\bhybrid_ensemble\b/i,
  /\b\d+\s*\/\s*\d+\s+models?\s+agree\b/i,
  /\bmodels?\s+agree\b/i,
  /\bensemble\b/i,
];

function normalizeLevel(value: string | undefined): string {
  const level = value?.trim();
  return level && level.length > 0 ? level : "Moderate";
}

function evidenceStrengthForLevel(level: string): string {
  const normalized = level.trim().toLowerCase();
  if (normalized === "very high" || normalized === "high") return "Strong";
  if (normalized === "moderate" || normalized === "medium") return "Moderate";
  if (normalized === "low") return "Developing";
  return "Limited";
}

export function sanitizeAnalyticsNarrative(
  value: string | undefined,
  fallback: string,
): string {
  const candidate = value?.trim();
  if (!candidate) return fallback;
  const hasInternalDetail = INTERNAL_ANALYTICS_TEXT.some((pattern) =>
    pattern.test(candidate),
  );
  return hasInternalDetail ? fallback : candidate;
}

function fallbackSummary(result: CustomerFacingAnalyticsResult): string {
  if (result.analysis_type === "anomaly_detection") {
    return result.summary.total_anomalies > 0
      ? "Telemetry patterns show abnormal machine behavior that warrants review."
      : "No material anomaly pattern was identified in the selected period.";
  }
  return result.summary.failure_probability_pct >= 35
    ? "Current machine behavior suggests a meaningful increase in maintenance risk."
    : "Current machine behavior remains within an acceptable operating profile.";
}

function fallbackInterpretation(result: CustomerFacingAnalyticsResult): string {
  if (result.analysis_type === "anomaly_detection") {
    return `Health impact is currently ${result.summary.health_impact.toLowerCase()} based on the analyzed telemetry.`;
  }
  return `Maintenance urgency is ${result.summary.maintenance_urgency.toLowerCase()} with an estimated remaining life of ${result.summary.estimated_remaining_life}.`;
}

function fallbackAction(result: CustomerFacingAnalyticsResult): string {
  if (result.analysis_type === "anomaly_detection") {
    return (
      result.reasoning?.recommended_action ??
      result.recommendations[0]?.action ??
      "Continue monitoring and schedule inspection if conditions persist."
    );
  }
  return (
    result.confidence_summary?.recommended_action ??
    result.recommended_actions[0]?.action ??
    "Continue monitoring and schedule a maintenance review."
  );
}

function fallbackFactors(result: CustomerFacingAnalyticsResult): string[] {
  if (result.analysis_type === "anomaly_detection") {
    return (
      result.reasoning?.affected_parameters?.slice(0, 3) ??
      result.parameter_breakdown.slice(0, 3).map((item) => item.parameter)
    );
  }
  return (
    result.confidence_summary?.factors?.slice(0, 3) ??
    result.reasoning?.top_risk_factors?.slice(0, 3) ??
    result.risk_factors.slice(0, 3).map((item) => item.parameter)
  );
}

export function getAnalyticsConfidenceSummary(
  result: CustomerFacingAnalyticsResult,
): AnalyticsConfidenceSummaryView {
  const providedSummary = result.confidence_summary;
  const level = normalizeLevel(
    providedSummary?.level ??
      result.confidence?.level ??
      result.reasoning?.confidence,
  );

  return {
    title: providedSummary?.title ?? "Analysis Confidence",
    level,
    evidenceStrength:
      providedSummary?.evidence_strength ?? evidenceStrengthForLevel(level),
    summary: sanitizeAnalyticsNarrative(
      providedSummary?.summary ?? result.reasoning?.summary,
      fallbackSummary(result),
    ),
    interpretation:
      sanitizeAnalyticsNarrative(
        providedSummary?.interpretation,
        fallbackInterpretation(result),
      ),
    recommendedAction: sanitizeAnalyticsNarrative(
      fallbackAction(result),
      "Continue monitoring and follow the recommended maintenance plan.",
    ),
    factors: fallbackFactors(result),
  };
}
