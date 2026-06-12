/* eslint-disable @typescript-eslint/no-require-imports */
const { expect, test } = require("@playwright/test");
const DASHBOARD_BOOTSTRAP_ENDPOINT = /\/backend\/device\/api\/v1\/devices\/DEVICE-RECOVERY\/dashboard-bootstrap$/;
const DETAIL_SNAPSHOT_ENDPOINT = "**/backend/device/api/v1/devices/DEVICE-RECOVERY/detail-snapshot**";

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function base64Json(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function buildAccessToken() {
  return `header.${base64Json({ role: "viewer", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 })}.signature`;
}

function buildSummaryPayload(overrides = {}) {
  return {
    success: true,
    generated_at: "2026-04-30T00:00:00Z",
    version: 11,
    device_id: "DEVICE-RECOVERY",
    device_name: "Recovery Compressor",
    device_type: "compressor",
    plant_id: "PLANT-1",
    location: "Plant 1",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-04-30T00:00:00Z",
    first_telemetry_timestamp: "2026-04-30T00:00:00Z",
    health_score: null,
    uptime_percentage: null,
    current_shift_uptime_percentage: null,
    daily_uptime_percentage: null,
    full_load_current_a: null,
    idle_threshold_pct_of_fla: null,
    derived_idle_threshold_a: null,
    derived_overconsumption_threshold_a: null,
    last_current_a: null,
    last_voltage_v: null,
    data_source_type: null,
    data_freshness_ts: "2026-04-30T00:00:00Z",
    live_updated_at: "2026-04-30T00:00:00Z",
    loss_overview: {
      day_bucket: "2026-04-30",
      updated_at: "2026-04-30T00:00:00Z",
      last_telemetry_ts: "2026-04-30T00:00:00Z",
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
    },
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: false,
      uptime_ready: false,
      loss_ready: true,
    },
    ...overrides,
  };
}

function buildBootstrapPayload(overrides = {}) {
  const base = {
    generated_at: "2026-04-30T00:00:00Z",
    version: 11,
    device: {
      device_id: "DEVICE-RECOVERY",
      tenant_id: "SH00000001",
      device_name: "Recovery Compressor",
      device_type: "compressor",
      status: "active",
      runtime_status: "running",
      location: "Plant 1",
      last_seen_timestamp: "2026-04-30T00:00:00Z",
    },
    telemetry: [{ timestamp: "2026-04-30T00:00:00Z", power: 140, current: 2.4 }],
    uptime: { shifts_configured: 1, uptime_percentage: 99, total_planned_minutes: 60, total_effective_minutes: 60, actual_running_minutes: 59, message: "OK" },
    shifts: [],
    health_configs: [],
    health_score: null,
    widget_config: { available_fields: ["power", "current"], selected_fields: [], effective_fields: ["power", "current"], default_applied: true },
    current_state: { device_id: "DEVICE-RECOVERY", state: "running", current_band: "in_load", current: 2.4, voltage: 230, threshold: 5, timestamp: "2026-04-30T00:00:00Z", current_field: "current", voltage_field: "voltage" },
    idle_stats: null,
    idle_config: null,
    waste_config: null,
    loss_stats: null,
  };
  return {
    ...base,
    ...overrides,
    device: {
      ...base.device,
      ...(overrides.device || {}),
    },
    current_state: {
      ...base.current_state,
      ...(overrides.current_state || {}),
    },
  };
}

