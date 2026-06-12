import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const machinePageSource = fs.readFileSync(
  path.join(process.cwd(), "app/(protected)/machines/[deviceId]/page.tsx"),
  "utf8",
);

test("overview charts expose history-aware range controls", () => {
  assert.equal(machinePageSource.includes('type OverviewChartRange = "live" | "6h" | "24h" | "7d";'), true);
  assert.equal(machinePageSource.includes("OVERVIEW_CHART_RANGE_OPTIONS"), true);
  assert.equal(machinePageSource.includes("Telemetry Trends"), true);
});

test("overview historical ranges use telemetry history instead of expanding the live buffer", () => {
  assert.equal(machinePageSource.includes("getOverviewHistoryParams(overviewChartRange)"), true);
  assert.equal(machinePageSource.includes("getTelemetryHistory(deviceId, params)"), true);
  assert.equal(machinePageSource.includes('aggregate: "mean"'), true);
  assert.equal(machinePageSource.includes('interval = "1m"'), true);
  assert.equal(machinePageSource.includes('interval = "5m"'), true);
  assert.equal(machinePageSource.includes('interval = "15m"'), true);
  assert.equal(machinePageSource.includes('setTelemetry((prev) => sortTelemetryAsc([...prev, latest]).slice(-100));'), true);
});

test("overview charts switch between live and historical telemetry sources", () => {
  assert.equal(
    machinePageSource.includes('const overviewChartTelemetry = overviewChartRange === "live" ? telemetry : overviewHistoryTelemetry;'),
    true,
  );
  assert.equal(machinePageSource.includes("getMetricData(overviewChartTelemetry, metric)"), true);
});
