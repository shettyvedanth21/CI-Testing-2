import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  deriveAnomalyDisplay,
  formatAnomalyCount,
  formatAnomalyCountShort,
  formatWeekOverWeek,
  formatWeekOverWeekTone,
  deriveSeverityTone,
  deriveWeekOverWeekLabel,
  formatSignalFieldLabel,
  formatAnomalyTypeLabel,
  formatLastAnomalySummary,
  formatAnomalyTimeAgo,
  formatSeverityBreakdown,
  formatBaselineContext,
  formatTimeWindowCell,
  formatWeekOverWeekExpanded,
  formatSignalBreakdown,
  formatAnomalyDetail,
  formatDuration,
  formatBaselineSignalStatus,
  formatAnomalyEventDisplay,
  SIGNAL_FIELD_LABELS,
  ANOMALY_FIELD_TO_SIGNAL,
  ANOMALY_TYPE_LABELS,
  SEVERITY_LABELS,
  TIME_WINDOW_LABELS,
} from "../../lib/anomalyDisplay.ts";

describe("deriveAnomalyDisplay", () => {
  it("available state", () => {
    const d = deriveAnomalyDisplay("available");
    assert.equal(d.stateBadgeVariant, "success");
    assert.equal(d.stateLabel, "Available");
    assert.equal(d.staleNote, null);
    assert.equal(d.showCounts, true);
    assert.equal(d.showDetails, true);
  });

  it("stale state", () => {
    const d = deriveAnomalyDisplay("stale");
    assert.equal(d.stateBadgeVariant, "warning");
    assert.equal(d.stateLabel, "Available");
    assert.equal(d.staleNote, "Stale — data may be outdated");
    assert.equal(d.showCounts, true);
    assert.equal(d.showDetails, true);
  });

  it("learning state", () => {
    const d = deriveAnomalyDisplay("learning");
    assert.equal(d.stateBadgeVariant, "info");
    assert.equal(d.stateLabel, "Learning baseline");
    assert.equal(d.showCounts, false);
    assert.equal(d.showDetails, false);
  });

  it("unavailable state", () => {
    const d = deriveAnomalyDisplay("unavailable");
    assert.equal(d.stateBadgeVariant, "default");
    assert.equal(d.stateLabel, "Not available yet");
    assert.equal(d.showCounts, false);
    assert.equal(d.showDetails, false);
  });

  it("null defaults to unavailable", () => {
    const d = deriveAnomalyDisplay(null);
    assert.equal(d.stateBadgeVariant, "default");
    assert.equal(d.stateLabel, "Not available yet");
  });

  it("undefined defaults to unavailable", () => {
    const d = deriveAnomalyDisplay(undefined);
    assert.equal(d.stateBadgeVariant, "default");
  });
});

describe("formatAnomalyCount", () => {
  it("zero includes today", () => assert.equal(formatAnomalyCount(0), "No anomalies today"));
  it("one includes today", () => assert.equal(formatAnomalyCount(1), "1 anomaly today"));
  it("many includes today", () => assert.equal(formatAnomalyCount(3), "3 anomalies today"));
  it("null", () => assert.equal(formatAnomalyCount(null), "—"));
  it("undefined", () => assert.equal(formatAnomalyCount(undefined), "—"));
});

describe("formatAnomalyCountShort", () => {
  it("returns number as string", () => assert.equal(formatAnomalyCountShort(5), "5"));
  it("zero", () => assert.equal(formatAnomalyCountShort(0), "0"));
  it("null", () => assert.equal(formatAnomalyCountShort(null), "—"));
});

describe("formatWeekOverWeek", () => {
  it("positive", () => assert.equal(formatWeekOverWeek(3), "+3 vs last week"));
  it("negative", () => assert.equal(formatWeekOverWeek(-2), "-2 vs last week"));
  it("zero", () => assert.equal(formatWeekOverWeek(0), "Same as last week"));
  it("null", () => assert.equal(formatWeekOverWeek(null), "—"));
  it("undefined", () => assert.equal(formatWeekOverWeek(undefined), "—"));
});

describe("formatWeekOverWeekTone", () => {
  it("positive is amber", () => assert.equal(formatWeekOverWeekTone(3), "text-amber-600"));
  it("negative is green", () => assert.equal(formatWeekOverWeekTone(-2), "text-emerald-600"));
  it("zero is slate", () => assert.equal(formatWeekOverWeekTone(0), "text-slate-500"));
  it("null is slate muted", () => assert.equal(formatWeekOverWeekTone(null), "text-slate-400"));
});

