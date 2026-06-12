import test from "node:test";
import assert from "node:assert/strict";

import {
  buildSyntheticMachineFromSummary,
  deriveMachineDetailShellState,
  shouldAcceptIncomingShellSummary,
} from "../../lib/machineDetailShellState.ts";
import type { Device } from "../../lib/deviceApi.ts";

test("machine detail shell state keeps summary identity and shell KPIs when bootstrap data disagrees", () => {
  const summary = {
    generated_at: "2026-05-05T00:00:00Z",
    version: 9,
    device_id: "D-1",
    device_name: "Summary Machine",
    device_type: "compressor",
    plant_id: "P-1",
    location: "Line A",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-05-05T00:00:00Z",
    first_telemetry_timestamp: "2026-05-01T00:00:00Z",
    health_score: 92,
    uptime_percentage: 88,
    current_shift_uptime_percentage: 88,
    daily_uptime_percentage: 100,
    full_load_current_a: 10,
    idle_threshold_pct_of_fla: 0.25,
    derived_idle_threshold_a: 2.5,
    derived_overconsumption_threshold_a: 10,
    last_current_a: 3.2,
    last_voltage_v: 230,
    data_source_type: "metered",
    data_freshness_ts: "2026-05-05T00:00:00Z",
    live_updated_at: "2026-05-05T00:00:00Z",
    loss_overview: {
      day_bucket: "2026-05-05",
      updated_at: "2026-05-05T00:00:00Z",
      last_telemetry_ts: "2026-05-05T00:00:00Z",
      currency: "INR",
      costs_available: true,
      idle_kwh: 0.2,
      idle_cost_inr: 2,
      off_hours_kwh: 0.1,
      off_hours_cost_inr: 1,
      overconsumption_kwh: 0.3,
      overconsumption_cost_inr: 3,
      total_loss_kwh: 0.6,
      total_loss_cost_inr: 6,
      today_energy_kwh: 4.2,
      co2_overview: null,
    },
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: true,
      loss_ready: true,
    },
  };

  const shell = deriveMachineDetailShellState({
    summary,
    shellCurrentState: null,
    fallbackMachine: {
      id: "D-1",
      name: "Bootstrap Machine",
      type: "motor",
      plant_id: "P-1",
      location: "Line B",
      runtime_status: "stopped",
      last_seen_timestamp: "2026-04-01T00:00:00Z",
      first_telemetry_timestamp: "2026-04-01T00:00:00Z",
      data_source_type: "sensor",
    } as Device,
    fallbackHealthPercent: 41,
    fallbackUptimePercent: 22,
  });

  assert.equal(shell.machine.name, "Summary Machine");
  assert.equal(shell.machine.type, "compressor");
  assert.equal(shell.machine.runtime_status, "running");
  assert.equal(shell.healthPercent, 92);
  assert.equal(shell.uptimePercent, 88);
  assert.equal(shell.operationalStatus, "running");
  assert.equal(shell.lossOverview?.total_loss_kwh, 0.6);
  assert.equal(shell.overviewReadiness.loss_ready, true);
});

test("machine detail shell state keeps summary uptime ahead of fallback bootstrap uptime", () => {
  const summary = {
    generated_at: "2026-05-05T00:00:00Z",
    version: 9,
    device_id: "D-1",
    device_name: "Summary Machine",
    device_type: "compressor",
    plant_id: "P-1",
    location: "Line A",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-05-05T00:00:00Z",
    first_telemetry_timestamp: "2026-05-01T00:00:00Z",
    health_score: 92,
    uptime_percentage: 88,
    current_shift_uptime_percentage: 88,
    daily_uptime_percentage: 100,
    full_load_current_a: 10,
    idle_threshold_pct_of_fla: 0.25,
    derived_idle_threshold_a: 2.5,
    derived_overconsumption_threshold_a: 10,
    last_current_a: 3.2,
    last_voltage_v: 230,
    data_source_type: "metered",
    data_freshness_ts: "2026-05-05T00:00:00Z",
    live_updated_at: "2026-05-05T00:00:00Z",
    loss_overview: null,
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: true,
      loss_ready: false,
    },
  };

  const shell = deriveMachineDetailShellState({
    summary,
    shellCurrentState: null,
    fallbackMachine: null,
    fallbackHealthPercent: null,
    fallbackUptimePercent: 15.7,
  });

  assert.equal(shell.uptimePercent, 88);
  assert.equal(shell.machine.name, "Summary Machine");
});

