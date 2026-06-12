/* eslint-disable @typescript-eslint/no-require-imports */
const { expect, test } = require("@playwright/test");

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function buildTelemetryRow(index) {
  const base = new Date("2026-04-10T06:00:00Z").getTime();
  return {
    timestamp: new Date(base - index * 60_000).toISOString(),
    device_id: "DEVICE-1",
    power: 500 - index,
    current: 20 - index * 0.1,
  };
}

function buildSummaryPayload() {
  return {
    success: true,
    generated_at: "2026-04-10T06:00:00Z",
    version: 7,
    device_id: "DEVICE-1",
    device_name: "Compressor 1",
    device_type: "compressor",
    plant_id: "PLANT-1",
    location: "Plant 1",
    runtime_status: "running",
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    last_seen_timestamp: "2026-04-10T06:00:00Z",
    first_telemetry_timestamp: "2026-04-10T06:00:00Z",
    health_score: null,
    uptime_percentage: null,
    full_load_current_a: null,
    idle_threshold_pct_of_fla: null,
    derived_idle_threshold_a: null,
    derived_overconsumption_threshold_a: null,
    last_current_a: 20,
    last_voltage_v: 230,
    data_source_type: "metered",
    data_freshness_ts: "2026-04-10T06:00:00Z",
    live_updated_at: "2026-04-10T06:00:00Z",
    loss_overview: null,
    overview_readiness: {
      summary_ready: true,
      telemetry_ready: true,
      health_ready: false,
      uptime_ready: false,
      loss_ready: false,
    },
  };
}

function buildDetailSnapshotPayload() {
  return {
    generated_at: "2026-04-10T06:00:00Z",
    device_id: "DEVICE-1",
    data_freshness_ts: "2026-04-10T06:00:00Z",
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
      sample_ts: "2026-04-10T06:00:00Z",
      projection_version: 7,
      snapshot_version: 1,
      runtime_status: "running",
      load_state: "running",
      current_band: "in_load",
      last_power_kw: 0.5,
      last_current_a: 20,
      last_voltage_v: 230,
      numeric_fields: { power: 500, current: 20, voltage: 230 },
      source_fields: { current_field: "current", voltage_field: "voltage", power_field: "power" },
      normalization_version: "v1",
      updated_at: "2026-04-10T06:00:00Z",
    },
    health_score: null,
    health_configs: [],
    widget_config: {
      device_id: "DEVICE-1",
      available_fields: ["power", "current"],
      selected_fields: [],
      effective_fields: ["power", "current"],
      default_applied: true,
    },
    recent_telemetry: Array.from({ length: 100 }, (_, index) => buildTelemetryRow(index)),
  };
}

