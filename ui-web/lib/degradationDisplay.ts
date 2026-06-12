export type DegradationBadgeVariant = "success" | "warning" | "info" | "default" | "error";

export interface DegradationDisplayState {
  statusBadgeVariant: DegradationBadgeVariant;
  statusLabel: string;
  staleNote: string | null;
  lowConfidenceNote: string | null;
  showScore: boolean;
  showDetails: boolean;
  scoreTone: string;
}

const LOW_CONFIDENCE_THRESHOLD = 0.5;

export const SIGNAL_OPERATOR_LABELS: Record<string, string> = {
  current_variability_drift: "Current variability above baseline",
  power_factor_drop: "Power factor below baseline",
  abnormal_power_draw: "Power draw deviating from baseline",
  phase_imbalance_drift: "Phase imbalance above baseline",
  trend_worsening: "Degradation trend worsening",
};

export const SIGNAL_TO_ANOMALY_FIELD: Record<string, string | null> = {
  current_variability_drift: "current_avg",
  power_factor_drop: "power_factor",
  abnormal_power_draw: "power",
  phase_imbalance_drift: "phase_imbalance",
  trend_worsening: null,
};

export const SIGNAL_CORRELATION_NOTE: Record<string, string> = {
  current_variability_drift: "compared with current anomalies",
  power_factor_drop: "same as power factor anomalies",
  abnormal_power_draw: "same as power anomalies",
  phase_imbalance_drift: "same as phase imbalance anomalies",
};

export const SIGNAL_UNITS: Record<string, string> = {
  current_variability_drift: "A std dev",
  power_factor_drop: "",
  abnormal_power_draw: "kW",
  phase_imbalance_drift: "",
  trend_worsening: "",
};

export const STATUS_DESCRIPTIONS: Record<string, string> = {
  healthy: "Machine operating within normal parameters",
  watch: "Early signs of deviation from baseline",
  warning: "Notable degradation detected — monitor closely",
  critical: "Significant risk — investigate promptly",
};

export const SIGNAL_MAX_DRIFT: Record<string, number> = {
  current_variability_drift: 3.0,
  power_factor_drop: 1.0,
  abnormal_power_draw: 3.0,
  phase_imbalance_drift: 3.0,
  trend_worsening: 3.0,
};

export const BASELINE_QUALITY_DESCRIPTIONS: Record<string, string> = {
  high: "Well-established baseline",
  medium: "Baseline still stabilizing",
  low: "Limited baseline data",
  insufficient: "Insufficient baseline — score may be unreliable",
};

export const CONFIDENCE_DESCRIPTIONS: Record<string, string> = {
  high: "High confidence — score reflects true condition",
  medium: "Moderate confidence — score direction is reliable",
  low: "Low confidence — score may not reflect true condition",
};

export const SCORE_DIRECTION_LABEL = "Lower is better (1 = normal, 10 = critical)";

export const ALL_SIGNALS = [
  "current_variability_drift",
  "power_factor_drop",
  "abnormal_power_draw",
  "phase_imbalance_drift",
  "trend_worsening",
] as const;

export interface ContributionDisplay {
  signal: string;
  operatorLabel: string;
  barPct: number;
  driftMagnitude: "none" | "low" | "moderate" | "high";
  available: boolean;
  observedValue: number | null;
  baselineValue: number | null;
  rawDrift: number | null;
  weightPct: number;
}

export function translateContribution(
  signal: string,
  drift: number,
  available: boolean = true,
  observedValue: number | null = null,
  baselineValue: number | null = null,
  rawDrift: number | null = null,
  weight: number = 0,
): ContributionDisplay {
  const operatorLabel = SIGNAL_OPERATOR_LABELS[signal] || signal.replace(/_/g, " ");
  const maxDrift = SIGNAL_MAX_DRIFT[signal] ?? 3.0;
  const absDrift = Math.abs(drift);
  const barPct = Math.min(100, Math.round((absDrift / maxDrift) * 100));
  let driftMagnitude: ContributionDisplay["driftMagnitude"] = "none";
  if (absDrift > 0) driftMagnitude = "low";
  if (absDrift > maxDrift * 0.33) driftMagnitude = "moderate";
  if (absDrift > maxDrift * 0.66) driftMagnitude = "high";
  return { signal, operatorLabel, barPct, driftMagnitude, available, observedValue, baselineValue, rawDrift, weightPct: Math.round(weight * 100) };
}

