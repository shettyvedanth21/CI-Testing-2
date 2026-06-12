import test from "node:test";
import assert from "node:assert/strict";

import {
  getTelemetryCoverageLabel,
  getTelemetryCoverageSummary,
} from "../../lib/telemetryCoverage.ts";

test("shared job presentation distinguishes telemetry business outcomes", () => {
  const partial = {
    level: "partial_coverage" as const,
    coverage_pct: 42.86,
    usable_for_business_decisions: true,
    message: "Telemetry coverage is partial; results are usable with coverage warnings.",
  };
  const insufficient = {
    level: "insufficient_coverage" as const,
    coverage_pct: 0,
    usable_for_business_decisions: false,
    message: "Telemetry coverage is insufficient for a trustworthy result.",
  };
  const noCoverage = {
    level: "no_coverage" as const,
    coverage_pct: 0,
    usable_for_business_decisions: false,
    message: "No telemetry was available for the selected window.",
  };

  assert.equal(getTelemetryCoverageLabel(partial), "Partial result");
  assert.equal(getTelemetryCoverageLabel(insufficient), "Insufficient coverage");
  assert.equal(getTelemetryCoverageLabel(noCoverage), "No data");
  assert.equal(getTelemetryCoverageSummary(partial), partial.message);
  assert.equal(getTelemetryCoverageSummary(insufficient), insufficient.message);
  assert.equal(getTelemetryCoverageSummary(noCoverage), noCoverage.message);
});