test("recent telemetry table paginates buffered live rows while keeping older history on demand", async ({ page }) => {
  const payload = Buffer.from(JSON.stringify({ role: "org_admin", tenant_id: "SH00000001" })).toString("base64url");
  const token = `header.${payload}.signature`;
  const me = {
    user: {
      id: "user-1",
      email: "ops@example.com",
      full_name: "Factory Ops",
      role: "org_admin",
      tenant_id: "SH00000001",
      is_active: true,
      created_at: "2026-04-10T00:00:00Z",
      last_login_at: "2026-04-10T00:00:00Z",
    },
    tenant: {
      id: "SH00000001",
      name: "Tenant A",
      slug: "sh-00000001",
      is_active: true,
      created_at: "2026-04-10T00:00:00Z",
    },
    plant_ids: [],
    entitlements: {
      premium_feature_grants: [],
      role_feature_matrix: {
        org_admin: ["machines"],
        plant_manager: [],
        operator: [],
        viewer: [],
        super_admin: ["machines"],
      },
      baseline_features_by_role: {
        org_admin: ["machines"],
        plant_manager: [],
        operator: [],
        viewer: [],
        super_admin: ["machines"],
      },
      effective_features_by_role: {
        org_admin: ["machines"],
        plant_manager: [],
        operator: [],
        viewer: [],
        super_admin: ["machines"],
      },
      available_features: ["machines"],
      entitlements_version: 1,
    },
  };

  await page.addInitScript(({ accessToken }) => {
    const me = JSON.parse(window.atob(accessToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    const snapshot = {
      user: {
        id: "user-1",
        email: "ops@example.com",
        full_name: "Factory Ops",
        role: me.role,
        tenant_id: me.tenant_id,
        is_active: true,
        created_at: "2026-04-10T00:00:00Z",
        last_login_at: "2026-04-10T00:00:00Z",
      },
      tenant: {
        id: me.tenant_id,
        name: "Tenant A",
        slug: "sh-00000001",
        is_active: true,
        created_at: "2026-04-10T00:00:00Z",
      },
      plant_ids: [],
      entitlements: {
        premium_feature_grants: [],
        role_feature_matrix: {
          org_admin: ["machines"],
          plant_manager: [],
          operator: [],
          viewer: [],
          super_admin: ["machines"],
        },
        baseline_features_by_role: {
          org_admin: ["machines"],
          plant_manager: [],
          operator: [],
          viewer: [],
          super_admin: ["machines"],
        },
        effective_features_by_role: {
          org_admin: ["machines"],
          plant_manager: [],
          operator: [],
          viewer: [],
          super_admin: ["machines"],
        },
        available_features: ["machines"],
        entitlements_version: 1,
      },
    };
    window.sessionStorage.setItem("factoryops_access_token", accessToken);
    window.sessionStorage.setItem("factoryops_access_token_v2", accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_selected_tenant", "SH00000001");
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot));
  }, { accessToken: token });

  await page.route("**/backend/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path.endsWith("/api/v1/auth/me")) {
      return json(route, me);
    }

    if (path.endsWith("/api/v1/auth/refresh")) {
      return json(route, {
        access_token: token,
        token_type: "bearer",
        expires_in: 3600,
      });
    }

    if (path.endsWith("/api/v1/platform-maintenance/current")) {
      return json(route, {
        tenant_id: "SH00000001",
        announcements: [],
      });
    }

    if (path.endsWith("/api/v1/devices/DEVICE-1/dashboard-bootstrap/summary")) {
      return json(route, buildSummaryPayload());
    }

    if (path.endsWith("/api/v1/devices/DEVICE-1/detail-snapshot")) {
      return json(route, buildDetailSnapshotPayload());
    }

    if (path.endsWith("/api/v1/devices/DEVICE-1/dashboard-bootstrap")) {
      return json(route, {
        generated_at: "2026-04-10T06:00:00Z",
        version: 7,
        device: {
          device_id: "DEVICE-1",
          device_name: "Compressor 1",
          device_type: "compressor",
          status: "online",
          runtime_status: "running",
          location: "Plant 1",
          last_seen_timestamp: "2026-04-10T06:00:00Z",
        },
        telemetry: Array.from({ length: 100 }, (_, index) => buildTelemetryRow(index)),
        uptime: { uptime_percentage: 98, shifts_configured: 1, total_planned_minutes: 60, total_effective_minutes: 60, actual_running_minutes: 59 },
        shifts: [],
        health_configs: [],
        health_score: null,
        widget_config: { available_fields: ["power", "current"], selected_fields: [], effective_fields: ["power", "current"], default_applied: true },
        current_state: { state: "running", current: 12.7, voltage: 230, threshold: 5, timestamp: "2026-04-10T06:00:00Z", current_field: "current", voltage_field: "voltage", device_id: "DEVICE-1" },
        idle_stats: null,
        idle_config: null,
        waste_config: null,
        loss_stats: null,
      });
    }

    if (path.endsWith("/api/v1/devices/DEVICE-1/performance-trends")) {
      return json(route, { points: [], message: "No trend data" });
    }

    if (path.endsWith("/api/v1/alerts/events")) {
      return json(route, { data: [], total: 0, page: 1, page_size: 25, total_pages: 0 });
    }

    if (path.endsWith("/api/v1/alerts/events/unread-count")) {
      return json(route, { data: { count: 0 } });
    }

    return route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/machines/DEVICE-1");
  await page.getByRole("button", { name: "Telemetry" }).click();

  await expect(page.getByRole("heading", { name: "Recent Telemetry" })).toBeVisible();
  await expect(page.getByText(/100 buffered rows/)).toBeVisible();
  await expect(page.getByText(/Page 1 of 10/)).toBeVisible();
  await expect(page.getByText(/Showing rows 1-10 of 100/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Older Telemetry History" })).toBeVisible();
  await expect(page.getByText("Recent telemetry loads from the fast projection lane first. Older history is fetched on demand.")).toBeVisible();
  await expect(page.getByText("Recent telemetry is shown above. Load older history when you need deeper rows.")).toBeVisible();

  await expect(page.getByRole("cell", { name: "500.00" })).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText(/Page 2 of 10/)).toBeVisible();
  await expect(page.getByText(/Showing rows 11-20 of 100/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "490.00" })).toBeVisible();

  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(page.getByText(/Page 1 of 10/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "500.00" })).toBeVisible();
});