describe("deriveSeverityTone", () => {
  it("severe > 0 is red", () => {
    const t = deriveSeverityTone({ mild: 0, strong: 0, severe: 1 });
    assert.equal(t.countTone, "text-red-600");
    assert.equal(t.label, "Severe");
  });

  it("strong > 0 is amber", () => {
    const t = deriveSeverityTone({ mild: 0, strong: 2, severe: 0 });
    assert.equal(t.countTone, "text-amber-600");
    assert.equal(t.label, "Strong");
  });

  it("mild > 0 is blue", () => {
    const t = deriveSeverityTone({ mild: 3, strong: 0, severe: 0 });
    assert.equal(t.countTone, "text-blue-600");
    assert.equal(t.label, "Mild");
  });

  it("all zero is emerald", () => {
    const t = deriveSeverityTone({ mild: 0, strong: 0, severe: 0 });
    assert.equal(t.countTone, "text-emerald-600");
    assert.equal(t.label, "None");
  });

  it("null counts is slate", () => {
    const t = deriveSeverityTone(null);
    assert.equal(t.countTone, "text-slate-400");
  });

  it("severe takes precedence over strong", () => {
    const t = deriveSeverityTone({ mild: 1, strong: 1, severe: 1 });
    assert.equal(t.countTone, "text-red-600");
  });
});

describe("deriveWeekOverWeekLabel", () => {
  it("positive is Worsening", () => assert.equal(deriveWeekOverWeekLabel(3), "Worsening"));
  it("negative is Improving", () => assert.equal(deriveWeekOverWeekLabel(-2), "Improving"));
  it("zero is Stable", () => assert.equal(deriveWeekOverWeekLabel(0), "Stable"));
  it("null returns empty", () => assert.equal(deriveWeekOverWeekLabel(null), ""));
  it("undefined returns empty", () => assert.equal(deriveWeekOverWeekLabel(undefined), ""));
});

describe("SIGNAL_FIELD_LABELS", () => {
  it("maps known anomaly signal fields with disambiguated labels", () => {
    assert.equal(SIGNAL_FIELD_LABELS["current_avg"], "Current (magnitude)");
    assert.equal(SIGNAL_FIELD_LABELS["power"], "Power draw");
    assert.equal(SIGNAL_FIELD_LABELS["power_factor"], "Power factor");
    assert.equal(SIGNAL_FIELD_LABELS["voltage_avg"], "Voltage");
    assert.equal(SIGNAL_FIELD_LABELS["phase_imbalance"], "Phase imbalance");
  });
});

describe("ANOMALY_FIELD_TO_SIGNAL", () => {
  it("maps anomaly fields to degradation signals", () => {
    assert.equal(ANOMALY_FIELD_TO_SIGNAL["current_avg"], "current_variability_drift");
    assert.equal(ANOMALY_FIELD_TO_SIGNAL["power"], "abnormal_power_draw");
    assert.equal(ANOMALY_FIELD_TO_SIGNAL["power_factor"], "power_factor_drop");
    assert.equal(ANOMALY_FIELD_TO_SIGNAL["phase_imbalance"], "phase_imbalance_drift");
    assert.equal(ANOMALY_FIELD_TO_SIGNAL["voltage_avg"], null);
  });
});

describe("formatSignalFieldLabel", () => {
  it("known field returns label", () => assert.equal(formatSignalFieldLabel("current_avg"), "Current (magnitude)"));
  it("unknown field returns spaced name", () => assert.equal(formatSignalFieldLabel("some_field"), "some field"));
  it("null returns empty", () => assert.equal(formatSignalFieldLabel(null), ""));
  it("undefined returns empty", () => assert.equal(formatSignalFieldLabel(undefined), ""));
});

describe("formatAnomalyTypeLabel", () => {
  it("deviation", () => assert.equal(formatAnomalyTypeLabel("deviation"), "Deviation from baseline"));
  it("persistent", () => assert.equal(formatAnomalyTypeLabel("persistent"), "Persistent anomaly"));
  it("trend", () => assert.equal(formatAnomalyTypeLabel("trend"), "Worsening trend"));
  it("unknown falls back", () => assert.equal(formatAnomalyTypeLabel("other"), "other"));
  it("null returns empty", () => assert.equal(formatAnomalyTypeLabel(null), ""));
});

