import test from "node:test";
import assert from "node:assert/strict";

import {
  getAllDevicesScopeLabel,
  getRuleDeviceScopeDisplay,
  getRuleScopeOptions,
  getRulesPageSubtitle,
  getRulesScopeHint,
  isPlantScopedRuleRole,
} from "../../lib/ruleScope.ts";

test("plant-scoped roles are treated as accessible-device scoped", () => {
  assert.equal(isPlantScopedRuleRole("plant_manager"), true);
  assert.equal(isPlantScopedRuleRole("operator"), true);
  assert.equal(isPlantScopedRuleRole("viewer"), true);
  assert.equal(isPlantScopedRuleRole("org_admin"), false);
});

test("plant-scoped roles get accessible-device labels and hints", () => {
  assert.equal(getAllDevicesScopeLabel("operator"), "All Accessible Devices");
  assert.equal(getRuleScopeOptions("viewer")[0]?.label, "All Accessible Devices");
  assert.match(getRulesPageSubtitle("plant_manager"), /accessible machines/i);
  assert.match(getRulesScopeHint("operator") ?? "", /assigned plants/i);
});

test("org-wide roles keep org-wide labels", () => {
  assert.equal(getAllDevicesScopeLabel("org_admin"), "All Devices");
  assert.equal(getRuleScopeOptions("org_admin")[0]?.label, "All Devices");
  assert.equal(getRulesScopeHint("org_admin"), null);
});

test("rule device scope display is consistent for zero and selected devices", () => {
  assert.equal(getRuleDeviceScopeDisplay([], "viewer", (deviceId) => deviceId), "All Accessible Devices");
  assert.equal(
    getRuleDeviceScopeDisplay(["d1", "d2"], "org_admin", (deviceId) => `Machine ${deviceId}`),
    "Machine d1, Machine d2",
  );
});
