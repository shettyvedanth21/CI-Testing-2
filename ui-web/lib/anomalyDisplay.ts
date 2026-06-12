export type AnomalyBadgeVariant = "success" | "warning" | "info" | "default" | "error";

export interface AnomalyDisplayState {
  stateBadgeVariant: AnomalyBadgeVariant;
  stateLabel: string;
  staleNote: string | null;
  showCounts: boolean;
  showDetails: boolean;
}

export const SIGNAL_FIELD_LABELS: Record<string, string> = {
  current_avg: "Current (magnitude)",
  power: "Power draw",
  power_factor: "Power factor",
  voltage_avg: "Voltage",
  phase_imbalance: "Phase imbalance",
};

export const ANOMALY_FIELD_TO_SIGNAL: Record<string, string | null> = {
  current_avg: "current_variability_drift",
  power: "abnormal_power_draw",
  power_factor: "power_factor_drop",
  voltage_avg: null,
  phase_imbalance: "phase_imbalance_drift",
};

export const ANOMALY_TYPE_LABELS: Record<string, string> = {
  deviation: "Deviation from baseline",
  persistent: "Persistent anomaly",
  trend: "Worsening trend",
};

export const SEVERITY_LABELS: Record<string, { short: string; tone: string; bg: string }> = {
  severe: { short: "Severe", tone: "text-red-600", bg: "bg-red-50 border-red-200" },
  strong: { short: "Strong", tone: "text-amber-600", bg: "bg-amber-50 border-amber-200" },
  mild: { short: "Mild", tone: "text-blue-600", bg: "bg-blue-50 border-blue-200" },
};

export const TIME_WINDOW_LABELS = {
  today: "Today",
  week: "This Week",
  month: "This Month",
} as const;

export function formatSignalFieldLabel(field: string | null | undefined): string {
  if (!field) return "";
  return SIGNAL_FIELD_LABELS[field] || field.replace(/_/g, " ");
}

export function formatAnomalyTypeLabel(anomalyType: string | null | undefined): string {
  if (!anomalyType) return "";
  return ANOMALY_TYPE_LABELS[anomalyType] || anomalyType.replace(/_/g, " ");
}

export function formatLastAnomalySummary(
  lastAnomaly: {
    signal_field: string;
    severity: string;
    anomaly_type: string;
    occurred_at: string;
    supply_related: boolean;
    ended_at: string | null;
    startup_adjacent: boolean;
    mode_change: boolean;
    recurring: boolean;
  } | null,
): string {
  if (!lastAnomaly) return "";
  const signal = formatSignalFieldLabel(lastAnomaly.signal_field);
  const severity = SEVERITY_LABELS[lastAnomaly.severity]?.short || lastAnomaly.severity;
  let text = `${severity} ${signal}`;
  if (lastAnomaly.ended_at == null) text += " — Ongoing";
  if (lastAnomaly.recurring) text += " (recurring)";
  if (lastAnomaly.supply_related) text += " (supply-related)";
  return text;
}

export function formatAnomalyTimeAgo(occurredAt: string | null | undefined): string {
  if (!occurredAt) return "";
  try {
    const then = new Date(occurredAt).getTime();
    const now = Date.now();
    const diffMs = now - then;
    if (diffMs < 0) return "";
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    return `${diffDay}d ago`;
  } catch {
    return "";
  }
}

export function deriveAnomalyDisplay(state: string | null | undefined): AnomalyDisplayState {
  const normalized = (state || "unavailable").toLowerCase();

  if (normalized === "available") {
    return { stateBadgeVariant: "success", stateLabel: "Available", staleNote: null, showCounts: true, showDetails: true };
  }
  if (normalized === "stale") {
    return { stateBadgeVariant: "warning", stateLabel: "Available", staleNote: "Stale — data may be outdated", showCounts: true, showDetails: true };
  }
  if (normalized === "learning") {
    return { stateBadgeVariant: "info", stateLabel: "Learning baseline", staleNote: null, showCounts: false, showDetails: false };
  }
  return { stateBadgeVariant: "default", stateLabel: "Not available yet", staleNote: null, showCounts: false, showDetails: false };
}

