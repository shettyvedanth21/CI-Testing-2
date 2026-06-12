import test from "node:test";
import assert from "node:assert/strict";

import {
  getEmptyReportHistoryMessage,
  getEmptyScheduleMessage,
  getReportPageSubtitle,
  getReportScopeHint,
  getReportScopeLabel,
  isPlantScopedReportRole,
} from "../../lib/reportScope.ts";

test("plant-scoped report roles are recognized", () => {
  assert.equal(isPlantScopedReportRole("plant_manager"), true);
  assert.equal(isPlantScopedReportRole("operator"), true);
  assert.equal(isPlantScopedReportRole("viewer"), true);
  assert.equal(isPlantScopedReportRole("org_admin"), false);
});

test("plant-scoped report roles get accessible-device semantics", () => {
  assert.equal(getReportScopeLabel("plant_manager"), "All Accessible Devices");
  assert.match(getReportScopeHint("plant_manager") ?? "", /assigned plants/i);
  assert.match(getReportPageSubtitle("plant_manager"), /accessible plants/i);
  assert.match(getEmptyReportHistoryMessage("viewer"), /accessible devices/i);
  assert.match(getEmptyScheduleMessage("operator"), /accessible devices/i);
});

test("org-wide report roles keep general semantics", () => {
  assert.equal(getReportScopeLabel("org_admin"), "All Devices");
  assert.equal(getReportScopeHint("org_admin"), null);
  assert.equal(getEmptyReportHistoryMessage("org_admin"), "No reports generated yet");
  assert.equal(getEmptyScheduleMessage("org_admin"), "No schedules configured yet");
});
