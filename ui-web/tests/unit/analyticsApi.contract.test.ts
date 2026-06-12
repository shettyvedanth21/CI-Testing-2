import test from "node:test";
import assert from "node:assert/strict";

import { normalizeFormattedAnalyticsResult } from "../../lib/analyticsApi.ts";

test("normalizeFormattedAnalyticsResult maps internal blocked analysis types to customer-facing contract", () => {
  const normalized = normalizeFormattedAnalyticsResult({
    analysis_type: "anomaly",
    status: "no_data",
    job_id: "job-1",
    device_id: "DEV-1",
    summary: "No telemetry was available for the selected window.",
  });

  assert.equal(normalized.analysis_type, "anomaly_detection");
});

test("normalizeFormattedAnalyticsResult maps prediction to failure_prediction", () => {
  const normalized = normalizeFormattedAnalyticsResult({
    analysis_type: "prediction",
    status: "insufficient_coverage",
    job_id: "job-2",
    device_id: "DEV-2",
    summary: "Coverage is not enough to run this analysis.",
  });

  assert.equal(normalized.analysis_type, "failure_prediction");
});
