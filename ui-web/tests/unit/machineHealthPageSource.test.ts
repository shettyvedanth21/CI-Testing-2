import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const machinePageSource = fs.readFileSync(
  path.join(process.cwd(), "app/(protected)/machines/[deviceId]/page.tsx"),
  "utf8",
);

test("machine page computes machineHealthEnabled from hasFeature", () => {
  assert.equal(
    machinePageSource.includes('const machineHealthEnabled = hasFeature(me, "machine_health");'),
    true,
  );
});

test("machine page renders LockedPremiumCard for risk assessment when machine_health disabled", () => {
  assert.equal(
    machinePageSource.includes('<LockedPremiumCard feature="machine_health" description="Risk assessment scores, signal breakdown, and degradation trends." />'),
    true,
  );
});

test("machine page renders LockedPremiumCard for anomaly activity when machine_health disabled", () => {
  assert.equal(
    machinePageSource.includes('<LockedPremiumCard feature="machine_health" description="Anomaly activity counts, severity tracking, and event timeline." />'),
    true,
  );
});

test("degradation effect includes machineHealthEnabled in dependency array", () => {
  const degradationEffectMatch = machinePageSource.includes(
    "}, [deviceId, shellSummary, machineHealthEnabled]);",
  );
  assert.equal(degradationEffectMatch, true);
});

test("degradation effect clears state when machineHealthEnabled is false", () => {
  assert.equal(machinePageSource.includes("if (!machineHealthEnabled) {\n      setDegradationScore(null);"), true);
  assert.equal(machinePageSource.includes("setDegradationError(null);"), true);
  assert.equal(machinePageSource.includes("setDegradationLoading(false);"), true);
});

test("anomaly effect clears state when machineHealthEnabled is false", () => {
  assert.equal(machinePageSource.includes("if (!machineHealthEnabled) {\n      setAnomalyActivity(null);"), true);
  assert.equal(machinePageSource.includes("setAnomalyError(null);"), true);
  assert.equal(machinePageSource.includes("setAnomalyLoading(false);"), true);
});

test("health polling skips degradation and anomaly fetches when machineHealthEnabled is false", () => {
  assert.equal(machinePageSource.includes("if (!machineHealthEnabled) return;\n      let settled"), true);
});

test("machine health section uses conditional render based on hasFeature", () => {
  assert.equal(machinePageSource.includes('hasFeature(me, "machine_health") ? ('), true);
});