export function deriveDegradationStatusDisplay(status: string | null | undefined): DegradationDisplayState {
  const s = (status || "").toLowerCase();

  if (s === "healthy") {
    return { statusBadgeVariant: "success", statusLabel: "Healthy", staleNote: null, lowConfidenceNote: null, showScore: true, showDetails: true, scoreTone: "text-emerald-600" };
  }
  if (s === "watch") {
    return { statusBadgeVariant: "info", statusLabel: "Watch", staleNote: null, lowConfidenceNote: null, showScore: true, showDetails: true, scoreTone: "text-blue-600" };
  }
  if (s === "warning") {
    return { statusBadgeVariant: "warning", statusLabel: "Warning", staleNote: null, lowConfidenceNote: null, showScore: true, showDetails: true, scoreTone: "text-amber-600" };
  }
  if (s === "critical") {
    return { statusBadgeVariant: "error", statusLabel: "Critical", staleNote: null, lowConfidenceNote: null, showScore: true, showDetails: true, scoreTone: "text-red-600" };
  }
  return { statusBadgeVariant: "default", statusLabel: "", staleNote: null, lowConfidenceNote: null, showScore: false, showDetails: false, scoreTone: "text-slate-400" };
}

export function deriveDegradationStateDisplay(state: string | null | undefined): {
  stateLabel: string;
  stateBadgeVariant: DegradationBadgeVariant;
  showScore: boolean;
  showDetails: boolean;
  scoreTone: string;
} {
  const normalized = (state || "unavailable").toLowerCase();

  if (normalized === "scored") {
    return { stateLabel: null as unknown as string, stateBadgeVariant: "success", showScore: true, showDetails: true, scoreTone: "text-emerald-600" };
  }
  if (normalized === "stale") {
    return { stateLabel: "Stale", stateBadgeVariant: "warning", showScore: true, showDetails: true, scoreTone: "text-amber-600" };
  }
  if (normalized === "learning") {
    return { stateLabel: "Learning baseline", stateBadgeVariant: "info", showScore: false, showDetails: false, scoreTone: "text-slate-400" };
  }
  return { stateLabel: "Not available yet", stateBadgeVariant: "default", showScore: false, showDetails: false, scoreTone: "text-slate-400" };
}

export function deriveDegradationDisplay(state: string | null | undefined, status: string | null | undefined, confidence: number | null | undefined = null): DegradationDisplayState {
  const normalized = (state || "unavailable").toLowerCase();

  if (normalized === "scored" || normalized === "stale") {
    const statusDisplay = deriveDegradationStatusDisplay(status);
    const lowConfidence = confidence != null && Number.isFinite(confidence) && confidence < LOW_CONFIDENCE_THRESHOLD;
    return {
      ...statusDisplay,
      staleNote: normalized === "stale" ? "Stale — data may be outdated" : null,
      lowConfidenceNote: lowConfidence ? "Low confidence: insufficient stable telemetry" : null,
      showScore: true,
      showDetails: true,
    };
  }
  if (normalized === "learning") {
    return { statusBadgeVariant: "info", statusLabel: "Learning baseline", staleNote: null, lowConfidenceNote: null, showScore: false, showDetails: false, scoreTone: "text-slate-400" };
  }
  return { statusBadgeVariant: "default", statusLabel: "Not available yet", staleNote: null, lowConfidenceNote: null, showScore: false, showDetails: false, scoreTone: "text-slate-400" };
}

export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return "—";
  return `${score.toFixed(1)}/10`;
}

