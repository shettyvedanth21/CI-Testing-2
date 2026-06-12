/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");

function base64Json(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

async function fulfillJson(route, data, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

function buildSuperAdminMe() {
  return {
    user: {
      id: "super-1",
      email: "super@example.com",
      full_name: "Super Admin",
      role: "super_admin",
      tenant_id: null,
      is_active: true,
      created_at: new Date().toISOString(),
      last_login_at: null,
    },
    tenant: null,
    plant_ids: [],
    entitlements: {
      premium_feature_grants: [],
      role_feature_matrix: {
        super_admin: ["machines", "calendar", "rules", "settings"],
        org_admin: ["machines", "calendar", "rules", "settings"],
        plant_manager: ["machines", "rules", "settings"],
        operator: ["machines", "rules"],
        viewer: ["machines"],
      },
      baseline_features_by_role: {
        org_admin: ["machines", "calendar", "rules", "settings"],
        plant_manager: ["machines", "rules", "settings"],
        operator: ["machines", "rules"],
        viewer: ["machines"],
      },
      effective_features_by_role: {
        super_admin: ["machines", "calendar", "rules", "settings"],
        org_admin: ["machines", "calendar", "rules", "settings"],
        plant_manager: ["machines", "rules", "settings"],
        operator: ["machines", "rules"],
        viewer: ["machines"],
      },
      available_features: ["machines", "calendar", "rules", "settings"],
      entitlements_version: 1,
    },
  };
}

function buildTenantMe() {
  return {
    user: {
      id: "org-admin-1",
      email: "org-admin@example.com",
      full_name: "Org Admin",
      role: "org_admin",
      tenant_id: "SH00000001",
      is_active: true,
      created_at: new Date().toISOString(),
      last_login_at: null,
    },
    tenant: {
      id: "SH00000001",
      name: "Factory Ops",
      slug: "factory-ops",
      is_active: true,
      created_at: new Date().toISOString(),
    },
    plant_ids: ["plant-1"],
    entitlements: {
      premium_feature_grants: [],
      role_feature_matrix: {
        org_admin: ["machines", "calendar", "rules", "settings"],
        plant_manager: ["machines", "rules", "settings"],
        operator: ["machines", "rules"],
        viewer: ["machines"],
      },
      baseline_features_by_role: {
        org_admin: ["machines", "calendar", "rules", "settings"],
        plant_manager: ["machines", "rules", "settings"],
        operator: ["machines", "rules"],
        viewer: ["machines"],
      },
      effective_features_by_role: {
        org_admin: ["machines", "calendar", "rules", "settings"],
        plant_manager: ["machines", "rules", "settings"],
        operator: ["machines", "rules"],
        viewer: ["machines"],
      },
      available_features: ["machines", "calendar", "rules", "settings"],
      entitlements_version: 1,
    },
  };
}

async function seedSession(page, me, accessTokenPayload, selectedTenant = null) {
  const accessToken = `header.${base64Json(accessTokenPayload)}.signature`;
  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem("factoryops_access_token", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_access_token_v2", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    if (snapshot.selectedTenant) {
      window.sessionStorage.setItem("factoryops_selected_tenant", snapshot.selectedTenant);
    }
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot.me));
  }, {
    accessToken,
    me,
    selectedTenant,
  });

  await page.route("**/backend/auth/api/v1/auth/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/api/v1/auth/me")) {
      await fulfillJson(route, me);
      return;
    }
    if (pathname.endsWith("/api/v1/auth/refresh")) {
      await fulfillJson(route, {
        access_token: accessToken,
        refresh_token: "refresh-token",
        token_type: "bearer",
        expires_in: 3600,
      });
      return;
    }
    await fulfillJson(route, {}, 404);
  });
  await page.route("**/backend/auth/api/v1/platform-maintenance/current", async (route) => {
    await fulfillJson(route, {
      tenant_id: selectedTenant,
      announcements: [],
    });
  });
}