function buildDetailSnapshotPayload(overrides = {}) {
  return {
    generated_at: "2026-04-30T00:00:00Z",
    device_id: "DEVICE-RECOVERY",
    data_freshness_ts: "2026-04-30T00:00:00Z",
    freshness_age_seconds: 4,
    availability: {
      snapshot_ready: true,
      health_score_ready: true,
      widget_config_ready: true,
      health_configs_ready: true,
      recent_telemetry_ready: true,
      stale: false,
    },
    snapshot: {
      sample_ts: "2026-04-30T00:00:00Z",
      projection_version: 11,
      snapshot_version: 1,
      runtime_status: "running",
      load_state: "running",
      current_band: "in_load",
      last_power_kw: 0.14,
      last_current_a: 2.4,
      last_voltage_v: 230,
      numeric_fields: {
        power: 140,
        current: 2.4,
        voltage: 230,
      },
      source_fields: {
        current_field: "current",
        voltage_field: "voltage",
        power_field: "power",
      },
      normalization_version: "v1",
      updated_at: "2026-04-30T00:00:00Z",
    },
    health_score: {
      device_id: "DEVICE-RECOVERY",
      health_score: 91,
      status: "Excellent",
      status_color: "🟢",
      message: "Healthy",
      machine_state: "RUNNING",
      parameter_scores: [],
      total_weight_configured: 100,
      parameters_included: 2,
      parameters_skipped: 0,
    },
    health_configs: [],
    widget_config: {
      device_id: "DEVICE-RECOVERY",
      available_fields: ["power", "current", "voltage"],
      selected_fields: [],
      effective_fields: ["power", "current", "voltage"],
      default_applied: true,
    },
    recent_telemetry: [
      {
        timestamp: "2026-04-30T00:00:00Z",
        device_id: "DEVICE-RECOVERY",
        power: 140,
        current: 2.4,
        voltage: 230,
      },
    ],
    ...overrides,
  };
}

async function expectStatusCardValue(page, value) {
  await expect(
    page.locator("div.rounded-xl").filter({ has: page.getByText("Status", { exact: true }) }).getByText(value, { exact: true }),
  ).toBeVisible({ timeout: 15_000 });
}

async function expectSummaryMetricCard(page, metricLabel, value) {
  const card = page.locator("div.rounded-xl").filter({ has: page.getByText(metricLabel, { exact: true }) });
  const valueLocator =
    value instanceof RegExp ? card.getByText(value) : card.getByText(value, { exact: true });
  await expect(valueLocator).toBeVisible({ timeout: 15_000 });
}

async function expectLossOverviewCard(page, metricLabel, value) {
  await expect(
    page.locator("div.rounded-xl").filter({ has: page.getByText(metricLabel, { exact: true }) }).getByText(value, { exact: true }),
  ).toBeVisible({ timeout: 15_000 });
}

async function gotoRecoveryMachine(page) {
  await page.goto("/machines/DEVICE-RECOVERY", {
    timeout: 60_000,
    waitUntil: "domcontentloaded",
  });
  await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
}

function buildContradictoryBootstrapPayload() {
  return {
    ...buildBootstrapPayload({
      device: {
        device_name: "Bootstrap Renamed Machine",
        device_type: "motor",
        runtime_status: "stopped",
        location: "Bootstrap Plant",
        last_seen_timestamp: "2026-03-01T00:00:00Z",
      },
      uptime: { shifts_configured: 1, uptime_percentage: 12, total_planned_minutes: 60, total_effective_minutes: 60, actual_running_minutes: 7, message: "Contradictory" },
      current_state: {
        state: "unknown",
        current_band: "unknown",
        timestamp: "2026-03-01T00:00:00Z",
      },
      loss_stats: {
        today: {
          idle_kwh: 9.9,
          idle_cost_inr: 99,
          off_hours_kwh: 8.8,
          off_hours_cost_inr: 88,
          overconsumption_kwh: 7.7,
          overconsumption_cost_inr: 77,
          total_loss_kwh: 26.4,
          total_loss_cost_inr: 264,
          today_energy_kwh: 40,
          today_energy_cost_inr: 400,
        },
        currency: "INR",
        tariff_configured: true,
        last_telemetry_ts: "2026-03-01T00:00:00Z",
      },
    }),
  };
}

