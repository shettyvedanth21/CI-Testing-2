import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  getAnalyticsConfidenceSummary,
  sanitizeAnalyticsNarrative,
} from "../../lib/analyticsPresentation.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const analyticsPagePath = path.resolve(
  __dirname,
  "../../app/(protected)/analytics/page.tsx",
);
const analyticsPageSource = readFileSync(analyticsPagePath, "utf-8");
const asyncPresentationPath = path.resolve(
  __dirname,
  "../../lib/asyncJobPresentation.ts",
);
const asyncPresentationSource = readFileSync(asyncPresentationPath, "utf-8");

test("analytics page removes the raw model confirmation panel from customer view", () => {
  assert.equal(analyticsPageSource.includes("Model Confirmation"), false);
  assert.equal(analyticsPageSource.includes("Confidence Summary"), true);
  assert.equal(analyticsPageSource.includes("Analysis Confidence"), true);
  assert.equal(analyticsPageSource.includes("Evidence Strength"), true);
});

test("analytics page presents fleet analytics as a background workflow", () => {
  assert.equal(analyticsPageSource.includes("Fleet analysis started"), true);
  assert.equal(asyncPresentationSource.includes("Some devices may run immediately while others wait in queue"), true);
  assert.equal(analyticsPageSource.includes("Fleet Coverage Summary"), true);
  assert.equal(analyticsPageSource.includes("What To Do Next"), true);
  assert.equal(analyticsPageSource.includes("Fleet workflow"), true);
});

test("analytics history detail panel is explicitly framed as selected-job detail", () => {
  assert.equal(analyticsPageSource.includes("Selected Job Details"), true);
  assert.equal(analyticsPageSource.includes("Select a recent analytics job to view status, progress, and result readiness."), true);
});

test("analytics stored-result reopen handles blocked outcomes with dedicated truthful view", () => {
  assert.equal(analyticsPageSource.includes("setScreen(\"blocked\")"), true);
  assert.equal(analyticsPageSource.includes("No telemetry in selected range"), true);
  assert.equal(analyticsPageSource.includes("Insufficient telemetry coverage"), true);
  assert.equal(analyticsPageSource.includes("no chart-ready analytics output exists for this run"), true);
});

test("fleet async helpers format backend-truthful progress copy", () => {
  assert.equal(asyncPresentationSource.includes("Some devices may run immediately while others wait in queue as capacity becomes available."), true);
  assert.equal(asyncPresentationSource.includes("devices selected"), true);
  assert.equal(asyncPresentationSource.includes("queued devices"), true);
  assert.equal(asyncPresentationSource.includes("running devices"), true);
  assert.equal(asyncPresentationSource.includes("completed devices"), true);
  assert.equal(asyncPresentationSource.includes("% coverage"), true);
});

test("anomaly confidence summary prefers backend-safe customer messaging", () => {
  const summary = getAnalyticsConfidenceSummary({
    analysis_type: "anomaly_detection",
    device_id: "DEV-1",
    job_id: "job-1",
    health_score: 82,
    confidence: {
      level: "High",
      badge_color: "#22c55e",
      banner_text: "Sufficient telemetry available for a reliable analysis.",
      banner_style: "green",
      days_available: 14,
    },
    confidence_summary: {
      title: "Analysis Confidence",
      level: "High",
      evidence_strength: "Strong",
      summary: "A confirmed abnormal operating pattern was detected.",
      interpretation: "The machine shows a moderate health impact in the selected period.",
      recommended_action: "Inspect the cooling path and review load balance.",
      factors: ["temperature", "power"],
    },
    summary: {
      total_anomalies: 12,
      anomaly_rate_pct: 5.2,
      anomaly_score: 36,
      health_impact: "Moderate",
      most_affected_parameter: "temperature",
      data_points_analyzed: 2048,
      days_analyzed: 7,
      model_confidence: "High",
      sensitivity: "medium",
    },
    anomaly_rate_gauge: { value: 5.2, max: 10, color: "amber" },
    parameter_breakdown: [],
    anomalies_over_time: [],
    anomaly_list: [],
    recommendations: [],
    metadata: {},
    reasoning: {
      summary: "A confirmed abnormal operating pattern was detected.",
      affected_parameters: ["temperature", "power"],
      recommended_action: "Inspect the cooling path and review load balance.",
      confidence: "High",
    },
    data_quality_flags: [],
  });

  assert.equal(summary.title, "Analysis Confidence");
  assert.equal(summary.evidenceStrength, "Strong");
  assert.equal(summary.recommendedAction, "Inspect the cooling path and review load balance.");
  assert.deepEqual(summary.factors, ["temperature", "power"]);
});

test("failure confidence summary falls back to safe business wording when needed", () => {
  const summary = getAnalyticsConfidenceSummary({
    analysis_type: "failure_prediction",
    device_id: "DEV-9",
    job_id: "job-9",
    health_score: 58,
    confidence: {
      level: "Moderate",
      badge_color: "#f59e0b",
      banner_text: "The forecast is based on a limited but usable data window.",
      banner_style: "amber",
      days_available: 4,
    },
    summary: {
      failure_risk: "High",
      failure_probability_pct: 67,
      failure_probability_meter: 67,
      safe_probability_pct: 33,
      estimated_remaining_life: "< 7 days",
      maintenance_urgency: "Urgent",
      confidence_level: "Moderate",
      days_analyzed: 4,
    },
    risk_breakdown: { safe_pct: 33, warning_pct: 41, critical_pct: 26 },
    risk_factors: [
      {
        parameter: "vibration",
        contribution_pct: 41,
        trend: "increasing",
        context: "Rising vibration during sustained runtime.",
        reasoning: "Elevated oscillation compared with the recent baseline.",
        current_value: 8,
        baseline_value: 5,
      },
    ],
    recommended_actions: [
      {
        rank: 1,
        action: "Inspect bearings and schedule maintenance.",
        urgency: "Urgent",
        reasoning: "Vibration is rising against the baseline.",
        parameter: "vibration",
      },
    ],
    metadata: {},
    reasoning: {
      summary: "Current machine behavior suggests a meaningful increase in maintenance risk.",
      evidence_text: "Evidence strength is moderate because several warning signals are aligned.",
      top_risk_factors: ["Vibration"],
      recommended_actions: ["Inspect bearings and schedule maintenance."],
      confidence: "Moderate",
    },
    degradation_series: [],
    data_quality_flags: [],
    attention_required: true,
  });

  assert.equal(summary.level, "Moderate");
  assert.equal(summary.evidenceStrength, "Moderate");
  assert.equal(summary.recommendedAction, "Inspect bearings and schedule maintenance.");
  assert.deepEqual(summary.factors, ["Vibration"]);
});

test("historical reasoning text with model details is sanitized before display", () => {
  const cleaned = sanitizeAnalyticsNarrative(
    "xgboost and lstm_classifier agree. 2/3 models agree on elevated risk.",
    "Evidence strength reflects the consistency of the observed telemetry pattern.",
  );

  assert.equal(
    cleaned,
    "Evidence strength reflects the consistency of the observed telemetry pattern.",
  );
});
