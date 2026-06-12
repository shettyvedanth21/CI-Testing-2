import test from "node:test";
import assert from "node:assert/strict";

import {
  deriveDegradationDisplay,
  deriveDegradationStatusDisplay,
  formatScore,
  formatConfidence,
  confidenceDescription,
  baselineQualityDescription,
  formatUpdatedMinutesAgo,
  formatSignalCompleteness,
  translateContribution,
  contributionBarColor,
  buildContributionList,
  formatObservedBaseline,
  formatRawDriftPct,
  deriveTopReasons,
  SIGNAL_OPERATOR_LABELS,
  SIGNAL_TO_ANOMALY_FIELD,
  SIGNAL_CORRELATION_NOTE,
  SIGNAL_UNITS,
  STATUS_DESCRIPTIONS,
  SIGNAL_MAX_DRIFT,
  BASELINE_QUALITY_DESCRIPTIONS,
  CONFIDENCE_DESCRIPTIONS,
  SCORE_DIRECTION_LABEL,
  ALL_SIGNALS,
  STATUS_THRESHOLD_LINES,
} from "../../lib/degradationDisplay.ts";

test("deriveDegradationDisplay: scored + healthy status shows healthy badge and score", () => {
  const display = deriveDegradationDisplay("scored", "healthy");
  assert.equal(display.statusBadgeVariant, "success");
  assert.equal(display.statusLabel, "Healthy");
  assert.equal(display.staleNote, null);
  assert.equal(display.showScore, true);
  assert.equal(display.showDetails, true);
});

test("deriveDegradationDisplay: scored + watch status shows watch badge", () => {
  const display = deriveDegradationDisplay("scored", "watch");
  assert.equal(display.statusBadgeVariant, "info");
  assert.equal(display.statusLabel, "Watch");
  assert.equal(display.showScore, true);
});

test("deriveDegradationDisplay: scored + warning status shows warning badge", () => {
  const display = deriveDegradationDisplay("scored", "warning");
  assert.equal(display.statusBadgeVariant, "warning");
  assert.equal(display.statusLabel, "Warning");
  assert.equal(display.showScore, true);
});

test("deriveDegradationDisplay: scored + critical status shows error badge", () => {
  const display = deriveDegradationDisplay("scored", "critical");
  assert.equal(display.statusBadgeVariant, "error");
  assert.equal(display.statusLabel, "Critical");
  assert.equal(display.showScore, true);
});

test("deriveDegradationDisplay: stale + healthy shows status badge plus stale note", () => {
  const display = deriveDegradationDisplay("stale", "healthy");
  assert.equal(display.statusBadgeVariant, "success");
  assert.equal(display.statusLabel, "Healthy");
  assert.equal(display.staleNote, "Stale — data may be outdated");
  assert.equal(display.showScore, true);
  assert.equal(display.showDetails, true);
});

test("deriveDegradationDisplay: stale + critical shows critical badge plus stale note", () => {
  const display = deriveDegradationDisplay("stale", "critical");
  assert.equal(display.statusBadgeVariant, "error");
  assert.equal(display.statusLabel, "Critical");
  assert.equal(display.staleNote, "Stale — data may be outdated");
  assert.equal(display.showScore, true);
});

test("deriveDegradationDisplay: learning state shows info badge", () => {
  const display = deriveDegradationDisplay("learning", null);
  assert.equal(display.statusBadgeVariant, "info");
  assert.equal(display.statusLabel, "Learning baseline");
  assert.equal(display.showScore, false);
  assert.equal(display.showDetails, false);
});

test("deriveDegradationDisplay: unavailable state shows default badge", () => {
  const display = deriveDegradationDisplay("unavailable", null);
  assert.equal(display.statusBadgeVariant, "default");
  assert.equal(display.statusLabel, "Not available yet");
  assert.equal(display.showScore, false);
});

test("deriveDegradationDisplay: null state defaults to unavailable", () => {
  const display = deriveDegradationDisplay(null, null);
  assert.equal(display.statusBadgeVariant, "default");
  assert.equal(display.statusLabel, "Not available yet");
});

