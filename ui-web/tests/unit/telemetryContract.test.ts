import assert from "node:assert/strict";
import test from "node:test";

import {
  isAnalyticsBusinessFeature,
  isPhaseDiagnosticField,
  isRuleSelectableMetric,
} from "../../lib/telemetryContract.ts";

test("rule metric selector excludes diagnostic phase fields", () => {
  const fields = [
    "current",
    "voltage",
    "power",
    "temperature",
    "current_l1",
    "current_l2",
    "current_l3",
    "voltage_l1",
    "voltage_l2",
    "voltage_l3",
  ];

  const selectable = fields.filter(isRuleSelectableMetric);

  assert.deepEqual(selectable, ["current", "voltage", "power", "temperature"]);
  assert.equal(isPhaseDiagnosticField("current_l1"), true);
  assert.equal(isPhaseDiagnosticField("voltage_l3"), true);
});

test("analytics business feature contract excludes phase diagnostics", () => {
  assert.equal(isAnalyticsBusinessFeature("current"), true);
  assert.equal(isAnalyticsBusinessFeature("voltage"), true);
  assert.equal(isAnalyticsBusinessFeature("current_l1"), false);
  assert.equal(isAnalyticsBusinessFeature("voltage_l1"), false);
  assert.equal(isAnalyticsBusinessFeature("power_factor_l1"), false);
});
