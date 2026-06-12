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

function accessToken(expOffsetSeconds = 3600) {
  return `header.${base64Json({
    sub: "user-ops",
    role: "org_admin",
    tenant_id: "SH00000001",
    exp: Math.floor(Date.now() / 1000) + expOffsetSeconds,
  })}.signature`;
}

function meSnapshot() {
  return {
    user: {
      id: "user-ops",
      email: "ops@example.com",
      full_name: "Org Admin",
      role: "org_admin",
      tenant_id: "SH00000001",
      is_active: true,
      created_at: "2026-05-02T00:00:00Z",
      last_login_at: "2026-05-02T01:00:00Z",
    },
    tenant: {
      id: "SH00000001",
      name: "Factory Ops",
      slug: "factory-ops",
      is_active: true,
      created_at: "2026-05-02T00:00:00Z",
    },
    plant_ids: ["plant-1"],
    entitlements: {
      premium_feature_grants: [],
      role_feature_matrix: {
        org_admin: ["machines", "calendar", "rules", "reports", "settings"],
        plant_manager: ["machines", "calendar", "rules", "reports"],
        operator: ["machines", "calendar", "rules"],
        viewer: ["machines", "calendar"],
        super_admin: ["machines", "calendar", "rules", "reports", "settings"],
      },
      baseline_features_by_role: {
        org_admin: ["machines", "calendar", "rules", "reports", "settings"],
        plant_manager: ["machines", "calendar", "rules", "reports"],
        operator: ["machines", "calendar", "rules"],
        viewer: ["machines", "calendar"],
        super_admin: ["machines", "calendar", "rules", "reports", "settings"],
      },
      effective_features_by_role: {
        org_admin: ["machines", "calendar", "rules", "reports", "settings"],
        plant_manager: ["machines", "calendar", "rules", "reports"],
        operator: ["machines", "calendar", "rules"],
        viewer: ["machines", "calendar"],
        super_admin: ["machines", "calendar", "rules", "reports", "settings"],
      },
      available_features: ["machines", "calendar", "rules", "reports", "settings"],
      entitlements_version: 1,
    },
  };
}

async function installSession(page, token) {
  await page.addInitScript(({ accessToken, me }) => {
    window.sessionStorage.setItem("factoryops_access_token", accessToken);
    window.sessionStorage.setItem("factoryops_access_token_v2", accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_selected_tenant", "SH00000001");
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(me));
  }, {
    accessToken: token,
    me: meSnapshot(),
  });
}

test("protected tenant requests refresh auth before retrying and eventually succeed", async ({ page }) => {
  const staleToken = accessToken(3600);
  const freshToken = accessToken(3600);
  const me = meSnapshot();
  let refreshCalls = 0;
  let plantCalls = 0;
  let lastPlantAuthHeader = "";

  await installSession(page, staleToken);

  await page.route("**/backend/auth/api/v1/auth/me", async (route) => {
    await fulfillJson(route, me);
  });

  await page.route("**/backend/auth/api/v1/platform-maintenance/current", async (route) => {
    await fulfillJson(route, { tenant_id: "SH00000001", announcements: [] });
  });

  await page.route("**/backend/auth/api/v1/auth/refresh", async (route) => {
    refreshCalls += 1;
    await fulfillJson(route, {
      access_token: freshToken,
      refresh_token: "refresh-token-2",
      token_type: "bearer",
      expires_in: 3600,
    });
  });

  await page.route("**/backend/auth/api/v1/tenants/SH00000001/plants", async (route) => {
    plantCalls += 1;
    lastPlantAuthHeader = route.request().headers().authorization || "";
    if (plantCalls === 1) {
      await fulfillJson(route, { message: "TOKEN_EXPIRED" }, 401);
      return;
    }
    await fulfillJson(route, [{
      id: "plant-1",
      tenant_id: "SH00000001",
      name: "Plant One",
      location: "Pune",
      timezone: "Asia/Kolkata",
      is_active: true,
      created_at: "2026-05-02T00:00:00Z",
    }]);
  });

  await page.goto("/tenant/plants");
  await expect(page.getByRole("heading", { name: "Plants" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Plant One" }).first()).toBeVisible();
  await expect.poll(() => refreshCalls).toBe(1);
  await expect.poll(() => plantCalls).toBeGreaterThanOrEqual(2);
  await expect(lastPlantAuthHeader).toBe(`Bearer ${freshToken}`);
});

test("expired refresh on a protected tenant request clears auth and returns to login", async ({ page }) => {
  const staleToken = accessToken(3600);
  const me = meSnapshot();
  let refreshCalls = 0;

  await installSession(page, staleToken);

  await page.route("**/backend/auth/api/v1/auth/me", async (route) => {
    await fulfillJson(route, me);
  });

  await page.route("**/backend/auth/api/v1/platform-maintenance/current", async (route) => {
    await fulfillJson(route, { tenant_id: "SH00000001", announcements: [] });
  });

  await page.route("**/backend/auth/api/v1/auth/refresh", async (route) => {
    refreshCalls += 1;
    await fulfillJson(route, { message: "Session expired" }, 401);
  });

  await page.route("**/backend/auth/api/v1/tenants/SH00000001/plants", async (route) => {
    await fulfillJson(route, { message: "TOKEN_EXPIRED" }, 401);
  });

  await page.goto("/tenant/plants");
  await expect(page).toHaveURL(/\/login$/);
  await expect.poll(() => refreshCalls).toBe(1);
});
