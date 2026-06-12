import test from "node:test";
import assert from "node:assert/strict";

import {
  areAllSelectableDevicesSelected,
  normalizeSelectedDeviceIds,
} from "../../lib/deviceSelection.ts";

test("normalizeSelectedDeviceIds removes stale device ids when accessible devices change", () => {
  assert.deepEqual(
    normalizeSelectedDeviceIds(["dev-a", "dev-c", "dev-b"], ["dev-a", "dev-b"]),
    ["dev-a", "dev-b"],
  );
});

test("normalizeSelectedDeviceIds returns empty selection when nothing is accessible", () => {
  assert.deepEqual(normalizeSelectedDeviceIds(["dev-a"], []), []);
});

test("areAllSelectableDevicesSelected only returns true when every accessible device is selected", () => {
  assert.equal(areAllSelectableDevicesSelected(["dev-a"], ["dev-a", "dev-b"]), false);
  assert.equal(areAllSelectableDevicesSelected(["dev-a", "dev-b"], ["dev-a", "dev-b"]), true);
  assert.equal(areAllSelectableDevicesSelected(["dev-a", "dev-b", "dev-c"], ["dev-a", "dev-b"]), true);
});
