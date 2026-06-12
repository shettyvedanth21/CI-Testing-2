/* eslint-disable @typescript-eslint/no-require-imports */
const { expect, test } = require("@playwright/test");
const DASHBOARD_BOOTSTRAP_ENDPOINT = /\/backend\/device\/api\/v1\/devices\/DEVICE-ACTIVITY\/dashboard-bootstrap$/;
const DETAIL_SNAPSHOT_ENDPOINT = "**/backend/device/api/v1/devices/DEVICE-ACTIVITY/detail-snapshot**";

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

function buildBootstrapPayload() {
  return {
    generated_at: "2026-04-30T00:00:00Z",
    version: 11,
    device: {
      device_id: "DEVICE-ACTIVITY",
      tenant_id: "SH00000001",
      device_name: "Activity Compressor",
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
    current_state: { device_id: "DEVICE-ACTIVITY", state: "running", current_band: "in_load", current: 2.4, voltage: 230, threshold: 5, timestamp: "2026-04-30T00:00:00Z", current_field: "current", voltage_field: "voltage" },
    idle_stats: null,
    idle_config: null,
    waste_config: null,
    loss_stats: null,
  };
}

function buildSummaryPayload() {
  return {
    success: true,
    generated_at: "2026-04-30T00:00:00Z",
    version: 11,
    device_id: "DEVICE-ACTIVITY",
    device_name: "Activity Compressor",
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
      idle_kwh: 0.1,
      idle_cost_inr: 1,
      off_hours_kwh: 0,
      off_hours_cost_inr: 0,
      overconsumption_kwh: 0,
      overconsumption_cost_inr: 0,
      total_loss_kwh: 0.1,
      total_loss_cost_inr: 1,
      today_energy_kwh: 3.1,
    },
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: false,
      uptime_ready: false,
      loss_ready: true,
    },
  };
}

