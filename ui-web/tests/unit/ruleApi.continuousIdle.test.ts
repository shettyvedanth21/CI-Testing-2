import test from "node:test";
import assert from "node:assert/strict";

import {
  buildCreateRuleRequestBody,
  mapRawRule,
} from "../../lib/ruleApiContract.ts";

test("buildCreateRuleRequestBody sends duration_minutes only for continuous idle rules", () => {
  const body = buildCreateRuleRequestBody({
    ruleName: "Idle 40",
    ruleType: "continuous_idle_duration",
    scope: "selected_devices",
    durationMinutes: 40,
    notificationChannels: ["email"],
    deviceIds: ["D1"],
    cooldownMode: "interval",
    cooldownUnit: "minutes",
    cooldownMinutes: 15,
  });

  assert.equal(body.rule_type, "continuous_idle_duration");
  assert.equal(body.duration_minutes, 40);
  assert.equal("time_window_start" in body, false);
  assert.equal("time_window_end" in body, false);
  assert.equal("time_condition" in body, false);
});

test("buildCreateRuleRequestBody keeps time-based payload fields unchanged", () => {
  const body = buildCreateRuleRequestBody({
    ruleName: "Night Watch",
    ruleType: "time_based",
    scope: "selected_devices",
    timeWindowStart: "20:00",
    timeWindowEnd: "06:00",
    timeCondition: "running_in_window",
    notificationChannels: ["email"],
    deviceIds: ["D1"],
    cooldownMode: "interval",
    cooldownUnit: "minutes",
    cooldownMinutes: 15,
  });

  assert.equal(body.rule_type, "time_based");
  assert.equal(body.time_window_start, "20:00");
  assert.equal(body.time_window_end, "06:00");
  assert.equal(body.time_condition, "running_in_window");
  assert.equal("duration_minutes" in body, false);
});

test("mapRawRule maps duration_minutes directly to the UI rule type", () => {
  const rule = mapRawRule({
    rule_id: "r1",
    rule_name: "Idle 40",
    rule_type: "continuous_idle_duration",
    scope: "selected_devices",
    duration_minutes: 40,
    notification_channels: ["email"],
    cooldown_minutes: 15,
    device_ids: ["D1"],
    status: "active",
    created_at: "2026-04-12T00:00:00Z",
  } as never);

  assert.equal(rule.ruleType, "continuous_idle_duration");
  assert.equal(rule.durationMinutes, 40);
});
