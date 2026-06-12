import test from "node:test";
import assert from "node:assert/strict";

import {
  isRetryableMachineDetailBootstrapError,
  loadMachineDetailBootstrap,
  loadMachineDetailSummary,
} from "../../lib/machineDetailLoadContract.ts";

test("machine detail bootstrap retries a slow first attempt before succeeding", async () => {
  let attempts = 0;
  const retryAttempts: number[] = [];

  const result = await loadMachineDetailBootstrap({
    loadBootstrap: async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("Request timed out");
      }
      return {
        generated_at: "2026-04-30T00:00:00Z",
        version: 7,
        device: null,
        telemetry: [],
        uptime: {} as never,
        shifts: [],
        health_configs: [],
        health_score: null,
        widget_config: null,
        current_state: null,
        idle_stats: null,
        idle_config: null,
        waste_config: null,
        loss_stats: null,
        co2_overview: null,
      };
    },
    retryDelayMs: 0,
    onRetry: (attempt) => {
      retryAttempts.push(attempt);
    },
  });

  assert.equal(attempts, 2);
  assert.deepEqual(retryAttempts, [2]);
  assert.equal(result.fatalError, null);
  assert.equal(result.data?.version, 7);
});

test("machine detail bootstrap does not retry real fatal failures", async () => {
  let attempts = 0;

  const result = await loadMachineDetailBootstrap({
    loadBootstrap: async () => {
      attempts += 1;
      throw new Error("HTTP 404");
    },
    retryDelayMs: 0,
  });

  assert.equal(attempts, 1);
  assert.equal(result.fatalError, "HTTP 404");
  assert.equal(result.data, null);
});

test("machine detail bootstrap still fails after exhausting retryable attempts", async () => {
  let attempts = 0;

  const result = await loadMachineDetailBootstrap({
    loadBootstrap: async () => {
      attempts += 1;
      throw new Error("Request timed out");
    },
    retryDelayMs: 0,
  });

  assert.equal(attempts, 2);
  assert.equal(result.fatalError, "Request timed out");
  assert.equal(result.data, null);
});

test("machine detail retry classifier only retries transient bootstrap failures", () => {
  assert.equal(isRetryableMachineDetailBootstrapError(new Error("Request timed out")), true);
  assert.equal(isRetryableMachineDetailBootstrapError(new Error("HTTP 503")), true);
  assert.equal(isRetryableMachineDetailBootstrapError(new Error("HTTP 404")), false);
  assert.equal(isRetryableMachineDetailBootstrapError(new Error("HTTP 401")), false);
});

test("machine detail summary retries a timed-out first attempt before succeeding", async () => {
  let attempts = 0;

  const result = await loadMachineDetailSummary({
    loadSummary: async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("Request timed out");
      }
      return {
        generated_at: "2026-04-30T00:00:00Z",
        version: 7,
        device_id: "D-1",
        device_name: "Test",
        device_type: "motor",
        plant_id: null,
        location: null,
        runtime_status: "running",
        load_state: "running",
        current_band: "in_load",
        operational_status: "running",
        last_seen_timestamp: null,
        first_telemetry_timestamp: null,
        health_score: null,
        uptime_percentage: null,
        full_load_current_a: null,
        idle_threshold_pct_of_fla: null,
        derived_idle_threshold_a: null,
        derived_overconsumption_threshold_a: null,
        last_current_a: null,
        last_voltage_v: null,
        data_source_type: null,
        data_freshness_ts: null,
        live_updated_at: null,
        loss_overview: null,
        overview_readiness: {
          summary_ready: true,
          telemetry_ready: false,
          health_ready: false,
          uptime_ready: false,
          loss_ready: false,
        },
      };
    },
    retryDelayMs: 0,
  });

  assert.equal(attempts, 2);
  assert.equal(result.fatalError, null);
  assert.equal(result.data?.device_id, "D-1");
});

test("machine detail summary does not retry non-retryable failures", async () => {
  let attempts = 0;

  const result = await loadMachineDetailSummary({
    loadSummary: async () => {
      attempts += 1;
      throw new Error("HTTP 404");
    },
    retryDelayMs: 0,
  });

  assert.equal(attempts, 1);
  assert.equal(result.fatalError, "HTTP 404");
  assert.equal(result.data, null);
});