test("deriveDegradationDisplay: low confidence shows low-confidence note", () => {
  const display = deriveDegradationDisplay("scored", "healthy", 0.3);
  assert.equal(display.lowConfidenceNote, "Low confidence: insufficient stable telemetry");
  assert.equal(display.showScore, true);
});

test("deriveDegradationDisplay: high confidence has no low-confidence note", () => {
  const display = deriveDegradationDisplay("scored", "healthy", 0.8);
  assert.equal(display.lowConfidenceNote, null);
});

test("deriveDegradationDisplay: null confidence has no low-confidence note", () => {
  const display = deriveDegradationDisplay("scored", "healthy", null);
  assert.equal(display.lowConfidenceNote, null);
});

test("deriveDegradationDisplay: learning state has no low-confidence note regardless of confidence", () => {
  const display = deriveDegradationDisplay("learning", null, 0.2);
  assert.equal(display.lowConfidenceNote, null);
  assert.equal(display.showScore, false);
});

test("deriveDegradationStatusDisplay: healthy", () => {
  const d = deriveDegradationStatusDisplay("healthy");
  assert.equal(d.statusBadgeVariant, "success");
  assert.equal(d.statusLabel, "Healthy");
});

test("deriveDegradationStatusDisplay: watch", () => {
  const d = deriveDegradationStatusDisplay("watch");
  assert.equal(d.statusBadgeVariant, "info");
  assert.equal(d.statusLabel, "Watch");
});

test("deriveDegradationStatusDisplay: warning", () => {
  const d = deriveDegradationStatusDisplay("warning");
  assert.equal(d.statusBadgeVariant, "warning");
  assert.equal(d.statusLabel, "Warning");
});

test("deriveDegradationStatusDisplay: critical", () => {
  const d = deriveDegradationStatusDisplay("critical");
  assert.equal(d.statusBadgeVariant, "error");
  assert.equal(d.statusLabel, "Critical");
});

test("deriveDegradationStatusDisplay: null returns default", () => {
  const d = deriveDegradationStatusDisplay(null);
  assert.equal(d.statusBadgeVariant, "default");
  assert.equal(d.statusLabel, "");
});

test("formatScore: formats as X.X/10", () => {
  assert.equal(formatScore(7.4), "7.4/10");
  assert.equal(formatScore(3), "3.0/10");
  assert.equal(formatScore(0), "0.0/10");
  assert.equal(formatScore(10), "10.0/10");
});

test("formatScore: null returns dash", () => {
  assert.equal(formatScore(null), "—");
  assert.equal(formatScore(undefined), "—");
});

test("formatConfidence: converts to percentage", () => {
  assert.equal(formatConfidence(0.85), "85%");
  assert.equal(formatConfidence(1), "100%");
  assert.equal(formatConfidence(0), "0%");
});

test("formatConfidence: null returns dash", () => {
  assert.equal(formatConfidence(null), "—");
});

test("formatUpdatedMinutesAgo: under 1 minute shows just now", () => {
  assert.equal(formatUpdatedMinutesAgo(0.3), "Updated just now");
});

test("formatUpdatedMinutesAgo: under 60 minutes shows minutes", () => {
  assert.equal(formatUpdatedMinutesAgo(15), "Updated 15m ago");
});

test("formatUpdatedMinutesAgo: 60+ minutes shows hours", () => {
  assert.equal(formatUpdatedMinutesAgo(120), "Updated 2h ago");
});

test("formatUpdatedMinutesAgo: null returns empty string", () => {
  assert.equal(formatUpdatedMinutesAgo(null), "");
  assert.equal(formatUpdatedMinutesAgo(undefined), "");
});

test("formatSignalCompleteness: formats as percentage coverage", () => {
  assert.equal(formatSignalCompleteness(0.8), "80% signal coverage");
  assert.equal(formatSignalCompleteness(1), "100% signal coverage");
  assert.equal(formatSignalCompleteness(0.4), "40% signal coverage");
});

test("formatSignalCompleteness: null/undefined returns empty string", () => {
  assert.equal(formatSignalCompleteness(null), "");
  assert.equal(formatSignalCompleteness(undefined), "");
});