async function installSession(page) {
  const me = {
    user: {
      id: "user-1",
      email: "ops@example.com",
      full_name: "Factory Ops",
      role: "viewer",
      tenant_id: "SH00000001",
      is_active: true,
      created_at: "2026-04-30T00:00:00Z",
      last_login_at: "2026-04-30T00:00:00Z",
    },
    org: {
      id: "SH00000001",
      name: "Tenant A",
      slug: "tenant-a",
      is_active: true,
      created_at: "2026-04-30T00:00:00Z",
    },
    plant_ids: [],
    entitlements: {
      premium_feature_grants: ["machine_health"],
      role_feature_matrix: { org_admin: ["machines"], plant_manager: [], operator: [], viewer: ["machines"], super_admin: ["machines"] },
      baseline_features_by_role: { org_admin: ["machines"], plant_manager: [], operator: [], viewer: ["machines"], super_admin: ["machines"] },
      effective_features_by_role: {
        org_admin: ["machines", "machine_health"],
        plant_manager: [],
        operator: [],
        viewer: ["machines", "machine_health"],
        super_admin: ["machines", "machine_health"],
      },
      available_features: ["machines", "machine_health"],
      entitlements_version: 1,
    },
  };

  await page.addInitScript(({ accessToken, meSnapshot }) => {
    window.sessionStorage.setItem("factoryops_access_token", accessToken);
    window.sessionStorage.setItem("factoryops_access_token_v2", accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_selected_tenant", "SH00000001");
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(meSnapshot));
  }, {
    accessToken: buildAccessToken(),
    meSnapshot: me,
  });

  await page.route("**/backend/auth/**", async (route) => {
    const requestUrl = new URL(route.request().url());
    const pathname = requestUrl.pathname;
    if (pathname.endsWith("/api/v1/auth/me")) {
      await json(route, me);
      return;
    }
    if (pathname.endsWith("/api/v1/auth/refresh")) {
      await json(route, {
        access_token: buildAccessToken(),
        token_type: "bearer",
        expires_in: 3600,
      });
      return;
    }
    if (pathname.endsWith("/api/v1/auth/logout")) {
      await json(route, { success: true });
      return;
    }
    await route.fulfill({ status: 404, body: "mocked auth route not handled in test" });
  });
  await page.route("**/backend/device/api/v1/devices/DEVICE-RECOVERY/dashboard-bootstrap/summary", async (route) => json(route, buildSummaryPayload()));
  await page.route(DETAIL_SNAPSHOT_ENDPOINT, async (route) => json(route, buildDetailSnapshotPayload()));
  await page.route("**/backend/device/api/v1/devices/DEVICE-RECOVERY/degradation-score**", async (route) =>
    json(route, {
      device_id: "DEVICE-RECOVERY",
      state: "scored",
      status: "healthy",
      score: 1.2,
      confidence: 0.95,
      updated_minutes_ago: 5,
      scored_at: "2026-04-30T00:00:00Z",
      factors: [],
      score_trend: [],
      stale: false,
    }),
  );
  await page.route("**/backend/device/api/v1/devices/DEVICE-RECOVERY/anomaly-activity**", async (route) =>
    json(route, {
      device_id: "DEVICE-RECOVERY",
      available: true,
      state: "available",
      monitored_signal_count: 5,
      today_count: 0,
      week_count: 0,
      month_count: 0,
      severity_counts: { mild: 0, strong: 0, severe: 0 },
      last_anomaly: null,
      trend: "stable",
      updated_minutes_ago: 5,
      stale: false,
    }),
  );
  await page.route("**/backend/device/api/v1/devices/DEVICE-RECOVERY/performance-trends**", async (route) => json(route, { points: [], summary: null }));
  await page.route("**/backend/device/api/v1/devices/DEVICE-RECOVERY/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/current-state")) {
      await json(route, buildBootstrapPayload().current_state);
      return;
    }
    if (pathname.endsWith("/idle-config")) {
      await json(route, {
        full_load_current_a: null,
        idle_threshold_pct_of_fla: null,
        derived_idle_threshold_a: null,
        derived_overconsumption_threshold_a: null,
      });
      return;
    }
    if (pathname.endsWith("/uptime")) {
      await json(route, buildBootstrapPayload().uptime);
      return;
    }
    if (pathname.endsWith("/shifts")) {
      await json(route, []);
      return;
    }
    if (pathname.endsWith("/health-configs")) {
      await json(route, []);
      return;
    }
    if (pathname.endsWith("/maintenance-log/summary")) {
      await json(route, {
        total_records: 0,
        open_records: 0,
        overdue_records: 0,
        next_due_at: null,
        last_completed_at: null,
      });
      return;
    }
    if (pathname.endsWith("/maintenance-log")) {
      await json(route, []);
      return;
    }
    await route.fallback();
  });
  await page.route("**/backend/data/api/v1/data/telemetry/DEVICE-RECOVERY/ws-ticket", async (route) =>
    json(route, { data: { ticket: "test-ticket", expires_in_seconds: 60 } }),
  );
  await page.route("**/backend/data/api/v1/data/telemetry/DEVICE-RECOVERY**", async (route) =>
    json(route, {
      data: {
        items: [
          { timestamp: "2026-04-30T00:00:00Z", device_id: "DEVICE-RECOVERY", power: 140, current: 2.4, voltage: 230 },
        ],
      },
    }),
  );
  await page.route("**/backend/rule-engine/api/v1/alerts/events?**", async (route) => json(route, { data: [], total: 0, page: 1, page_size: 25, total_pages: 0 }));
  await page.route("**/backend/rule-engine/api/v1/alerts/events/unread-count?**", async (route) => json(route, { data: { count: 0 } }));
}

