import test, { afterEach, mock } from "node:test";
import assert from "node:assert/strict";

import { clearAccessToken, setAccessToken } from "../../lib/browserSession.ts";
import { getDashboardBootstrapSummary, getDashboardBootstrap } from "../../lib/deviceApi.ts";

class SessionStorageMock {
  private readonly store = new Map<string, string>();
  getItem(key: string): string | null { return this.store.has(key) ? this.store.get(key)! : null; }
  setItem(key: string, value: string): void { this.store.set(key, value); }
  removeItem(key: string): void { this.store.delete(key); }
  clear(): void { this.store.clear(); }
}

function base64UrlEncode(value: object): string {
  return Buffer.from(JSON.stringify(value), "utf8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function makeToken(payload: object): string {
  return `header.${base64UrlEncode(payload)}.signature`;
}

function installWindow(): SessionStorageMock {
  const sessionStorage = new SessionStorageMock();
  Object.defineProperty(globalThis, "window", {
    value: {
      sessionStorage,
      setTimeout: (fn: (...args: unknown[]) => unknown, ms?: number) => globalThis.setTimeout(fn, ms),
      clearTimeout: (id?: unknown) => globalThis.clearTimeout(id as number | undefined),
    },
    configurable: true,
    writable: true,
  });
  return sessionStorage;
}

function co2OverviewJson(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    available: true,
    today: {
      energy_kwh: 100.0,
      co2_kg: 71.6,
      loss_kwh: 2.0,
      avoidable_co2_kg: 1.432,
      available: true,
      avoidable_co2_available: true,
      avoidable_co2_reason: null,
    },
    week: { available: false, reason: "weekly_projection_not_supported" },
    month: {
      energy_kwh: 2400.0,
      co2_kg: 1718.4,
      available: true,
      avoidable_co2_available: false,
      avoidable_co2_reason: "monthly_loss_projection_not_supported",
    },
    factor: {
      value: 0.716,
      unit: "kg_co2_per_kwh",
      method: "location_based",
      country: "IN",
      region: "all_india_grid",
      source: "Central Electricity Authority CO2 Baseline Database",
      source_version: "Version 19.0",
      factor_year: "FY2022-23",
    },
    factor_source: "platform_default",
    calculation_version: "co2_scope2_v1",
    ...overrides,
  };
}

function summaryJsonWithCo2(co2: Record<string, unknown> | null = co2OverviewJson()): Record<string, unknown> {
  return {
    generated_at: "2026-06-04T10:00:00Z",
    version: 1,
    device_id: "DEV-001",
    device_name: "Test Device",
    device_type: "compressor",
    plant_id: "PLANT-1",
    location: null,
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-06-04T09:59:00Z",
    first_telemetry_timestamp: "2026-06-01T00:00:00Z",
    health_score: 85,
    uptime_percentage: 92.5,
    current_shift_uptime_percentage: 95.0,
    daily_uptime_percentage: 90.0,
    full_load_current_a: 100.0,
    idle_threshold_pct_of_fla: 30,
    derived_idle_threshold_a: 30.0,
    derived_overconsumption_threshold_a: 110.0,
    last_current_a: 50.0,
    last_voltage_v: 415.0,
    data_source_type: "metered",
    data_freshness_ts: "2026-06-04T10:00:00Z",
    live_updated_at: "2026-06-04T10:00:00Z",
    loss_overview: {
      day_bucket: "2026-06-04",
      updated_at: "2026-06-04T10:00:00Z",
      last_telemetry_ts: "2026-06-04T09:59:00Z",
      currency: "INR",
      costs_available: true,
      idle_kwh: 1.0,
      idle_cost_inr: 5.0,
      off_hours_kwh: 0.5,
      off_hours_cost_inr: 2.5,
      overconsumption_kwh: 0.5,
      overconsumption_cost_inr: 2.5,
      total_loss_kwh: 2.0,
      total_loss_cost_inr: 10.0,
      today_energy_kwh: 100.0,
      co2_overview: co2,
    },
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: true,
      uptime_ready: true,
      loss_ready: true,
    },
  };
}

function bootstrapJsonWithCo2(topLevelCo2: Record<string, unknown> | null = co2OverviewJson()): Record<string, unknown> {
  return {
    generated_at: "2026-06-04T10:00:00Z",
    version: 1,
    success: true,
    device: null,
    telemetry: [],
    uptime: {},
    shifts: [],
    health_configs: [],
    health_score: null,
    widget_config: null,
    current_state: null,
    idle_stats: null,
    idle_config: null,
    waste_config: null,
    loss_stats: {
      device_id: "DEV-001",
      day_bucket: "2026-06-04",
      last_telemetry_ts: null,
      updated_at: null,
      tariff_configured: false,
      currency: "INR",
      today: {
        idle_kwh: 0,
        idle_cost_inr: null,
        off_hours_kwh: 0,
        off_hours_cost_inr: null,
        overconsumption_kwh: 0,
        overconsumption_cost_inr: null,
        total_loss_kwh: 0,
        total_loss_cost_inr: null,
        today_energy_kwh: 0,
        today_energy_cost_inr: null,
      },
      co2_overview: topLevelCo2,
    },
    co2_overview: topLevelCo2,
  };
}

afterEach(() => {
  clearAccessToken();
  mock.restoreAll();
  Reflect.deleteProperty(globalThis, "window");
});