test("SIGNAL_OPERATOR_LABELS: maps known signals to unified operator labels", () => {
  assert.equal(SIGNAL_OPERATOR_LABELS["current_variability_drift"], "Current variability above baseline");
  assert.equal(SIGNAL_OPERATOR_LABELS["power_factor_drop"], "Power factor below baseline");
  assert.equal(SIGNAL_OPERATOR_LABELS["abnormal_power_draw"], "Power draw deviating from baseline");
  assert.equal(SIGNAL_OPERATOR_LABELS["phase_imbalance_drift"], "Phase imbalance above baseline");
  assert.equal(SIGNAL_OPERATOR_LABELS["trend_worsening"], "Degradation trend worsening");
});

test("STATUS_DESCRIPTIONS: provides plain-language descriptions for each status", () => {
  assert.ok(STATUS_DESCRIPTIONS["healthy"]);
  assert.ok(STATUS_DESCRIPTIONS["watch"]);
  assert.ok(STATUS_DESCRIPTIONS["warning"]);
  assert.ok(STATUS_DESCRIPTIONS["critical"]);
});

test("translateContribution: known signal gets operator label", () => {
  const c = translateContribution("power_factor_drop", 0.5);
  assert.equal(c.operatorLabel, "Power factor below baseline");
  assert.equal(c.signal, "power_factor_drop");
  assert.equal(c.available, true);
});

test("translateContribution: unknown signal falls back to spaced name", () => {
  const c = translateContribution("unknown_signal", 1.0);
  assert.equal(c.operatorLabel, "unknown signal");
});

test("translateContribution: zero drift is none magnitude", () => {
  const c = translateContribution("current_variability_drift", 0);
  assert.equal(c.driftMagnitude, "none");
  assert.equal(c.barPct, 0);
});

test("translateContribution: small drift is low magnitude", () => {
  const c = translateContribution("current_variability_drift", 0.5);
  assert.equal(c.driftMagnitude, "low");
  assert.ok(c.barPct > 0);
  assert.ok(c.barPct < 34);
});

test("translateContribution: moderate drift", () => {
  const c = translateContribution("current_variability_drift", 1.2);
  assert.equal(c.driftMagnitude, "moderate");
});

test("translateContribution: high drift", () => {
  const c = translateContribution("current_variability_drift", 2.5);
  assert.equal(c.driftMagnitude, "high");
  assert.ok(c.barPct >= 66);
});

test("translateContribution: power_factor_drop uses max drift 1.0 for bar", () => {
  const c = translateContribution("power_factor_drop", 0.8);
  assert.ok(c.barPct >= 50);
  assert.equal(c.driftMagnitude, "high");
});

test("translateContribution: available flag defaults to true", () => {
  const c = translateContribution("current_variability_drift", 0.5);
  assert.equal(c.available, true);
});

test("translateContribution: available=false sets available flag", () => {
  const c = translateContribution("current_variability_drift", 0, false);
  assert.equal(c.available, false);
  assert.equal(c.barPct, 0);
});

test("contributionBarColor: maps magnitude to correct color class", () => {
  assert.equal(contributionBarColor("high"), "bg-red-500");
  assert.equal(contributionBarColor("moderate"), "bg-amber-500");
  assert.equal(contributionBarColor("low"), "bg-blue-400");
  assert.equal(contributionBarColor("none"), "bg-slate-200");
});

test("contributionBarColor: unavailable signal returns slate-200", () => {
  assert.equal(contributionBarColor("high", false), "bg-slate-200");
  assert.equal(contributionBarColor("none", false), "bg-slate-200");
});

test("SCORE_DIRECTION_LABEL: contains direction hint", () => {
  assert.ok(SCORE_DIRECTION_LABEL.includes("Lower is better"));
  assert.ok(SCORE_DIRECTION_LABEL.includes("1 = normal"));
  assert.ok(SCORE_DIRECTION_LABEL.includes("10 = critical"));
});