describe("formatLastAnomalySummary", () => {
  const base = {
    signal_field: "current_avg",
    severity: "severe",
    anomaly_type: "deviation",
    occurred_at: new Date().toISOString(),
    supply_related: false,
    ended_at: new Date().toISOString() as string | null,
    startup_adjacent: false,
    mode_change: false,
    recurring: false,
  };

  it("formats severe current with severity", () => {
    const result = formatLastAnomalySummary(base);
    assert.ok(result.includes("Severe"));
    assert.ok(result.includes("Current"));
  });

  it("includes supply-related tag", () => {
    const result = formatLastAnomalySummary({
      ...base,
      signal_field: "voltage_avg",
      severity: "mild",
      supply_related: true,
    });
    assert.ok(result.includes("supply-related"));
  });

  it("shows Ongoing when ended_at is null", () => {
    const result = formatLastAnomalySummary({ ...base, ended_at: null });
    assert.ok(result.includes("Ongoing"));
  });

  it("does not show Ongoing when ended_at is set", () => {
    const result = formatLastAnomalySummary(base);
    assert.ok(!result.includes("Ongoing"));
  });

  it("shows recurring tag", () => {
    const result = formatLastAnomalySummary({ ...base, recurring: true });
    assert.ok(result.includes("recurring"));
  });

  it("null returns empty string", () => {
    assert.equal(formatLastAnomalySummary(null), "");
  });
});

describe("formatAnomalyTimeAgo", () => {
  it("recent timestamp returns just now", () => {
    const result = formatAnomalyTimeAgo(new Date().toISOString());
    assert.equal(result, "just now");
  });

  it("5 minutes ago", () => {
    const ts = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    assert.equal(formatAnomalyTimeAgo(ts), "5m ago");
  });

  it("2 hours ago", () => {
    const ts = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    assert.equal(formatAnomalyTimeAgo(ts), "2h ago");
  });

  it("1 day ago", () => {
    const ts = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString();
    assert.equal(formatAnomalyTimeAgo(ts), "1d ago");
  });

  it("null returns empty", () => assert.equal(formatAnomalyTimeAgo(null), ""));
  it("undefined returns empty", () => assert.equal(formatAnomalyTimeAgo(undefined), ""));
});

describe("formatSeverityBreakdown", () => {
  it("severe and strong and mild", () => {
    assert.equal(formatSeverityBreakdown({ mild: 5, strong: 3, severe: 1 }), "1 severe, 3 strong, 5 mild");
  });

  it("only mild", () => {
    assert.equal(formatSeverityBreakdown({ mild: 2, strong: 0, severe: 0 }), "2 mild");
  });

  it("all zero returns none", () => {
    assert.equal(formatSeverityBreakdown({ mild: 0, strong: 0, severe: 0 }), "none");
  });

  it("null returns empty", () => {
    assert.equal(formatSeverityBreakdown(null), "");
  });
});

describe("SEVERITY_LABELS", () => {
  it("has entries for severe, strong, mild with short, tone, bg", () => {
    assert.ok(SEVERITY_LABELS["severe"]);
    assert.ok(SEVERITY_LABELS["strong"]);
    assert.ok(SEVERITY_LABELS["mild"]);
    assert.equal(SEVERITY_LABELS["severe"].short, "Severe");
    assert.equal(SEVERITY_LABELS["strong"].short, "Strong");
    assert.equal(SEVERITY_LABELS["mild"].short, "Mild");
    assert.ok(SEVERITY_LABELS["severe"].bg);
    assert.ok(SEVERITY_LABELS["strong"].bg);
    assert.ok(SEVERITY_LABELS["mild"].bg);
  });
});

describe("ANOMALY_TYPE_LABELS", () => {
  it("maps all known types", () => {
    assert.ok(ANOMALY_TYPE_LABELS["deviation"]);
    assert.ok(ANOMALY_TYPE_LABELS["persistent"]);
    assert.ok(ANOMALY_TYPE_LABELS["trend"]);
  });
});

