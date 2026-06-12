import assert from "node:assert/strict";
import test from "node:test";

import { getVisibleDeviceDetailTabs } from "../../lib/deviceDetailTabs.ts";

test("viewer only sees read-only device detail tabs", () => {
  assert.deepEqual(
    getVisibleDeviceDetailTabs({ isReadOnly: true, canEditDevice: false, canCreateRule: false }),
    [
      { id: "overview", label: "Overview" },
      { id: "telemetry", label: "Telemetry" },
      { id: "maintenance", label: "Maintenance Log" },
    ],
  );
});

test("operator sees rules tab but not parameter configuration", () => {
  assert.deepEqual(
    getVisibleDeviceDetailTabs({ isReadOnly: false, canEditDevice: false, canCreateRule: true }),
    [
      { id: "overview", label: "Overview" },
      { id: "telemetry", label: "Telemetry" },
      { id: "maintenance", label: "Maintenance Log" },
      { id: "rules", label: "Configure Rules" },
    ],
  );
});

test("device editors keep full machine detail tab set", () => {
  assert.deepEqual(
    getVisibleDeviceDetailTabs({ isReadOnly: false, canEditDevice: true, canCreateRule: true }),
    [
      { id: "overview", label: "Overview" },
      { id: "telemetry", label: "Telemetry" },
      { id: "maintenance", label: "Maintenance Log" },
      { id: "parameters", label: "Parameter Configuration" },
      { id: "rules", label: "Configure Rules" },
    ],
  );
});