export function formatConfidence(confidence: number | null | undefined): string {
  if (confidence === null || confidence === undefined || !Number.isFinite(confidence)) return "—";
  return `${Math.round(confidence * 100)}%`;
}

export function confidenceDescription(confidence: number | null | undefined): string {
  if (confidence === null || confidence === undefined || !Number.isFinite(confidence)) return "";
  if (confidence >= 0.85) return CONFIDENCE_DESCRIPTIONS.high;
  if (confidence >= 0.5) return CONFIDENCE_DESCRIPTIONS.medium;
  return CONFIDENCE_DESCRIPTIONS.low;
}

export function baselineQualityDescription(quality: string | null | undefined): string {
  if (!quality) return "";
  return BASELINE_QUALITY_DESCRIPTIONS[quality.toLowerCase()] || "";
}

export function formatUpdatedMinutesAgo(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return "";
  if (minutes < 1) return "Updated just now";
  if (minutes < 60) return `Updated ${Math.round(minutes)}m ago`;
  const hours = Math.round(minutes / 60);
  return `Updated ${hours}h ago`;
}

export function formatSignalCompleteness(completeness: number | null | undefined): string {
  if (completeness === null || completeness === undefined || !Number.isFinite(completeness)) return "";
  return `${Math.round(completeness * 100)}% signal coverage`;
}

export function contributionBarColor(magnitude: ContributionDisplay["driftMagnitude"], available: boolean = true): string {
  if (!available) return "bg-slate-200";
  if (magnitude === "high") return "bg-red-500";
  if (magnitude === "moderate") return "bg-amber-500";
  if (magnitude === "low") return "bg-blue-400";
  return "bg-slate-200";
}

export function buildContributionList(
  contributions: Array<{ signal: string; drift: number; available: boolean; observed_value?: number | null; baseline_value?: number | null; raw_drift?: number | null; weight?: number }>,
): ContributionDisplay[] {
  const contributedSignals = new Set(contributions.map((c) => c.signal));
  const results = contributions
    .map((c) => translateContribution(c.signal, c.drift, c.available, c.observed_value ?? null, c.baseline_value ?? null, c.raw_drift ?? null, c.weight ?? 0))
    .sort((a, b) => {
      if (a.available !== b.available) return a.available ? -1 : 1;
      return b.barPct - a.barPct;
    });
  for (const signal of ALL_SIGNALS) {
    if (!contributedSignals.has(signal)) {
      results.push(translateContribution(signal, 0, false));
    }
  }
  return results;
}

export function formatObservedBaseline(
  signal: string,
  observed: number | null | undefined,
  baseline: number | null | undefined,
): string {
  if (observed == null || baseline == null) return "";
  const unit = SIGNAL_UNITS[signal] || "";
  const unitStr = unit ? ` ${unit}` : "";
  return `Observed: ${observed.toFixed(2)}${unitStr} · Baseline: ${baseline.toFixed(2)}${unitStr}`;
}

export function formatRawDriftPct(rawDrift: number | null | undefined): string {
  if (rawDrift == null || !Number.isFinite(rawDrift)) return "";
  const pct = rawDrift * 100;
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${pct.toFixed(0)}% drift`;
}

export const STATUS_THRESHOLD_LINES = [
  { value: 3, status: "watch", color: "#3b82f6", label: "Watch" },
  { value: 5, status: "warning", color: "#f59e0b", label: "Warning" },
  { value: 7, status: "critical", color: "#ef4444", label: "Critical" },
];

export function deriveTopReasons(
  contributions: Array<{ signal: string; drift: number; weight: number; available: boolean }>,
  maxReasons: number = 3,
): string[] {
  const active = contributions
    .filter((c) => c.available && c.drift > 0)
    .sort((a, b) => (b.drift * b.weight) - (a.drift * a.weight));
  return active.slice(0, maxReasons).map((c) => SIGNAL_OPERATOR_LABELS[c.signal] || c.signal.replace(/_/g, " "));
}