describe("formatBaselineContext", () => {
  it("active baseline", () => {
    assert.equal(formatBaselineContext("active", 5), "Monitoring 5 signals");
  });

  it("candidate baseline", () => {
    assert.equal(formatBaselineContext("candidate", 3), "Building baseline — 3 of 5 signals learned");
  });

  it("partial baseline", () => {
    assert.equal(formatBaselineContext("partial", 2), "Partial baseline — 2 of 5 signals active");
  });

  it("null status returns empty", () => {
    assert.equal(formatBaselineContext(null, 5), "");
  });

  it("custom total signals", () => {
    assert.equal(formatBaselineContext("active", 7, 7), "Monitoring 7 signals");
  });
});

describe("formatTimeWindowCell", () => {
  it("null counts returns dashes", () => {
    const cell = formatTimeWindowCell(null);
    assert.equal(cell.total, "—");
    assert.equal(cell.breakdown, "");
    assert.equal(cell.supplyNote, "");
  });

  it("zero total", () => {
    const cell = formatTimeWindowCell({ total: 0, mild: 0, strong: 0, severe: 0, supply_related: 0 });
    assert.equal(cell.total, "0");
    assert.equal(cell.breakdown, "none");
    assert.equal(cell.supplyNote, "");
  });

  it("with counts", () => {
    const cell = formatTimeWindowCell({ total: 6, mild: 3, strong: 2, severe: 1, supply_related: 1 });
    assert.equal(cell.total, "6");
    assert.ok(cell.breakdown.includes("1 severe"));
    assert.ok(cell.breakdown.includes("2 strong"));
    assert.ok(cell.breakdown.includes("3 mild"));
    assert.equal(cell.supplyNote, "1 supply-related");
  });

  it("no supply-related", () => {
    const cell = formatTimeWindowCell({ total: 2, mild: 2, strong: 0, severe: 0, supply_related: 0 });
    assert.equal(cell.supplyNote, "");
  });
});

describe("formatWeekOverWeekExpanded", () => {
  it("positive change", () => {
    assert.equal(formatWeekOverWeekExpanded(3), "3 more than last week — Worsening");
  });

  it("negative change", () => {
    assert.equal(formatWeekOverWeekExpanded(-2), "2 fewer than last week — Improving");
  });

  it("zero change", () => {
    assert.equal(formatWeekOverWeekExpanded(0), "Same as last week — Stable");
  });

  it("null returns empty", () => {
    assert.equal(formatWeekOverWeekExpanded(null), "");
  });

  it("undefined returns empty", () => {
    assert.equal(formatWeekOverWeekExpanded(undefined), "");
  });
});

describe("TIME_WINDOW_LABELS", () => {
  it("has today, week, month", () => {
    assert.equal(TIME_WINDOW_LABELS.today, "Today");
    assert.equal(TIME_WINDOW_LABELS.week, "This Week");
    assert.equal(TIME_WINDOW_LABELS.month, "This Month");
  });
});

describe("formatSignalBreakdown", () => {
  it("filters zero-count signals and sorts by count descending", () => {
    const result = formatSignalBreakdown([
      { field_name: "current_avg", count: 1, mild: 1, strong: 0, severe: 0 },
      { field_name: "power", count: 3, mild: 1, strong: 1, severe: 1 },
    ]);
    assert.equal(result.length, 2);
    assert.equal(result[0].label, "Power draw");
    assert.equal(result[0].count, 3);
    assert.ok(result[0].detail.includes("1 severe"));
  });

  it("excludes zero-count signals", () => {
    const result = formatSignalBreakdown([
      { field_name: "current_avg", count: 0, mild: 0, strong: 0, severe: 0 },
    ]);
    assert.equal(result.length, 0);
  });

  it("empty array returns empty", () => {
    assert.equal(formatSignalBreakdown([]).length, 0);
  });
});

describe("formatDuration", () => {
  it("formats seconds under 60", () => assert.equal(formatDuration(45), "45s"));
  it("formats minutes and seconds", () => assert.equal(formatDuration(125), "2m 5s"));
  it("formats hours and minutes", () => assert.equal(formatDuration(3665), "1h 1m"));
  it("null returns empty", () => assert.equal(formatDuration(null), ""));
});