test("machine detail page survives a slow successful bootstrap without a fatal timeout", async ({ page }) => {
  await installSession(page);

  let bootstrapCalls = 0;
  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) => {
    bootstrapCalls += 1;
    await new Promise((resolve) => setTimeout(resolve, 6_500));
    await json(route, buildBootstrapPayload());
  });

  await gotoRecoveryMachine(page);
  await expect(page.getByRole("heading", { name: "Recovery Compressor" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Error" })).toHaveCount(0);
  await expect.poll(() => bootstrapCalls >= 1).toBe(true);
});

test("machine detail page recovers after an initial detail-snapshot failure without surfacing a fatal shell error", async ({ page }) => {
  test.setTimeout(45_000);
  await installSession(page);

  let snapshotCalls = 0;
  await page.unroute(DETAIL_SNAPSHOT_ENDPOINT);
  await page.route(DETAIL_SNAPSHOT_ENDPOINT, async (route) => {
    snapshotCalls += 1;
    if (snapshotCalls === 1) {
      await route.fulfill({
        status: 503,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ detail: { message: "Projection detail snapshot temporarily unavailable" } }),
      });
      return;
    }

    await json(route, buildDetailSnapshotPayload());
  });
  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) => json(route, buildBootstrapPayload()));

  await gotoRecoveryMachine(page);
  await expect(page.getByRole("heading", { name: "Recovery Compressor" })).toBeVisible({ timeout: 25_000 });
  await expect.poll(() => snapshotCalls, { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
  const degradedHeading = page.getByRole("heading", { name: "Detailed KPIs unavailable" });
  const retryButton = page.getByRole("button", { name: "Retry KPIs" });
  await expect
    .poll(
      async () => {
        if (snapshotCalls >= 2) {
          return "recovered";
        }
        if (await retryButton.isVisible().catch(() => false)) {
          return "retry-visible";
        }
        if (await degradedHeading.isVisible().catch(() => false)) {
          return "degraded";
        }
        return "pending";
      },
      { timeout: 20_000 },
    )
    .not.toBe("pending");
  if (await retryButton.isVisible().catch(() => false)) {
    await retryButton.click().catch(() => null);
  }
  await expect.poll(() => snapshotCalls, { timeout: 20_000 }).toBeGreaterThanOrEqual(2);
  await expect(degradedHeading).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Error" })).toHaveCount(0);
});

test("machine detail page still surfaces a real unrecoverable bootstrap failure", async ({ page }) => {
  await installSession(page);

  await page.route("**/backend/device/api/v1/devices/DEVICE-RECOVERY/dashboard-bootstrap/summary", async (route) => {
    await route.fulfill({
      status: 404,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ detail: { error: { code: "DEVICE_NOT_FOUND", message: "Device not found" } } }),
    });
  });
  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) => {
    await route.fulfill({
      status: 404,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ detail: { error: { code: "DEVICE_NOT_FOUND", message: "Device not found" } } }),
    });
  });

  await gotoRecoveryMachine(page);
  await expect(page.getByRole("heading", { name: "Error" })).toBeVisible();
  await expect(page.getByText("HTTP 404")).toBeVisible();
});

