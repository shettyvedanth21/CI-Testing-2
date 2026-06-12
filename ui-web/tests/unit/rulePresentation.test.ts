import test from "node:test";
import assert from "node:assert/strict";

import {
  RULE_TYPE_OPTIONS,
  getRuleConditionSummary,
  getRuleTriggerSummary,
  getRuleTypeHelperText,
  getRuleTypeLabel,
} from "../../lib/rulePresentation.ts";

test("rule type options expose continuous idle duration", () => {
  assert.deepEqual(
    RULE_TYPE_OPTIONS.map((option) => option.value),
    ["threshold", "time_based", "continuous_idle_duration"],
  );
  assert.equal(
    RULE_TYPE_OPTIONS.find((option) => option.value === "continuous_idle_duration")?.label,
    "Continuous Idle Duration",
  );
});

test("continuous idle type helper text and summaries are readable", () => {
  assert.equal(getRuleTypeLabel("continuous_idle_duration"), "Continuous Idle Duration");
  assert.equal(
    getRuleTypeHelperText("continuous_idle_duration"),
    "Alert when the machine stays idle continuously for N minutes.",
  );

  const rule = {
    ruleType: "continuous_idle_duration",
    durationMinutes: 40,
    property: null,
    condition: null,
    threshold: null,
    timeWindowStart: null,
    timeWindowEnd: null,
  } as const;

  assert.equal(getRuleTriggerSummary(rule), "Idle continuously for 40 minutes");
  assert.equal(getRuleConditionSummary(rule), "40 minutes continuous idle");
});

