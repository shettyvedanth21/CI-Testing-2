/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");

function base64Json(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function iso(minutesOffset = 0) {
  return new Date(Date.UTC(2026, 4, 3, 7, 0 + minutesOffset, 0)).toISOString();
}

async function fulfillJson(route, data, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

function buildEntitlements() {
  const modules = ["machines", "calendar", "rules", "reports", "settings", "analytics", "copilot", "waste_analysis"];
  return {
    premium_feature_grants: [],
    role_feature_matrix: {
      super_admin: modules,
      org_admin: ["machines", "calendar", "rules", "reports", "settings"],
      plant_manager: ["machines", "calendar", "rules", "reports"],
      operator: ["machines", "calendar", "rules"],
      viewer: ["machines", "calendar"],
    },
    baseline_features_by_role: {
      super_admin: modules,
      org_admin: ["machines", "calendar", "rules", "reports", "settings"],
      plant_manager: ["machines", "calendar", "rules", "reports"],
      operator: ["machines", "calendar", "rules"],
      viewer: ["machines", "calendar"],
    },
    effective_features_by_role: {
      super_admin: modules,
      org_admin: ["machines", "calendar", "rules", "reports", "settings"],
      plant_manager: ["machines", "calendar", "rules", "reports"],
      operator: ["machines", "calendar", "rules"],
      viewer: ["machines", "calendar"],
    },
    available_features: modules,
    entitlements_version: 1,
  };
}

async function installAdminOrgCreateHarness(page) {
  const state = {
    currentUser: null,
    tenants: [
      { id: "SH00000001", name: "Factory Ops", slug: "factory-ops", is_active: true, created_at: iso(-120) },
    ],
    tenantCounter: 2,
  };

  const superAdmin = {
    id: "user-super-1",
    email: "super@example.com",
    password: "FactoryOps#123",
    full_name: "Super Admin",
    role: "super_admin",
    tenant_id: null,
    is_active: true,
    created_at: iso(-180),
    last_login_at: null,
  };

  function buildToken() {
    return `header.${base64Json({
      sub: superAdmin.id,
      role: superAdmin.role,
      tenant_id: null,
      exp: Math.floor(Date.now() / 1000) + 3600,
    })}.signature`;
  }

  function buildMe() {
    return {
      user: {
        id: superAdmin.id,
        email: superAdmin.email,
        full_name: superAdmin.full_name,
        role: superAdmin.role,
        tenant_id: null,
        is_active: true,
        created_at: superAdmin.created_at,
        last_login_at: superAdmin.last_login_at,
        lifecycle_state: "active",
        invite_status: "none",
      },
      tenant: null,
      plant_ids: [],
      entitlements: buildEntitlements(),
    };
  }

  await page.route("**/backend/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/backend/auth/api/v1/auth/login" && method === "POST") {
      const body = request.postDataJSON();
      if (body.email === superAdmin.email && body.password === superAdmin.password) {
        state.currentUser = superAdmin;
        superAdmin.last_login_at = iso(0);
        await fulfillJson(route, {
          access_token: buildToken(),
          refresh_token: "refresh-token",
          token_type: "bearer",
          expires_in: 3600,
        });
        return;
      }
      await fulfillJson(route, { message: "Invalid email or password" }, 401);
      return;
    }

    if (path === "/backend/auth/api/v1/auth/logout" && method === "POST") {
      state.currentUser = null;
      await fulfillJson(route, { message: "logged out" });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/refresh" && method === "POST") {
      if (!state.currentUser) {
        await fulfillJson(route, { message: "Session expired" }, 401);
        return;
      }
      await fulfillJson(route, {
        access_token: buildToken(),
        refresh_token: "refresh-token",
        token_type: "bearer",
        expires_in: 3600,
      });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/me" && method === "GET") {
      if (!state.currentUser) {
        await fulfillJson(route, { message: "Unauthenticated" }, 401);
        return;
      }
      await fulfillJson(route, buildMe());
      return;
    }

    if (path === "/backend/auth/api/v1/platform-maintenance/current" && method === "GET") {
      await fulfillJson(route, { tenant_id: null, announcements: [] });
      return;
    }

    if (path === "/backend/auth/api/admin/tenants" && method === "GET") {
      await fulfillJson(route, state.tenants);
      return;
    }

    if (path === "/backend/auth/api/admin/tenants" && method === "POST") {
      const body = request.postDataJSON();
      if ((body.name || "").trim().length < 2) {
        await fulfillJson(route, { message: "Organisation name must be at least 2 characters." }, 400);
        return;
      }
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(String(body.slug || "").trim())) {
        await fulfillJson(route, { message: "Slug must be lowercase letters, numbers, and hyphens only" }, 400);
        return;
      }
      if (state.tenants.some((tenant) => tenant.slug === String(body.slug).trim())) {
        await fulfillJson(route, { message: "slug already taken" }, 409);
        return;
      }
      const newTenant = {
        id: `SH0000000${state.tenantCounter++}`,
        name: String(body.name).trim(),
        slug: String(body.slug).trim(),
        is_active: true,
        created_at: iso(0),
      };
      state.tenants.unshift(newTenant);
      await fulfillJson(route, newTenant, 201);
      return;
    }

    const tenantDetailMatch = path.match(/^\/backend\/auth\/api\/admin\/tenants\/([^/]+)$/);
    if (tenantDetailMatch && method === "GET") {
      const tenant = state.tenants.find((entry) => entry.id === tenantDetailMatch[1]);
      if (!tenant) {
        await fulfillJson(route, { message: "TENANT_NOT_FOUND" }, 404);
        return;
      }
      await fulfillJson(route, tenant);
      return;
    }

    const tenantStateMatch = path.match(/^\/backend\/auth\/api\/admin\/tenants\/([^/]+)\/(suspend|reactivate)$/);
    if (tenantStateMatch && method === "PATCH") {
      const [, tenantId, action] = tenantStateMatch;
      const tenant = state.tenants.find((entry) => entry.id === tenantId);
      if (!tenant) {
        await fulfillJson(route, { message: "TENANT_NOT_FOUND" }, 404);
        return;
      }
      tenant.is_active = action === "reactivate";
      await fulfillJson(route, tenant);
      return;
    }

    const tenantPlantsMatch = path.match(/^\/backend\/auth\/api\/v1\/tenants\/([^/]+)\/plants$/);
    if (tenantPlantsMatch && method === "GET") {
      await fulfillJson(route, []);
      return;
    }

    const tenantUsersMatch = path.match(/^\/backend\/auth\/api\/v1\/tenants\/([^/]+)\/users$/);
    if (tenantUsersMatch && method === "GET") {
      await fulfillJson(route, []);
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/dashboard/summary") && method === "GET") {
      await fulfillJson(route, {
        generated_at: iso(0),
        stale: false,
        warnings: [],
        summary: { total_devices: 0, system_health: 100 },
        alerts: { active_alerts: 0 },
        devices: [],
        cost_data_state: "fresh",
        cost_data_reasons: [],
        cost_generated_at: iso(0),
        energy_widgets: { today_loss_kwh: 0, today_loss_cost_inr: 0, currency: "INR" },
      });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/dashboard/fleet-snapshot") && method === "GET") {
      await fulfillJson(route, {
        generated_at: iso(0),
        total: 0,
        page: 1,
        page_size: 60,
        total_pages: 1,
        devices: [],
      });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/dashboard/fleet-stream") && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `id: 1\nevent: heartbeat\ndata: ${JSON.stringify({
          id: "1",
          event: "heartbeat",
          generated_at: iso(0),
          freshness_ts: iso(0),
          stale: false,
          warnings: [],
          devices: [],
          partial: false,
          version: 0,
        })}\n\n`,
      });
      return;
    }

    if (path.startsWith("/backend/rule-engine/api/v1/alerts/events/unread-count") && method === "GET") {
      await fulfillJson(route, { data: { count: 0 } });
      return;
    }

    if (path.startsWith("/backend/rule-engine/api/v1/alerts/events")) {
      if (method === "DELETE") {
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
      return;
    }

    if (path.startsWith("/backend/") || path.startsWith("/api/")) {
      await fulfillJson(route, { message: `UNMOCKED_API ${method} ${path}` }, 500);
      return;
    }

    await route.fallback();
  });
}

test("super admin creates an organisation from UI and sees it in the admin directory", async ({ page }) => {
  await installAdminOrgCreateHarness(page);

  await page.goto("/login");
  await page.getByLabel("Email").fill("super@example.com");
  await page.getByLabel("Password").fill("FactoryOps#123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);

  await page.goto("/admin/tenants");
  await expect(page.getByRole("heading", { name: "Organisations" })).toBeVisible();
  await page.getByRole("button", { name: "New Organisation" }).click();

  const dialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "New organisation" }) });
  await dialog.getByLabel("Organisation name").fill("Aurora Metals");
  await dialog.getByLabel("Slug").fill("aurora-metals");
  await dialog.getByRole("button", { name: "Create organisation", exact: true }).click();

  await expect(page).toHaveURL(/\/admin\/tenants\/SH00000002$/);
  await expect(page.getByRole("heading", { name: "Aurora Metals" })).toBeVisible();

  await page.goto("/admin/tenants");
  await expect(page.getByRole("row").filter({ hasText: "Aurora Metals" }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "aurora-metals" }).first()).toBeVisible();
});