describe("formatAnomalyDetail", () => {
  it("returns observed vs baseline for current_avg", () => {
    const detail = formatAnomalyDetail({
      signal_field: "current_avg",
      severity: "strong",
      anomaly_type: "deviation",
      signal_value: 12.5,
      baseline_mean: 10.0,
      z_score: 2.5,
      duration_seconds: 120,
      supply_related: false,
    });
    assert.ok(detail.observedVsBaseline.includes("12.50 A"));
    assert.ok(detail.observedVsBaseline.includes("10.00 A"));
    assert.ok(detail.zScoreLabel.includes("2.5"));
    assert.equal(detail.durationLabel, "2m 0s");
  });

  it("returns empty for null anomaly", () => {
    const detail = formatAnomalyDetail(null);
    assert.equal(detail.observedVsBaseline, "");
    assert.equal(detail.zScoreLabel, "");
  });

  it("returns partial when signal_value null", () => {
    const detail = formatAnomalyDetail({
      signal_field: "power",
      severity: "mild",
      anomaly_type: "deviation",
      signal_value: null,
      baseline_mean: 5.0,
      z_score: 1.5,
      duration_seconds: null,
      supply_related: false,
    });
    assert.equal(detail.observedVsBaseline, "");
    assert.ok(detail.zScoreLabel.includes("1.5"));
  });
});

describe("formatBaselineSignalStatus", () => {
  it("maps active signal with high quality", () => {
    const result = formatBaselineSignalStatus([
      { field_name: "current_avg", status: "active", quality_score: 0.9 },
    ]);
    assert.equal(result.length, 1);
    assert.equal(result[0].label, "Current (magnitude)");
    assert.equal(result[0].status, "Active");
    assert.equal(result[0].qualityLabel, "High quality");
  });

  it("maps candidate signal with low quality", () => {
    const result = formatBaselineSignalStatus([
      { field_name: "power", status: "candidate", quality_score: 0.3 },
    ]);
    assert.equal(result[0].status, "Learning");
    assert.equal(result[0].qualityLabel, "Insufficient");
  });

  it("empty array returns empty", () => {
    assert.equal(formatBaselineSignalStatus([]).length, 0);
  });
});

describe("formatAnomalyEventDisplay", () => {
  const baseEvent = {
    occurred_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    signal_field: "current_avg",
    severity: "severe",
    anomaly_type: "deviation",
    signal_value: 14.0,
    baseline_mean: 10.0,
    z_score: 4.0,
    duration_seconds: 300,
    ended_at: new Date().toISOString(),
    supply_related: false,
    startup_adjacent: false,
    mode_change: false,
    recurring: false,
  };

  it("returns all display fields for a complete event", () => {
    const d = formatAnomalyEventDisplay(baseEvent);
    assert.ok(d.timeAgo.length > 0);
    assert.equal(d.signalLabel, SIGNAL_FIELD_LABELS["current_avg"]);
    assert.equal(d.severityLabel, SEVERITY_LABELS["severe"].short);
    assert.equal(d.severityTone, SEVERITY_LABELS["severe"].tone);
    assert.equal(d.anomalyTypeLabel, ANOMALY_TYPE_LABELS["deviation"]);
    assert.ok(d.observedVsBaseline.length > 0);
    assert.ok(d.zScoreLabel.length > 0);
    assert.ok(d.durationLabel.length > 0);
    assert.equal(d.ongoing, false);
    assert.equal(d.contextTags.length, 0);
  });

  it("ongoing=true when ended_at is null", () => {
    const d = formatAnomalyEventDisplay({ ...baseEvent, ended_at: null });
    assert.equal(d.ongoing, true);
  });

  it("contextTags includes supply-related, startup, mode-change, recurring", () => {
    const d = formatAnomalyEventDisplay({
      ...baseEvent,
      supply_related: true,
      startup_adjacent: true,
      mode_change: true,
      recurring: true,
    });
    assert.equal(d.contextTags.length, 4);
    assert.ok(d.contextTags.includes("Supply-related"));
    assert.ok(d.contextTags.includes("Startup"));
    assert.ok(d.contextTags.includes("Mode change"));
    assert.ok(d.contextTags.includes("Recurring"));
  });

  it("contextTags empty when all flags false", () => {
    const d = formatAnomalyEventDisplay(baseEvent);
    assert.equal(d.contextTags.length, 0);
  });

  it("handles null signal_value and baseline_mean", () => {
    const d = formatAnomalyEventDisplay({
      ...baseEvent,
      signal_value: null,
      baseline_mean: null,
    });
    assert.equal(d.observedVsBaseline, "");
  });

  it("handles null z_score", () => {
    const d = formatAnomalyEventDisplay({
      ...baseEvent,
      z_score: null,
    });
    assert.equal(d.zScoreLabel, "");
  });
});