test("BASELINE_QUALITY_DESCRIPTIONS: maps all quality levels", () => {
  assert.ok(BASELINE_QUALITY_DESCRIPTIONS["high"].includes("Well-established"));
  assert.ok(BASELINE_QUALITY_DESCRIPTIONS["medium"].includes("stabilizing"));
  assert.ok(BASELINE_QUALITY_DESCRIPTIONS["low"].includes("Limited"));
  assert.ok(BASELINE_QUALITY_DESCRIPTIONS["insufficient"].includes("Insufficient"));
});

test("baselineQualityDescription: returns operator-friendly wording", () => {
  assert.equal(baselineQualityDescription("high"), "Well-established baseline");
  assert.equal(baselineQualityDescription("medium"), "Baseline still stabilizing");
  assert.equal(baselineQualityDescription("low"), "Limited baseline data");
  assert.equal(baselineQualityDescription("insufficient"), "Insufficient baseline — score may be unreliable");
  assert.equal(baselineQualityDescription(null), "");
  assert.equal(baselineQualityDescription(""), "");
});

test("CONFIDENCE_DESCRIPTIONS: maps confidence tiers", () => {
  assert.ok(CONFIDENCE_DESCRIPTIONS["high"].includes("High confidence"));
  assert.ok(CONFIDENCE_DESCRIPTIONS["medium"].includes("Moderate confidence"));
  assert.ok(CONFIDENCE_DESCRIPTIONS["low"].includes("Low confidence"));
});

test("confidenceDescription: high confidence", () => {
  assert.ok(confidenceDescription(0.9).includes("High confidence"));
});

test("confidenceDescription: medium confidence", () => {
  assert.ok(confidenceDescription(0.6).includes("Moderate confidence"));
});

test("confidenceDescription: low confidence", () => {
  assert.ok(confidenceDescription(0.3).includes("Low confidence"));
});

test("confidenceDescription: null returns empty", () => {
  assert.equal(confidenceDescription(null), "");
  assert.equal(confidenceDescription(undefined), "");
});

test("ALL_SIGNALS: contains 5 known signals", () => {
  assert.equal(ALL_SIGNALS.length, 5);
  assert.ok(ALL_SIGNALS.includes("current_variability_drift"));
  assert.ok(ALL_SIGNALS.includes("power_factor_drop"));
  assert.ok(ALL_SIGNALS.includes("abnormal_power_draw"));
  assert.ok(ALL_SIGNALS.includes("phase_imbalance_drift"));
  assert.ok(ALL_SIGNALS.includes("trend_worsening"));
});

test("STATUS_THRESHOLD_LINES: contains watch/warning/critical thresholds", () => {
  assert.equal(STATUS_THRESHOLD_LINES.length, 3);
  assert.equal(STATUS_THRESHOLD_LINES[0].value, 3);
  assert.equal(STATUS_THRESHOLD_LINES[0].status, "watch");
  assert.equal(STATUS_THRESHOLD_LINES[1].value, 5);
  assert.equal(STATUS_THRESHOLD_LINES[1].status, "warning");
  assert.equal(STATUS_THRESHOLD_LINES[2].value, 7);
  assert.equal(STATUS_THRESHOLD_LINES[2].status, "critical");
});

test("buildContributionList: includes unavailable signals from ALL_SIGNALS", () => {
  const contributions = [
    { signal: "current_variability_drift", drift: 1.0, available: true },
    { signal: "power_factor_drop", drift: 0.5, available: true },
  ];
  const list = buildContributionList(contributions);
  assert.equal(list.length, 5);
  const unavailable = list.filter((c) => !c.available);
  assert.equal(unavailable.length, 3);
  assert.ok(unavailable.some((c) => c.signal === "abnormal_power_draw"));
  assert.ok(unavailable.some((c) => c.signal === "phase_imbalance_drift"));
  assert.ok(unavailable.some((c) => c.signal === "trend_worsening"));
});

test("buildContributionList: available signals sorted before unavailable", () => {
  const contributions = [
    { signal: "current_variability_drift", drift: 0.5, available: true },
    { signal: "power_factor_drop", drift: 0, available: false },
  ];
  const list = buildContributionList(contributions);
  const firstAvailable = list.findIndex((c) => c.available);
  const firstUnavailable = list.findIndex((c) => !c.available);
  assert.ok(firstAvailable < firstUnavailable);
});

