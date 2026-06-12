import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const machinePageSource = fs.readFileSync(
  path.join(process.cwd(), "app/(protected)/machines/[deviceId]/page.tsx"),
  "utf8",
);

test("health configuration modal exposes a visible saving state", () => {
  assert.equal(
    machinePageSource.includes("Saving configuration and refreshing machine details..."),
    true,
  );
  assert.equal(machinePageSource.includes('const [saveInFlight, setSaveInFlight] = useState(false);'), true);
  assert.equal(machinePageSource.includes('{saveInFlight ? "Saving..." : isWeightValid ? "Save" : `Save (${totalWeight.toFixed(0)}%)`}'), true);
});

test("health configuration modal blocks duplicate actions while saving", () => {
  assert.equal(machinePageSource.includes("disabled={saveInFlight || deleteInFlight}"), true);
  assert.equal(machinePageSource.includes("if (saveInFlight || deleteInFlight) {"), true);
});

test("health configuration reconciliation fully backgrounds post-save refresh work", () => {
  assert.equal(machinePageSource.includes("void fetchData(false).catch"), true);
  assert.equal(machinePageSource.includes("void fetchHydration().catch"), true);
  assert.equal(machinePageSource.includes("void refreshShellSummary().catch"), true);
  assert.equal(machinePageSource.includes("void loadPerformanceTrends().catch"), true);
  assert.equal(machinePageSource.includes("await reconcileAfterHealthConfigChange();"), false);
});