test("getDashboardBootstrapSummary preserves co2_overview with full data inside loss_overview", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(summaryJsonWithCo2(co2OverviewJson())), { status: 200 });
  });

  const result = await getDashboardBootstrapSummary("DEV-001");

  assert.ok(result.loss_overview, "loss_overview must be present");
  assert.ok(result.loss_overview.co2_overview, "co2_overview must be preserved inside loss_overview");
  assert.equal(result.loss_overview.co2_overview.available, true);
  assert.equal(result.loss_overview.co2_overview.today?.co2_kg, 71.6);
  assert.equal(result.loss_overview.co2_overview.month?.co2_kg, 1718.4);
  assert.equal(result.loss_overview.co2_overview.today?.avoidable_co2_available, true);
  assert.equal(result.loss_overview.co2_overview.month?.avoidable_co2_available, false);
  assert.equal(result.loss_overview.co2_overview.factor?.value, 0.716);
  assert.equal(result.loss_overview.co2_overview.factor_source, "platform_default");
});

test("getDashboardBootstrapSummary handles co2_overview null in loss_overview", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(summaryJsonWithCo2(null)), { status: 200 });
  });

  const result = await getDashboardBootstrapSummary("DEV-001");

  assert.ok(result.loss_overview);
  assert.equal(result.loss_overview.co2_overview, null);
});

test("getDashboardBootstrapSummary handles loss_overview null entirely", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  const json = summaryJsonWithCo2(null);
  json.loss_overview = null;

  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(json), { status: 200 });
  });

  const result = await getDashboardBootstrapSummary("DEV-001");

  assert.equal(result.loss_overview, null);
});

test("getDashboardBootstrapSummary preserves unavailable co2_overview with reason", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(summaryJsonWithCo2({
      available: false,
      reason: "emission_factor_not_configured",
      factor_source: "unconfigured",
      calculation_version: "co2_scope2_v1",
    })), { status: 200 });
  });

  const result = await getDashboardBootstrapSummary("DEV-001");

  assert.ok(result.loss_overview?.co2_overview);
  assert.equal(result.loss_overview.co2_overview.available, false);
  assert.equal(result.loss_overview.co2_overview.reason, "emission_factor_not_configured");
});

test("getDashboardBootstrapSummary preserves avoidable_co2_available false with reason", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(summaryJsonWithCo2(co2OverviewJson({
      today: {
        energy_kwh: 80.0,
        co2_kg: 57.28,
        loss_kwh: 1.5,
        avoidable_co2_kg: null,
        available: true,
        avoidable_co2_available: false,
        avoidable_co2_reason: "loss_data_not_current_day",
      },
    }))), { status: 200 });
  });

  const result = await getDashboardBootstrapSummary("DEV-001");

  assert.ok(result.loss_overview?.co2_overview?.today);
  assert.equal(result.loss_overview.co2_overview.today.avoidable_co2_available, false);
  assert.equal(result.loss_overview.co2_overview.today.avoidable_co2_reason, "loss_data_not_current_day");
  assert.equal(result.loss_overview.co2_overview.today.avoidable_co2_kg, null);
  assert.equal(result.loss_overview.co2_overview.today.co2_kg, 57.28);
});

test("getDashboardBootstrapSummary preserves zero-energy co2_overview as available true", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(summaryJsonWithCo2(co2OverviewJson({
      today: {
        energy_kwh: 0,
        co2_kg: 0.0,
        loss_kwh: 0,
        avoidable_co2_kg: 0.0,
        available: true,
        avoidable_co2_available: true,
        avoidable_co2_reason: null,
      },
      month: {
        energy_kwh: 0,
        co2_kg: 0.0,
        available: true,
        avoidable_co2_available: false,
        avoidable_co2_reason: "monthly_loss_projection_not_supported",
      },
    }))), { status: 200 });
  });

  const result = await getDashboardBootstrapSummary("DEV-001");

  assert.ok(result.loss_overview?.co2_overview);
  assert.equal(result.loss_overview.co2_overview.available, true);
  assert.equal(result.loss_overview.co2_overview.today?.co2_kg, 0.0);
  assert.equal(result.loss_overview.co2_overview.today?.avoidable_co2_available, true);
});

test("getDashboardBootstrap preserves top-level co2_overview", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  const co2 = co2OverviewJson();
  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(bootstrapJsonWithCo2(co2)), { status: 200 });
  });

  const result = await getDashboardBootstrap("DEV-001");

  assert.ok(result.co2_overview, "top-level co2_overview must be preserved");
  assert.equal(result.co2_overview.available, true);
  assert.equal(result.co2_overview.today?.co2_kg, 71.6);
  assert.equal(result.co2_overview.factor?.value, 0.716);
});

test("getDashboardBootstrap preserves co2_overview inside loss_stats", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  const co2 = co2OverviewJson();
  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(bootstrapJsonWithCo2(co2)), { status: 200 });
  });

  const result = await getDashboardBootstrap("DEV-001");

  assert.ok(result.loss_stats, "loss_stats must be present");
  assert.ok(result.loss_stats.co2_overview, "co2_overview must be preserved inside loss_stats");
  assert.equal(result.loss_stats.co2_overview.available, true);
  assert.equal(result.loss_stats.co2_overview.today?.co2_kg, 71.6);
});

test("getDashboardBootstrap handles null co2_overview gracefully", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify(bootstrapJsonWithCo2(null)), { status: 200 });
  });

  const result = await getDashboardBootstrap("DEV-001");

  assert.equal(result.co2_overview, null);
  assert.ok(result.loss_stats);
  assert.equal(result.loss_stats.co2_overview, null);
});