export function formatAnomalyCount(count: number | null | undefined): string {
  if (count === null || count === undefined) return "—";
  if (count === 0) return "No anomalies today";
  if (count === 1) return "1 anomaly today";
  return `${count} anomalies today`;
}

export function formatAnomalyCountShort(count: number | null | undefined): string {
  if (count === null || count === undefined) return "—";
  return String(count);
}

export function formatWeekOverWeek(change: number | null | undefined): string {
  if (change === null || change === undefined) return "—";
  if (change > 0) return `+${change} vs last week`;
  if (change < 0) return `${change} vs last week`;
  return "Same as last week";
}

export function formatWeekOverWeekTone(change: number | null | undefined): string {
  if (change === null || change === undefined) return "text-slate-400";
  if (change > 0) return "text-amber-600";
  if (change < 0) return "text-emerald-600";
  return "text-slate-500";
}

export function deriveWeekOverWeekLabel(change: number | null | undefined): string {
  if (change === null || change === undefined) return "";
  if (change > 0) return "Worsening";
  if (change < 0) return "Improving";
  return "Stable";
}

export function formatWeekOverWeekExpanded(change: number | null | undefined): string {
  if (change === null || change === undefined) return "";
  if (change > 0) return `${change} more than last week — Worsening`;
  if (change < 0) return `${Math.abs(change)} fewer than last week — Improving`;
  return "Same as last week — Stable";
}

export interface AnomalySeverityTone {
  countTone: string;
  label: string;
}

export function deriveSeverityTone(counts: { mild: number; strong: number; severe: number } | null): AnomalySeverityTone {
  if (!counts) return { countTone: "text-slate-400", label: "" };
  if (counts.severe > 0) return { countTone: "text-red-600", label: "Severe" };
  if (counts.strong > 0) return { countTone: "text-amber-600", label: "Strong" };
  if (counts.mild > 0) return { countTone: "text-blue-600", label: "Mild" };
  return { countTone: "text-emerald-600", label: "None" };
}

export function formatSeverityBreakdown(counts: { mild: number; strong: number; severe: number } | null): string {
  if (!counts) return "";
  const parts: string[] = [];
  if (counts.severe > 0) parts.push(`${counts.severe} severe`);
  if (counts.strong > 0) parts.push(`${counts.strong} strong`);
  if (counts.mild > 0) parts.push(`${counts.mild} mild`);
  return parts.length > 0 ? parts.join(", ") : "none";
}

export function formatBaselineContext(
  baselineStatus: string | null | undefined,
  baselineFieldCount: number | null | undefined,
  totalSignals: number = 5,
): string {
  if (baselineStatus === "active") {
    return `Monitoring ${baselineFieldCount ?? 0} signals`;
  }
  if (baselineStatus === "candidate") {
    return `Building baseline — ${baselineFieldCount ?? 0} of ${totalSignals} signals learned`;
  }
  if (baselineStatus === "partial") {
    return `Partial baseline — ${baselineFieldCount ?? 0} of ${totalSignals} signals active`;
  }
  return "";
}

export function formatBaselineSignalStatus(
  signals: Array<{ field_name: string; status: string; quality_score: number | null }>,
): Array<{ label: string; status: string; qualityLabel: string }> {
  return signals.map((s) => {
    let qualityLabel = "";
    if (s.quality_score != null) {
      if (s.quality_score >= 0.85) qualityLabel = "High quality";
      else if (s.quality_score >= 0.70) qualityLabel = "Medium quality";
      else if (s.quality_score >= 0.50) qualityLabel = "Low quality";
      else qualityLabel = "Insufficient";
    }
    const statusLabel = s.status === "active" ? "Active" : s.status === "candidate" ? "Learning" : s.status;
    return { label: formatSignalFieldLabel(s.field_name), status: statusLabel, qualityLabel };
  });
}