test("admin platform-maintenance UI truthfully handles selected org targeting and suspended-org exclusion", async ({ page }) => {
  const me = buildSuperAdminMe();
  const tenants = [
    { id: "SH00000001", name: "Factory Ops", slug: "factory-ops", is_active: true, created_at: "2026-04-01T00:00:00Z" },
    { id: "SH00000002", name: "Suspended Org", slug: "suspended-org", is_active: false, created_at: "2026-04-01T00:00:00Z" },
  ];
  let createPayload = null;

  await seedSession(
    page,
    me,
    { sub: "super-1", role: "super_admin", permissions_version: 1, tenant_entitlements_version: 1, exp: Math.floor(Date.now() / 1000) + 3600 },
    null,
  );

  await page.route("**/backend/auth/api/admin/platform-maintenance", async (route) => {
    if (route.request().method() === "GET") {
      await fulfillJson(route, []);
      return;
    }
    createPayload = route.request().postDataJSON();
    await fulfillJson(route, {
      id: "pm-created",
      title: createPayload.title,
      severity: createPayload.severity,
      message: createPayload.message,
      starts_at: createPayload.starts_at,
      estimated_duration_minutes: createPayload.estimated_duration_minutes,
      ends_at: "2026-05-01T13:00:00Z",
      status: "scheduled",
      effective_status: "scheduled",
      broadcast_all_tenants: createPayload.broadcast_all_tenants,
      target_tenant_ids: createPayload.target_tenant_ids,
      created_by: "super-1",
      updated_by: "super-1",
      created_at: "2026-04-01T00:00:00Z",
      updated_at: "2026-04-01T00:00:00Z",
    }, 201);
  });
  await page.route("**/backend/auth/api/admin/tenants", async (route) => {
    await fulfillJson(route, tenants);
  });

  await page.goto("/admin/platform-maintenance");

  await expect(page.getByText("No maintenance notices yet. Start a draft to plan your first platform announcement.")).toBeVisible();

  await page.getByLabel("Notice title").fill("Scheduled database maintenance");
  await page.getByLabel("Start time").fill("2026-05-01T18:00");
  await page.getByLabel("Expected duration (minutes)").fill("60");
  await page.locator("#maintenance-message").fill("Maintenance message for selected organisations.");
  await page.getByRole("button", { name: "Selected organisations" }).click();
  await expect(page.getByText("Suspended organisations are shown for visibility, but they cannot be selected for new delivery.")).toBeVisible();
  const factoryOpsRow = page.locator("label").filter({ hasText: "Factory Ops" }).first();
  const suspendedRow = page.locator("label").filter({ hasText: "Suspended Org" }).first();
  await expect(factoryOpsRow).toBeVisible();
  await expect(suspendedRow).toBeVisible();
  await expect(page.getByText("Suspended").first()).toBeVisible();
  await expect(suspendedRow.locator('input[type="checkbox"]')).toBeDisabled();
  await factoryOpsRow.locator('input[type="checkbox"]').check();
  await page.getByRole("button", { name: "Schedule Notice" }).click();

  await expect(page.getByText("Maintenance notice scheduled.")).toBeVisible();
  await expect(page.getByText("1 organisation selected: Factory Ops.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Scheduled database maintenance/i })).toContainText("Scheduled");

  expect(createPayload).not.toBeNull();
  expect(createPayload.broadcast_all_tenants).toBe(false);
  expect(createPayload.target_tenant_ids).toEqual(["SH00000001"]);
});

test("tenant banner UI truthfully shows active and scheduled notices for the current organisation", async ({ page }) => {
  const me = buildTenantMe();

  await seedSession(
    page,
    me,
    { sub: "org-admin-1", role: "org_admin", tenant_id: "SH00000001", plant_ids: ["plant-1"], permissions_version: 1, tenant_entitlements_version: 1, exp: Math.floor(Date.now() / 1000) + 3600 },
    "SH00000001",
  );

  await page.route("**/backend/auth/api/v1/platform-maintenance/current", async (route) => {
    await fulfillJson(route, {
      tenant_id: "SH00000001",
      announcements: [
        {
          id: "pm-active",
          title: "Database maintenance in progress",
          severity: "critical",
          message: "Writes may be delayed while maintenance is active.",
          starts_at: "2026-05-01T12:00:00Z",
          estimated_duration_minutes: 90,
          ends_at: "2026-05-01T13:30:00Z",
          status: "scheduled",
          effective_status: "active",
          broadcast_all_tenants: true,
          target_tenant_ids: [],
          created_by: "super-1",
          updated_by: "super-1",
          created_at: "2026-04-01T00:00:00Z",
          updated_at: "2026-04-01T00:00:00Z",
        },
        {
          id: "pm-scheduled",
          title: "Scheduled read replica maintenance",
          severity: "warning",
          message: "Dashboards may refresh more slowly during the window.",
          starts_at: "2026-05-01T15:00:00Z",
          estimated_duration_minutes: 45,
          ends_at: "2026-05-01T15:45:00Z",
          status: "scheduled",
          effective_status: "scheduled",
          broadcast_all_tenants: false,
          target_tenant_ids: ["SH00000001"],
          created_by: "super-1",
          updated_by: "super-1",
          created_at: "2026-04-01T00:00:00Z",
          updated_at: "2026-04-01T00:00:00Z",
        },
      ],
    });
  });
  await page.route("**/backend/auth/api/v1/tenants/SH00000001/plants", async (route) => {
    await fulfillJson(route, [
      {
        id: "plant-1",
        tenant_id: "SH00000001",
        name: "Plant One",
        location: "Pune",
        timezone: "Asia/Kolkata",
        is_active: true,
        created_at: "2026-05-01T00:00:00Z",
      },
    ]);
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/summary", async (route) => {
    await fulfillJson(route, {
      generated_at: "2026-05-01T12:05:00Z",
      stale: false,
      warnings: [],
      summary: {
        total_devices: 1,
        running_devices: 1,
        stopped_devices: 0,
        devices_with_health_data: 1,
        devices_with_uptime_configured: 1,
        devices_missing_uptime_config: 0,
        system_health: 95,
        average_efficiency: null,
      },
      alerts: { active_alerts: 0 },
      devices: [],
      cost_data_state: "fresh",
      cost_data_reasons: [],
      cost_generated_at: null,
      energy_widgets: {
        today_loss_kwh: 0.1,
        today_loss_cost_inr: 1,
        currency: "INR",
      },
    });
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/fleet-snapshot**", async (route) => {
    await fulfillJson(route, {
      generated_at: "2026-05-01T12:05:00Z",
      total: 1,
      page: 1,
      page_size: 60,
      total_pages: 1,
      devices: [
        {
          device_id: "DEVICE-1",
          device_name: "Compressor 1",
          device_type: "compressor",
          plant_id: "plant-1",
          runtime_status: "running",
          load_state: "running",
          current_band: "in_load",
          operational_status: "running",
          location: "Plant One",
          first_telemetry_timestamp: "2026-05-01T12:00:00Z",
          last_seen_timestamp: "2026-05-01T12:05:00Z",
          health_score: 95,
          version: 1,
          data_freshness_ts: "2026-05-01T12:05:00Z",
        },
      ],
    });
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/fleet-stream**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body:
        "id: bootstrap\n" +
        "event: heartbeat\n" +
        'data: {"id":"bootstrap","event":"heartbeat","generated_at":"2026-05-01T12:05:00Z","freshness_ts":"2026-05-01T12:05:00Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":1}\n\n',
    });
  });
  await page.route("**/backend/rule-engine/api/v1/alerts/events/unread-count?**", async (route) => {
    await fulfillJson(route, { data: { count: 0 } });
  });
  await page.route("**/backend/rule-engine/api/v1/alerts/events?**", async (route) => {
    await fulfillJson(route, {
      data: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
  });

  await page.goto("/machines");

  await expect(page.getByText("Maintenance in progress", { exact: true })).toBeVisible();
  await expect(page.getByText("Scheduled maintenance", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Database maintenance in progress" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Scheduled read replica maintenance" })).toBeVisible();
});

test("platform-maintenance UI surfaces overlap rejection and missing-record update/delete errors truthfully", async ({ page }) => {
  const me = buildSuperAdminMe();
  const tenants = [
    { id: "SH00000001", name: "Factory Ops", slug: "factory-ops", is_active: true, created_at: "2026-04-01T00:00:00Z" },
  ];
  const existingAnnouncement = {
    id: "pm-existing",
    title: "Existing maintenance",
    severity: "warning",
    message: "Existing planned work.",
    starts_at: "2026-05-01T12:00:00Z",
    estimated_duration_minutes: 60,
    ends_at: "2026-05-01T13:00:00Z",
    status: "scheduled",
    effective_status: "scheduled",
    broadcast_all_tenants: false,
    target_tenant_ids: ["SH00000001"],
    created_by: "super-1",
    updated_by: "super-1",
    created_at: "2026-04-01T00:00:00Z",
    updated_at: "2026-04-01T00:00:00Z",
  };

  await seedSession(
    page,
    me,
    { sub: "super-1", role: "super_admin", permissions_version: 1, tenant_entitlements_version: 1, exp: Math.floor(Date.now() / 1000) + 3600 },
    null,
  );

  await page.route("**/backend/auth/api/admin/tenants", async (route) => {
    await fulfillJson(route, tenants);
  });

  await page.route("**/backend/auth/api/admin/platform-maintenance", async (route) => {
    if (route.request().method() === "GET") {
      await fulfillJson(route, [existingAnnouncement]);
      return;
    }

    await fulfillJson(route, {
      detail: {
        message: "Another maintenance window already overlaps this organisation and time range.",
      },
    }, 409);
  });

  await page.route("**/backend/auth/api/admin/platform-maintenance/pm-existing", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      await fulfillJson(route, existingAnnouncement);
      return;
    }
    if (method === "PATCH") {
      await fulfillJson(route, {
        detail: {
          message: "This maintenance notice no longer exists.",
        },
      }, 404);
      return;
    }
    if (method === "DELETE") {
      await fulfillJson(route, {
        detail: {
          message: "This maintenance notice no longer exists.",
        },
      }, 404);
      return;
    }
    await fulfillJson(route, {}, 405);
  });

  await page.goto("/admin/platform-maintenance");

  await page.getByLabel("Notice title").fill("Overlapping maintenance");
  await page.getByLabel("Start time").fill("2026-05-01T18:00");
  await page.getByLabel("Expected duration (minutes)").fill("60");
  await page.locator("#maintenance-message").fill("Trying to schedule an overlapping maintenance notice.");
  await page.getByRole("button", { name: "Selected organisations" }).click();
  const overlapFactoryOpsRow = page.locator("label").filter({ hasText: "Factory Ops" }).first();
  await expect(overlapFactoryOpsRow).toBeVisible();
  await overlapFactoryOpsRow.locator('input[type="checkbox"]').check();
  await page.getByRole("button", { name: "Schedule Notice" }).click();
  await expect(page.getByText("Another maintenance window already overlaps this organisation and time range.")).toBeVisible();

  const existingMaintenanceButton = page.getByRole("button", { name: /Existing maintenance/i }).first();
  await expect(existingMaintenanceButton).toBeVisible();
  await existingMaintenanceButton.click({ force: true });
  await expect(page.getByText("Editing a scheduled notice")).toBeVisible();
  await page.getByRole("button", { name: "Save Changes" }).click();
  await expect(page.getByText("This maintenance notice no longer exists.")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete Notice" }).click();
  await expect(page.getByText("This maintenance notice no longer exists.")).toBeVisible();
});