test("machine detail page shows an honest degraded KPI state when deferred hydration fails", async ({ page }) => {
  await installSession(page);

  await page.unroute(DETAIL_SNAPSHOT_ENDPOINT);
  await page.route(DETAIL_SNAPSHOT_ENDPOINT, async (route) => route.abort("failed"));
  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) => json(route, buildBootstrapPayload()));

  await gotoRecoveryMachine(page);
  await expect(page.getByRole("heading", { name: "Recovery Compressor" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Detailed KPIs unavailable" })).toBeVisible();
  await expect(page.getByText("Retry KPIs")).toBeVisible();
  await expect(page.getByText("Waste & Loss Today")).toBeVisible();
  await expect(page.getByText("0.60 kWh")).toBeVisible();
  await expect(page.getByText("₹6.00")).toBeVisible();
  await expect(page.getByText("Waste and loss overview is not ready yet for this machine.")).toHaveCount(0);
  await expect(page.getByText("Detailed KPIs waiting for telemetry")).toHaveCount(0);
});

test("machine detail page renders overview KPI cards from detail snapshot even when bootstrap telemetry is empty", async ({ page }) => {
  await installSession(page);

  await page.unroute(DETAIL_SNAPSHOT_ENDPOINT);
  await page.route(DETAIL_SNAPSHOT_ENDPOINT, async (route) =>
    json(
      route,
      buildDetailSnapshotPayload({
        snapshot: {
          sample_ts: "2026-04-30T00:00:00Z",
          projection_version: 11,
          snapshot_version: 1,
          runtime_status: "running",
          load_state: "running",
          current_band: "in_load",
          last_power_kw: 0.14,
          last_current_a: 2.4,
          last_voltage_v: 230,
          numeric_fields: { power: 140, current: 2.4, voltage: 230 },
          source_fields: { current_field: "current", voltage_field: "voltage", power_field: "power" },
          normalization_version: "v1",
          updated_at: "2026-04-30T00:00:00Z",
        },
      }),
    ),
  );
  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) =>
    json(route, buildBootstrapPayload({ telemetry: [] })),
  );

  await gotoRecoveryMachine(page);
  await expect(page.getByRole("heading", { name: "Recovery Compressor" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Detailed KPIs waiting for telemetry" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Detailed KPIs unavailable" })).toHaveCount(0);
  await expect(page.getByText("140.00")).toBeVisible();
  await expect(page.getByText("2.40")).toBeVisible();
  await expect(page.getByText("230.00")).toBeVisible();
});

test("machine telemetry tab keeps recent seed visible when older history is degraded", async ({ page }) => {
  await installSession(page);

  await page.route("**/backend/data/api/v1/data/telemetry/DEVICE-RECOVERY**", async (route) => {
    await route.fulfill({
      status: 504,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        detail: {
          success: false,
          error: {
            code: "TELEMETRY_HISTORY_TIMEOUT",
            message: "Telemetry history is temporarily unavailable.",
            source: "influx",
            retryable: true,
            section: "history",
          },
          timestamp: "2026-04-30T00:00:00Z",
        },
      }),
    });
  });
  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) => json(route, buildBootstrapPayload({ telemetry: [] })));

  await gotoRecoveryMachine(page);
  await page.getByRole("button", { name: "Telemetry" }).click();
  const recentTelemetryCard = page
    .getByRole("heading", { name: "Recent Telemetry" })
    .locator("xpath=ancestor::div[contains(@class,'surface-panel')][1]");
  await expect(page.getByRole("heading", { name: "Recent Telemetry" })).toBeVisible();
  await expect(recentTelemetryCard.getByText("140.00", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Load Older History" }).click();
  await expect(page.getByText("Recent telemetry is available, but older history is temporarily unavailable.")).toBeVisible();
});