function buildDetailSnapshotPayload() {
  return {
    generated_at: "2026-04-30T00:00:00Z",
    device_id: "DEVICE-ACTIVITY",
    data_freshness_ts: "2026-04-30T00:00:00Z",
    freshness_age_seconds: 2,
    availability: {
      snapshot_ready: true,
      health_score_ready: false,
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
    health_score: null,
    health_configs: [],
    widget_config: {
      device_id: "DEVICE-ACTIVITY",
      available_fields: ["power", "current"],
      selected_fields: [],
      effective_fields: ["power", "current"],
      default_applied: true,
    },
    recent_telemetry: [
      {
        timestamp: "2026-04-30T00:00:00Z",
        device_id: "DEVICE-ACTIVITY",
        power: 140,
        current: 2.4,
        voltage: 230,
      },
    ],
  };
}

async function installSession(page) {
  const accessToken = buildAccessToken();
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
    tenant: {
      id: "SH00000001",
      name: "Tenant A",
      slug: "tenant-a",
      is_active: true,
      created_at: "2026-04-30T00:00:00Z",
    },
    plant_ids: [],
    entitlements: {
      premium_feature_grants: [],
      role_feature_matrix: { org_admin: ["machines"], plant_manager: [], operator: [], viewer: ["machines"], super_admin: ["machines"] },
      baseline_features_by_role: { org_admin: ["machines"], plant_manager: [], operator: [], viewer: ["machines"], super_admin: ["machines"] },
      effective_features_by_role: { org_admin: ["machines"], plant_manager: [], operator: [], viewer: ["machines"], super_admin: ["machines"] },
      available_features: ["machines"],
      entitlements_version: 1,
    },
  };

  await page.addInitScript(({ token, snapshot }) => {
    window.sessionStorage.setItem("factoryops_access_token", token);
    window.sessionStorage.setItem("factoryops_access_token_v2", token);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_selected_tenant", "SH00000001");
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot));
  }, { token: accessToken, snapshot: me });

  await page.route("**/backend/auth/**", async (route) => {
    const requestUrl = new URL(route.request().url());
    const pathname = requestUrl.pathname;
    if (pathname.endsWith("/api/v1/auth/login")) {
      await json(route, {
        access_token: accessToken,
        refresh_token: "refresh-token",
        token_type: "bearer",
        expires_in: 3600,
      });
      return;
    }
    if (pathname.endsWith("/api/v1/auth/me")) {
      await json(route, me);
      return;
    }
    if (pathname.endsWith("/api/v1/auth/refresh")) {
      await json(route, {
        access_token: accessToken,
        refresh_token: "refresh-token",
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
  await page.route("**/backend/auth/api/v1/platform-maintenance/current", async (route) => {
    await json(route, { tenant_id: "SH00000001", announcements: [] });
  });
  await page.route("**/backend/auth/api/v1/tenants/SH00000001/plants", async (route) => {
    await json(route, []);
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/summary", async (route) => {
    await json(route, {
      generated_at: "2026-04-30T00:00:00Z",
      stale: false,
      warnings: [],
      summary: {
        total_devices: 0,
        running_devices: 0,
        stopped_devices: 0,
        devices_with_health_data: 0,
        devices_with_uptime_configured: 0,
        devices_missing_uptime_config: 0,
        system_health: null,
        average_efficiency: null,
      },
      alerts: {
        active_alerts: 0,
      },
      devices: [],
      cost_data_state: "fresh",
      cost_data_reasons: [],
      cost_generated_at: null,
      energy_widgets: {
        today_loss_kwh: 0,
        today_loss_cost_inr: 0,
        currency: "INR",
      },
    });
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/fleet-snapshot**", async (route) => {
    await json(route, {
      generated_at: "2026-04-30T00:00:00Z",
      total: 0,
      page: 1,
      page_size: 60,
      total_pages: 1,
      devices: [],
    });
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/fleet-stream**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body:
        "id: bootstrap\n" +
        "event: heartbeat\n" +
        'data: {"id":"bootstrap","event":"heartbeat","generated_at":"2026-04-30T00:00:00Z","freshness_ts":"2026-04-30T00:00:00Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":0}\n\n',
    });
  });
  await page.route("**/backend/rule-engine/api/v1/alerts/events/unread-count?**", async (route) => {
    await json(route, { data: { count: 0 } });
  });
  await page.route("**/backend/rule-engine/api/v1/alerts/events?**", async (route) => {
    await json(route, {
      data: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
  });
  await page.route("**/backend/device/api/v1/devices/DEVICE-ACTIVITY/dashboard-bootstrap/summary", async (route) => json(route, buildSummaryPayload()));
  await page.route(DETAIL_SNAPSHOT_ENDPOINT, async (route) => json(route, buildDetailSnapshotPayload()));
  await page.route(DASHBOARD_BOOTSTRAP_ENDPOINT, async (route) => json(route, buildBootstrapPayload()));
  await page.route("**/backend/device/api/v1/devices/DEVICE-ACTIVITY/performance-trends**", async (route) => json(route, { points: [], summary: null }));
}

test("transient activity-history fetch misses do not strand the machine page and recover on retry", async ({ page }) => {
  await installSession(page);

  const consoleMessages = [];
  page.on("console", (msg) => consoleMessages.push(msg.text()));

  let eventCalls = 0;
  let unreadCalls = 0;
  await page.route("**/backend/rule-engine/api/v1/alerts/events?**", async (route) => {
    eventCalls += 1;
    if (eventCalls === 1) {
      await route.abort("failed");
      return;
    }
    await json(route, {
      data: [
        {
          event_id: "evt-1",
          tenant_id: "SH00000001",
          device_id: "DEVICE-ACTIVITY",
          rule_id: "rule-1",
          alert_id: "alert-1",
          event_type: "alert_triggered",
          title: "High current spike",
          message: "Current exceeded threshold.",
          metadata_json: {},
          is_read: false,
          read_at: null,
          created_at: "2026-04-30T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
  });
  await page.route("**/backend/rule-engine/api/v1/alerts/events/unread-count?**", async (route) => {
    unreadCalls += 1;
    await json(route, { data: { count: unreadCalls > 1 ? 1 : 0 } });
  });

  await page.goto("/machines/DEVICE-ACTIVITY");
  await expect(page.getByRole("heading", { name: "Activity Compressor" })).toBeVisible();

  await page.getByTitle("Machine alert history").click();
  await expect(page.getByText("Activity history is temporarily unavailable. The rest of the machine page is still live.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Activity Compressor" })).toBeVisible();
  await expect.poll(() => eventCalls, { timeout: 10_000 }).toBe(1);
  await expect(page.getByText("Loading activity history...")).toHaveCount(0);
  expect(consoleMessages.some((message) => message.includes("Failed to load activity history:"))).toBeFalsy();
});

test("real activity-history backend failures surface a truthful degraded message without breaking the page", async ({ page }) => {
  await installSession(page);

  const consoleMessages = [];
  page.on("console", (msg) => consoleMessages.push(msg.text()));

  await page.route("**/backend/rule-engine/api/v1/alerts/events?**", async (route) => {
    await route.fulfill({
      status: 503,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ detail: { message: "Activity history backend unavailable" } }),
    });
  });
  await page.route("**/backend/rule-engine/api/v1/alerts/events/unread-count?**", async (route) => {
    await json(route, { data: { count: 0 } });
  });

  await page.goto("/machines/DEVICE-ACTIVITY");
  await expect(page.getByRole("heading", { name: "Activity Compressor" })).toBeVisible();

  await page.getByTitle("Machine alert history").click();
  await expect(page.getByText("Activity history backend unavailable")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Activity Compressor" })).toBeVisible();
  await expect.poll(() => consoleMessages.some((message) => message.includes("Failed to load activity history:")), { timeout: 5_000 }).toBeTruthy();
});
