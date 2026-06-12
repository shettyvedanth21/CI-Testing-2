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

test("device onboarding is blocked when the organisation has no plants", async ({ page }) => {
  const me = {
    user: {
      id: "user-1",
      email: "admin@example.com",
      full_name: "Admin User",
      role: "org_admin",
      tenant_id: "SH00000001",
      is_active: true,
      created_at: new Date().toISOString(),
      last_login_at: null,
    },
    org: {
      id: "SH00000001",
      name: "Factory Ops",
      slug: "factory-ops",
      is_active: true,
      created_at: new Date().toISOString(),
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

  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem(
      "factoryops_access_token",
      snapshot.accessToken,
    );
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot.me));
  }, {
    accessToken: `header.${base64Json({ role: "org_admin", tenant_id: "SH00000001" })}.signature`,
    me,
  });

  await page.route("**/api/v1/auth/me", async (route) => {
    await fulfillJson(route, me);
  });
  await page.route("**/api/v1/tenants/SH00000001/plants", async (route) => {
    await fulfillJson(route, []);
  });
  await page.route("**/api/v1/devices/dashboard/summary", async (route) => {
    await fulfillJson(route, {
      generated_at: new Date().toISOString(),
      stale: false,
      warnings: [],
      summary: {
        total_devices: 0,
        system_health: 100,
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
  await page.route("**/api/v1/devices/dashboard/fleet-snapshot**", async (route) => {
    await fulfillJson(route, {
      generated_at: new Date().toISOString(),
      total: 0,
      page: 1,
      page_size: 60,
      total_pages: 1,
      devices: [],
    });
  });
  await page.route("**/api/v1/devices/dashboard/fleet-stream**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body:
        "id: 1\n" +
        "event: heartbeat\n" +
        'data: {"id":"1","event":"heartbeat","generated_at":"2026-04-02T00:00:00.000Z","freshness_ts":"2026-04-02T00:00:00.000Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":0}\n\n',
    });
  });
  await page.route("**/api/v1/alerts/events/unread-count**", async (route) => {
    await fulfillJson(route, { data: { count: 0 } });
  });
  await page.route("**/api/v1/alerts/events**", async (route) => {
    if (route.request().method() === "DELETE") {
      await fulfillJson(route, { data: { deleted: 0 } });
      return;
    }
    await fulfillJson(route, {
      data: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
  });

  await page.goto("/machines");

  await expect(page.getByRole("button", { name: "+ Add Device" })).toHaveCount(0);
});