test("machine detail shell state does not show daily uptime as active shift uptime", () => {
  const summary = {
    generated_at: "2026-05-05T16:15:00Z",
    version: 12,
    device_id: "D-1",
    device_name: "Summary Machine",
    device_type: "compressor",
    plant_id: "P-1",
    location: "Line A",
    runtime_status: "running",
    load_state: "idle",
    current_band: "idle",
    operational_status: "idle",
    last_seen_timestamp: "2026-05-05T16:15:00Z",
    first_telemetry_timestamp: "2026-05-01T00:00:00Z",
    health_score: 92,
    uptime_percentage: null,
    current_shift_uptime_percentage: null,
    daily_uptime_percentage: 79.5,
    full_load_current_a: 10,
    idle_threshold_pct_of_fla: 0.25,
    derived_idle_threshold_a: 2.5,
    derived_overconsumption_threshold_a: 10,
    last_current_a: 1.2,
    last_voltage_v: 230,
    data_source_type: "metered",
    data_freshness_ts: "2026-05-05T16:15:00Z",
    live_updated_at: "2026-05-05T16:15:00Z",
    loss_overview: null,
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: false,
      loss_ready: false,
    },
  };

  const shell = deriveMachineDetailShellState({
    summary,
    shellCurrentState: null,
    fallbackMachine: null,
    fallbackHealthPercent: null,
    fallbackUptimePercent: null,
  });

  assert.equal(shell.uptimePercent, null);
  assert.equal(shell.overviewReadiness.uptime_ready, false);
});

test("machine detail shell state allows polled current state to refine load state without bootstrap ownership", () => {
  const summary = {
    generated_at: "2026-05-05T00:00:00Z",
    version: 9,
    device_id: "D-1",
    device_name: "Summary Machine",
    device_type: "compressor",
    plant_id: "P-1",
    location: "Line A",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-05-05T00:00:00Z",
    first_telemetry_timestamp: "2026-05-01T00:00:00Z",
    health_score: 92,
    uptime_percentage: 88,
    current_shift_uptime_percentage: 88,
    daily_uptime_percentage: 100,
    full_load_current_a: null,
    idle_threshold_pct_of_fla: null,
    derived_idle_threshold_a: null,
    derived_overconsumption_threshold_a: null,
    last_current_a: null,
    last_voltage_v: null,
    data_source_type: "metered",
    data_freshness_ts: "2026-05-05T00:00:00Z",
    live_updated_at: "2026-05-05T00:00:00Z",
    loss_overview: null,
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: true,
      loss_ready: false,
    },
  };

  const shell = deriveMachineDetailShellState({
    summary,
    shellCurrentState: {
      device_id: "D-1",
      state: "idle",
      current_band: "idle",
      current: 1.2,
      voltage: 230,
      threshold: 2.5,
      full_load_current_a: null,
      idle_threshold_pct_of_fla: null,
      derived_idle_threshold_a: null,
      derived_overconsumption_threshold_a: null,
      timestamp: "2026-05-05T00:01:00Z",
      current_field: "current",
      voltage_field: "voltage",
    },
    fallbackMachine: null,
    fallbackHealthPercent: null,
    fallbackUptimePercent: null,
  });

  assert.equal(shell.effectiveLoadState, "idle");
  assert.equal(shell.currentBand, "idle");
  assert.equal(shell.operationalStatus, "idle");
});

test("machine detail shell state keeps summary shell KPIs when polled current state only refines machine-now status", () => {
  const summary = {
    generated_at: "2026-05-05T00:00:00Z",
    version: 9,
    device_id: "D-1",
    device_name: "Summary Machine",
    device_type: "compressor",
    plant_id: "P-1",
    location: "Line A",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-05-05T00:00:00Z",
    first_telemetry_timestamp: "2026-05-01T00:00:00Z",
    health_score: 92,
    uptime_percentage: 88,
    current_shift_uptime_percentage: 88,
    daily_uptime_percentage: 100,
    full_load_current_a: 10,
    idle_threshold_pct_of_fla: 0.25,
    derived_idle_threshold_a: 2.5,
    derived_overconsumption_threshold_a: 10,
    last_current_a: 9.1,
    last_voltage_v: 230,
    data_source_type: "metered",
    data_freshness_ts: "2026-05-05T00:00:00Z",
    live_updated_at: "2026-05-05T00:00:00Z",
    loss_overview: null,
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: true,
      loss_ready: false,
    },
  };

  const shell = deriveMachineDetailShellState({
    summary,
    shellCurrentState: {
      device_id: "D-1",
      state: "unknown",
      current_band: "unknown",
      current: 0.4,
      voltage: 230,
      threshold: 2.5,
      full_load_current_a: 10,
      idle_threshold_pct_of_fla: 0.25,
      derived_idle_threshold_a: 2.5,
      derived_overconsumption_threshold_a: 10,
      timestamp: "2026-05-05T00:01:00Z",
      current_field: "current",
      voltage_field: "voltage",
    },
    fallbackMachine: {
      id: "D-1",
      name: "Bootstrap Machine",
      type: "motor",
      plant_id: "P-1",
      location: "Line B",
      runtime_status: "stopped",
      last_seen_timestamp: "2026-04-01T00:00:00Z",
      first_telemetry_timestamp: "2026-04-01T00:00:00Z",
      data_source_type: "sensor",
    } as Device,
    fallbackHealthPercent: 11,
    fallbackUptimePercent: 22,
  });

  assert.equal(shell.machine.name, "Summary Machine");
  assert.equal(shell.machine.type, "compressor");
  assert.equal(shell.healthPercent, 92);
  assert.equal(shell.uptimePercent, 88);
  assert.equal(shell.effectiveLoadState, "unknown");
  assert.equal(shell.currentBand, "unknown");
});