export function formatSignalBreakdown(
  breakdown: Array<{ field_name: string; count: number; mild: number; strong: number; severe: number }>,
): Array<{ label: string; count: number; detail: string }> {
  return breakdown
    .filter((s) => s.count > 0)
    .sort((a, b) => b.count - a.count)
    .map((s) => {
      const parts: string[] = [];
      if (s.severe > 0) parts.push(`${s.severe} severe`);
      if (s.strong > 0) parts.push(`${s.strong} strong`);
      if (s.mild > 0) parts.push(`${s.mild} mild`);
      return { label: formatSignalFieldLabel(s.field_name), count: s.count, detail: parts.join(", ") };
    });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

export function formatAnomalyDetail(
  anomaly: {
    signal_field: string;
    severity: string;
    anomaly_type: string;
    signal_value: number | null;
    baseline_mean: number | null;
    z_score: number | null;
    duration_seconds: number | null;
    supply_related: boolean;
  } | null,
): { observedVsBaseline: string; zScoreLabel: string; durationLabel: string } {
  if (!anomaly) return { observedVsBaseline: "", zScoreLabel: "", durationLabel: "" };
  const signalLabel = formatSignalFieldLabel(anomaly.signal_field);
  const unit = anomaly.signal_field === "current_avg" ? "A"
    : anomaly.signal_field === "power" ? "kW"
    : anomaly.signal_field === "voltage_avg" ? "V"
    : "";
  const unitStr = unit ? ` ${unit}` : "";
  let observedVsBaseline = "";
  if (anomaly.signal_value != null && anomaly.baseline_mean != null) {
    observedVsBaseline = `${signalLabel}: ${anomaly.signal_value.toFixed(2)}${unitStr} (baseline: ${anomaly.baseline_mean.toFixed(2)}${unitStr})`;
  }
  let zScoreLabel = "";
  if (anomaly.z_score != null) {
    zScoreLabel = `z-score: ${anomaly.z_score.toFixed(1)}`;
  }
  const durationLabel = formatDuration(anomaly.duration_seconds);
  return { observedVsBaseline, zScoreLabel, durationLabel };
}

export function formatTimeWindowCell(counts: { total: number; mild: number; strong: number; severe: number; supply_related?: number } | null): {
  total: string;
  breakdown: string;
  supplyNote: string;
} {
  if (!counts) return { total: "—", breakdown: "", supplyNote: "" };
  const breakdown = formatSeverityBreakdown(counts);
  const supplyNote = (counts as { supply_related?: number }).supply_related && (counts as { supply_related?: number }).supply_related! > 0
    ? `${(counts as { supply_related?: number }).supply_related} supply-related`
    : "";
  return {
    total: String(counts.total),
    breakdown: counts.total > 0 ? breakdown : "none",
    supplyNote,
  };
}

export interface AnomalyEventDisplay {
  timeAgo: string;
  signalLabel: string;
  severityLabel: string;
  severityTone: string;
  anomalyTypeLabel: string;
  observedVsBaseline: string;
  zScoreLabel: string;
  durationLabel: string;
  ongoing: boolean;
  contextTags: string[];
}

export function formatAnomalyEventDisplay(event: {
  occurred_at: string;
  signal_field: string;
  severity: string;
  anomaly_type: string;
  signal_value: number | null;
  baseline_mean: number | null;
  z_score: number | null;
  duration_seconds: number | null;
  ended_at: string | null;
  supply_related: boolean;
  startup_adjacent: boolean;
  mode_change: boolean;
  recurring: boolean;
}): AnomalyEventDisplay {
  const timeAgo = formatAnomalyTimeAgo(event.occurred_at);
  const signalLabel = formatSignalFieldLabel(event.signal_field);
  const sev = SEVERITY_LABELS[event.severity];
  const severityLabel = sev?.short ?? event.severity;
  const severityTone = sev?.tone ?? "text-slate-600";
  const anomalyTypeLabel = formatAnomalyTypeLabel(event.anomaly_type);
  const detail = formatAnomalyDetail(event);
  const ongoing = event.ended_at == null;
  const contextTags: string[] = [];
  if (event.supply_related) contextTags.push("Supply-related");
  if (event.startup_adjacent) contextTags.push("Startup");
  if (event.mode_change) contextTags.push("Mode change");
  if (event.recurring) contextTags.push("Recurring");
  return {
    timeAgo,
    signalLabel,
    severityLabel,
    severityTone,
    anomalyTypeLabel,
    observedVsBaseline: detail.observedVsBaseline,
    zScoreLabel: detail.zScoreLabel,
    durationLabel: detail.durationLabel,
    ongoing,
    contextTags,
  };
}