test("buildContributionList: available signals sorted by barPct descending", () => {
  const contributions = [
    { signal: "current_variability_drift", drift: 0.5, available: true },
    { signal: "power_factor_drop", drift: 0.8, available: true },
  ];
  const list = buildContributionList(contributions);
  const availableItems = list.filter((c) => c.available);
  assert.ok(availableItems[0].barPct >= availableItems[1].barPct);
});

test("buildContributionList: all signals available returns no missing", () => {
  const contributions = ALL_SIGNALS.map((s) => ({ signal: s, drift: 0.5, available: true }));
  const list = buildContributionList(contributions);
  assert.equal(list.length, 5);
  assert.ok(list.every((c) => c.available));
});

test("buildContributionList: empty contributions returns all signals as unavailable", () => {
  const list = buildContributionList([]);
  assert.equal(list.length, 5);
  assert.ok(list.every((c) => !c.available));
});

test("SIGNAL_UNITS: maps known signals to units", () => {
  assert.equal(SIGNAL_UNITS["current_variability_drift"], "A std dev");
  assert.equal(SIGNAL_UNITS["power_factor_drop"], "");
  assert.equal(SIGNAL_UNITS["abnormal_power_draw"], "kW");
  assert.equal(SIGNAL_UNITS["phase_imbalance_drift"], "");
  assert.equal(SIGNAL_UNITS["trend_worsening"], "");
});

test("formatObservedBaseline: formats with unit for current_variability_drift", () => {
  const result = formatObservedBaseline("current_variability_drift", 0.8, 0.5);
  assert.ok(result.includes("Observed: 0.80"));
  assert.ok(result.includes("A std dev"));
  assert.ok(result.includes("Baseline: 0.50"));
});

test("formatObservedBaseline: formats without unit for power_factor_drop", () => {
  const result = formatObservedBaseline("power_factor_drop", 0.88, 0.95);
  assert.ok(result.includes("Observed: 0.88"));
  assert.ok(!result.includes("A std dev"));
  assert.ok(result.includes("Baseline: 0.95"));
});

test("formatObservedBaseline: returns empty string when observed is null", () => {
  assert.equal(formatObservedBaseline("current_variability_drift", null, 0.5), "");
});

test("formatObservedBaseline: returns empty string when baseline is null", () => {
  assert.equal(formatObservedBaseline("current_variability_drift", 0.8, null), "");
});

test("formatRawDriftPct: formats positive drift with plus sign", () => {
  const result = formatRawDriftPct(0.6);
  assert.ok(result.startsWith("+"));
  assert.ok(result.includes("60% drift"));
});

test("formatRawDriftPct: formats negative drift", () => {
  const result = formatRawDriftPct(-0.3);
  assert.ok(result.includes("-30% drift"));
});

test("formatRawDriftPct: returns empty for null", () => {
  assert.equal(formatRawDriftPct(null), "");
  assert.equal(formatRawDriftPct(undefined), "");
});

test("translateContribution: carries observedValue/baselineValue/rawDrift/weightPct", () => {
  const c = translateContribution("current_variability_drift", 0.5, true, 0.8, 0.5, 0.6, 0.25);
  assert.equal(c.observedValue, 0.8);
  assert.equal(c.baselineValue, 0.5);
  assert.equal(c.rawDrift, 0.6);
  assert.equal(c.weightPct, 25);
});

test("buildContributionList: passes observed/baseline/raw_drift through", () => {
  const contributions = [
    { signal: "current_variability_drift", drift: 0.5, available: true, observed_value: 0.8, baseline_value: 0.5, raw_drift: 0.6, weight: 0.25 },
  ];
  const list = buildContributionList(contributions);
  const item = list.find((c) => c.signal === "current_variability_drift" && c.available);
  assert.ok(item);
  assert.equal(item!.observedValue, 0.8);
  assert.equal(item!.baselineValue, 0.5);
  assert.equal(item!.rawDrift, 0.6);
  assert.equal(item!.weightPct, 25);
});