test("stopped-device telemetry tab can load truthful older history when no recent seed is available", async ({ page }) => {
  await installSession(page);

  await page.unroute("**/backend/device/api/v1/devices/DEVICE-RECOVERY/dashboard-bootstrap/summary");
  await page.route("**/backend/device/api/v1/devices/DEVICE-RECOVERY/dashboard-bootstrap/summary", async (route) =>
    json(route, buildSummaryPayload({
      runtime_status: "stopped",
      load_state: "unknown",
      current_band: "unknown",
      operational_status: "stopped",
      last_seen_timestamp: "2026-04-29T00:00:00Z",
      live_updated_at: "2026-04-29T00:00:00Z",
    })),
  );
  await page.unroute(DETAIL_SNAPSHOT_ENDPOINT);
  await page.route(DETAIL_SNAPSHOT_ENDPOINT, async (route) =>
    json(route, buildDetailSnapshotPayload({
      availability: {
        snapshot_ready: true,
        health_score_ready: true,
        widget_config_ready: true,
        health_configs_ready: true,
        recent_telemetry_ready: false,
        stale: true,
      },
      snapshot: {
        ...buildDetailSnapshotPayload().snapshot,
        runtime_status: "stopped",
        load_state: "unknown",
        current_band: "unknown",
        sample_ts: "2026-04-29T00:00:00Z",
      },
      recent_telemetry: [],
    })),
  );
  await page.route("**/backend/data/api/v1/data/telemetry/DEVICE-RECOVERY**", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        success: true,
        data: {
          device_id: "DEVICE-RECOVERY",
          items: [
            {
              timestamp: "2026-04-28T23:55:00Z",
              device_id: "DEVICE-RECOVERY",
              power: 118,
              current: 2.1,
              voltage: 229,
            },
          ],
        },
      }),
    });
  });
  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) => json(route, buildBootstrapPayload({ telemetry: [] })));

  await gotoRecoveryMachine(page);
  await page.getByRole("button", { name: "Telemetry" }).click();
  await expect(page.getByText("No recent telemetry seed yet. Load older history below to check earlier samples.")).toBeVisible();
  await page.getByRole("button", { name: "Load Older History" }).click();
  const olderTelemetryCard = page
    .getByRole("heading", { name: "Older Telemetry History" })
    .locator("xpath=ancestor::div[contains(@class,'surface-panel')][1]");
  await expect(page.getByRole("heading", { name: "Older Telemetry History" })).toBeVisible();
  await expect(olderTelemetryCard.getByText("118.00", { exact: true })).toBeVisible();
  await expect(olderTelemetryCard.getByText("2.10", { exact: true })).toBeVisible();
  await expect(page.getByText("Telemetry history is temporarily unavailable.")).toHaveCount(0);
  await expect(page.getByText("No telemetry received yet for this machine.")).toHaveCount(0);
});

test("machine detail shell keeps fast summary ownership when delayed bootstrap returns contradictory state", async ({ page }) => {
  await installSession(page);

  await page.route("**/backend/device/api/v1/devices/DEVICE-RECOVERY/dashboard-bootstrap/summary", async (route) =>
    json(route, buildSummaryPayload({
      health_score: 92,
      uptime_percentage: 88,
      current_shift_uptime_percentage: 88,
      daily_uptime_percentage: 88,
      runtime_status: "running",
      load_state: "running",
      current_band: "in_load",
      operational_status: "running",
    })),
  );

  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await json(route, buildContradictoryBootstrapPayload());
  });

  await gotoRecoveryMachine(page);
  await expect(page.getByRole("heading", { name: "Recovery Compressor" })).toBeVisible({ timeout: 15_000 });
  await expectStatusCardValue(page, "In Load");
  await expectSummaryMetricCard(page, "Health Score", "92%");
  await expectSummaryMetricCard(page, "Uptime", /^88(?:\.0)?%$/);
  await expectLossOverviewCard(page, "Total Loss", "0.60 kWh");
  await expect(page.getByRole("heading", { name: "Bootstrap Renamed Machine" })).toHaveCount(0);
  await expect(page.getByText("Bootstrap Plant")).toHaveCount(0);
  await expect(page.getByText("Stopped", { exact: true })).toHaveCount(0);
  await expect(page.getByText("26.40 kWh")).toHaveCount(0);
});
