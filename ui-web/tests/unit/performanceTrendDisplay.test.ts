import test from "node:test";
import assert from "node:assert/strict";

import { buildPerformanceTrendDisplayModel } from "../../lib/performanceTrendDisplay.ts";
import type { PerformanceTrendData } from "../../lib/deviceApi.ts";


function baseTrendData(): PerformanceTrendData {
  return {
    device_id: "DEVICE-1",
    metric: "health",
    range: "1h",
    interval_minutes: 5,
    timezone: "Asia/Kolkata",
    points: [],
    total_points: 0,
    sampled_points: 0,
    message: "",
    metric_message: "",
    range_start: "2026-04-04T10:00:00+05:30",
    range_end: "2026-04-04T11:00:00+05:30",
    is_stale: false,
    last_actual_timestamp: null,
    fallback_point: null,
  };
}

test("buildPerformanceTrendDisplayModel returns measured chart data when points exist", () => {
  const trendData = baseTrendData();
  trendData.points = [
    {
      timestamp: "2026-04-04T10:15:00+05:30",
      health_score: 72,
      uptime_percentage: 90,
      planned_minutes: 30,
      effective_minutes: 25,
      break_minutes: 0,
    },
  ];
  trendData.metric_message = "Measured normally.";

  const result = buildPerformanceTrendDisplayModel(trendData, "health");

  assert.equal(result.hasMeasuredData, true);
  assert.equal(result.hasFallbackOnly, false);
  assert.equal(result.empty, false);
  assert.deepEqual(result.chartData, [{
    timestamp: "2026-04-04T10:15:00+05:30",
    value: 72,
    actualTimestamp: "2026-04-04T10:15:00+05:30",
    stale: false,
  }]);
});

test("buildPerformanceTrendDisplayModel returns stale carry-forward series when only fallback exists", () => {
  const trendData = baseTrendData();
  trendData.metric_message = "No new health points in selected window.";
  trendData.last_actual_timestamp = "2026-04-04T09:45:00+05:30";
  trendData.fallback_point = {
    timestamp: "2026-04-04T09:45:00+05:30",
    value: 68,
  };

  const result = buildPerformanceTrendDisplayModel(trendData, "health");

  assert.equal(result.hasMeasuredData, false);
  assert.equal(result.hasFallbackOnly, true);
  assert.equal(result.empty, false);
  assert.deepEqual(result.staleChartData, [
    {
      timestamp: "2026-04-04T10:00:00+05:30",
      value: 68,
      actualTimestamp: "2026-04-04T09:45:00+05:30",
      stale: true,
    },
    {
      timestamp: "2026-04-04T11:00:00+05:30",
      value: 68,
      actualTimestamp: "2026-04-04T09:45:00+05:30",
      stale: true,
    },
  ]);
  assert.match(result.staleLabel ?? "", /Last actual point at/);
});

test("buildPerformanceTrendDisplayModel preserves true empty state when no fallback exists", () => {
  const trendData = baseTrendData();
  trendData.metric = "uptime";
  trendData.message = "No uptime trend data available for the selected window.";
  trendData.metric_message = "No uptime trend data available for the selected window.";

  const result = buildPerformanceTrendDisplayModel(trendData, "uptime");

  assert.equal(result.empty, true);
  assert.equal(result.hasFallbackOnly, false);
  assert.equal(result.message, "No uptime trend data available for the selected window.");
});