test("deriveTopReasons: sorts by drift*weight descending and returns top 3", () => {
  const contributions = [
    { signal: "current_variability_drift", drift: 0.2, weight: 0.2, available: true },
    { signal: "power_factor_drop", drift: 0.9, weight: 0.3, available: true },
    { signal: "phase_imbalance_drift", drift: 0.5, weight: 0.25, available: true },
    { signal: "abnormal_power_draw", drift: 0.1, weight: 0.15, available: true },
    { signal: "trend_worsening", drift: 0.3, weight: 0.1, available: true },
  ];
  const reasons = deriveTopReasons(contributions);
  assert.equal(reasons.length, 3);
  assert.equal(reasons[0], "Power factor below baseline");
  assert.equal(reasons[1], "Phase imbalance above baseline");
  assert.equal(reasons[2], "Current variability above baseline");
});

test("deriveTopReasons: respects maxReasons parameter", () => {
  const contributions = [
    { signal: "power_factor_drop", drift: 0.9, weight: 0.3, available: true },
    { signal: "current_variability_drift", drift: 0.5, weight: 0.25, available: true },
  ];
  const reasons = deriveTopReasons(contributions, 1);
  assert.equal(reasons.length, 1);
  assert.equal(reasons[0], "Power factor below baseline");
});

test("deriveTopReasons: empty contributions returns empty list", () => {
  const reasons = deriveTopReasons([]);
  assert.equal(reasons.length, 0);
});

test("deriveTopReasons: fewer contributions than maxReasons returns all", () => {
  const contributions = [
    { signal: "current_variability_drift", drift: 0.3, weight: 0.2, available: true },
  ];
  const reasons = deriveTopReasons(contributions, 3);
  assert.equal(reasons.length, 1);
  assert.equal(reasons[0], "Current variability above baseline");
});

test("deriveTopReasons: filters out unavailable and zero-drift contributions", () => {
  const contributions = [
    { signal: "power_factor_drop", drift: 0, weight: 0.3, available: true },
    { signal: "current_variability_drift", drift: 0.5, weight: 0.25, available: false },
    { signal: "phase_imbalance_drift", drift: 0.4, weight: 0.2, available: true },
  ];
  const reasons = deriveTopReasons(contributions, 3);
  assert.equal(reasons.length, 1);
  assert.equal(reasons[0], "Phase imbalance above baseline");
});

test("deriveTopReasons: unknown signal falls back to spaced name", () => {
  const contributions = [
    { signal: "custom_signal", drift: 0.8, weight: 0.4, available: true },
  ];
  const reasons = deriveTopReasons(contributions, 3);
  assert.equal(reasons.length, 1);
  assert.equal(reasons[0], "custom signal");
});

test("SIGNAL_TO_ANOMALY_FIELD: maps degradation signals to anomaly fields", () => {
  assert.equal(SIGNAL_TO_ANOMALY_FIELD["current_variability_drift"], "current_avg");
  assert.equal(SIGNAL_TO_ANOMALY_FIELD["power_factor_drop"], "power_factor");
  assert.equal(SIGNAL_TO_ANOMALY_FIELD["abnormal_power_draw"], "power");
  assert.equal(SIGNAL_TO_ANOMALY_FIELD["phase_imbalance_drift"], "phase_imbalance");
  assert.equal(SIGNAL_TO_ANOMALY_FIELD["trend_worsening"], null);
});

test("SIGNAL_CORRELATION_NOTE: provides cross-panel hints for correlatable signals", () => {
  assert.ok(SIGNAL_CORRELATION_NOTE["current_variability_drift"].includes("current anomalies"));
  assert.ok(SIGNAL_CORRELATION_NOTE["power_factor_drop"].includes("power factor"));
  assert.ok(SIGNAL_CORRELATION_NOTE["abnormal_power_draw"].includes("power anomalies"));
  assert.ok(SIGNAL_CORRELATION_NOTE["phase_imbalance_drift"].includes("phase imbalance"));
});
