import test from "node:test";
import assert from "node:assert/strict";

import { deriveMachineKpiState } from "../../lib/machineDetailKpiState.ts";

test("machine KPI state reports degraded truthfully when hydration fails", () => {
  const state = deriveMachineKpiState({
    hydrationLoading: false,
    hydrationFailed: true,
    hydrationError: "Detailed machine KPIs could not be loaded.",
    hasTelemetry: false,
    dynamicMetricCount: 0,
  });

  assert.equal(state.kind, "degraded");
  assert.match(state.message ?? "", /could not be loaded/i);
});

test("machine KPI state reports loading while deferred hydration is in flight", () => {
  const state = deriveMachineKpiState({
    hydrationLoading: true,
    hydrationFailed: false,
    hydrationError: null,
    hasTelemetry: false,
    dynamicMetricCount: 0,
  });

  assert.equal(state.kind, "loading");
});

test("machine KPI state reports telemetry wait instead of blank overview cards", () => {
  const state = deriveMachineKpiState({
    hydrationLoading: false,
    hydrationFailed: false,
    hydrationError: null,
    hasTelemetry: false,
    dynamicMetricCount: 0,
  });

  assert.equal(state.kind, "waiting_for_telemetry");
  assert.match(state.message ?? "", /telemetry/i);
});

test("machine KPI state is ready when snapshot-backed overview metrics are available", () => {
  const state = deriveMachineKpiState({
    hydrationLoading: false,
    hydrationFailed: false,
    hydrationError: null,
    hasTelemetry: true,
    dynamicMetricCount: 3,
  });

  assert.equal(state.kind, "ready");
  assert.equal(state.title, null);
  assert.equal(state.message, null);
});