test("machine summary can still synthesize a shell machine before detailed hydration arrives", () => {
  const machine = buildSyntheticMachineFromSummary({
    generated_at: "2026-05-05T00:00:00Z",
    version: 1,
    device_id: "D-1",
    device_name: "Summary Machine",
    device_type: "compressor",
    plant_id: "P-1",
    location: "Line A",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-05-05T00:00:00Z",
    first_telemetry_timestamp: "2026-05-01T00:00:00Z",
    health_score: 92,
    uptime_percentage: 88,
    full_load_current_a: null,
    idle_threshold_pct_of_fla: null,
    derived_idle_threshold_a: null,
    derived_overconsumption_threshold_a: null,
    last_current_a: null,
    last_voltage_v: null,
    data_source_type: "metered",
    data_freshness_ts: "2026-05-05T00:00:00Z",
    live_updated_at: "2026-05-05T00:00:00Z",
    loss_overview: null,
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: true,
      loss_ready: false,
    },
  });

  assert.equal(machine.id, "D-1");
  assert.equal(machine.name, "Summary Machine");
  assert.equal(machine.runtime_status, "running");
});

test("machine detail shell summary rejects stale lower-version refreshes", () => {
  const current = {
    generated_at: "2026-05-05T00:01:00Z",
    version: 11,
    device_id: "D-1",
    device_name: "Summary Machine",
    device_type: "compressor",
    plant_id: "P-1",
    location: "Line A",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-05-05T00:01:00Z",
    first_telemetry_timestamp: "2026-05-01T00:00:00Z",
    health_score: 92,
    uptime_percentage: 88,
    full_load_current_a: null,
    idle_threshold_pct_of_fla: null,
    derived_idle_threshold_a: null,
    derived_overconsumption_threshold_a: null,
    last_current_a: 9.1,
    last_voltage_v: 230,
    data_source_type: "metered",
    data_freshness_ts: "2026-05-05T00:01:00Z",
    live_updated_at: "2026-05-05T00:01:00Z",
    loss_overview: null,
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: true,
      loss_ready: false,
    },
  };

  const incoming = {
    ...current,
    generated_at: "2026-05-05T00:00:30Z",
    version: 10,
    last_seen_timestamp: "2026-05-05T00:00:30Z",
    live_updated_at: "2026-05-05T00:00:30Z",
  };

  assert.equal(shouldAcceptIncomingShellSummary(current, incoming), false);
});

test("machine detail shell summary accepts same-version refreshes with newer live freshness", () => {
  const current = {
    generated_at: "2026-05-05T00:01:00Z",
    version: 11,
    device_id: "D-1",
    device_name: "Summary Machine",
    device_type: "compressor",
    plant_id: "P-1",
    location: "Line A",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-05-05T00:01:00Z",
    first_telemetry_timestamp: "2026-05-01T00:00:00Z",
    health_score: 92,
    uptime_percentage: 88,
    full_load_current_a: null,
    idle_threshold_pct_of_fla: null,
    derived_idle_threshold_a: null,
    derived_overconsumption_threshold_a: null,
    last_current_a: 9.1,
    last_voltage_v: 230,
    data_source_type: "metered",
    data_freshness_ts: "2026-05-05T00:01:00Z",
    live_updated_at: "2026-05-05T00:01:00Z",
    loss_overview: null,
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: true,
      loss_ready: false,
    },
  };

  const incoming = {
    ...current,
    generated_at: "2026-05-05T00:02:00Z",
    live_updated_at: "2026-05-05T00:02:00Z",
    last_seen_timestamp: "2026-05-05T00:02:00Z",
    last_current_a: 9.8,
  };

  assert.equal(shouldAcceptIncomingShellSummary(current, incoming), true);
});
