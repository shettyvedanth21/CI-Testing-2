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

function createState() {
  const tenant = {
    id: "SH00000001",
    name: "Factory Ops",
    slug: "factory-ops",
    is_active: true,
    created_at: iso(-120),
  };

  const superAdmin = {
    id: "user-super-1",
    email: "super@example.com",
    password: "FactoryOps#123",
    full_name: "Super Admin",
    role: "super_admin",
    tenant_id: null,
    is_active: true,
    created_at: iso(-120),
    last_login_at: null,
  };

  const invitedAdmin = {
    id: "user-org-invite-1",
    email: "new-org-admin@example.com",
    password: null,
    full_name: "New Org Admin",
    role: "org_admin",
    tenant_id: tenant.id,
    is_active: false,
    created_at: iso(-10),
    last_login_at: null,
    lifecycle_state: "invited",
    invite_status: "pending",
  };

  return {
    tenant,
    superAdmin,
    plants: [
      {
        id: "plant-1",
        tenant_id: tenant.id,
        name: "Plant One",
        location: "Pune",
        timezone: "Asia/Kolkata",
        is_active: true,
        created_at: iso(-110),
      },
    ],
    users: [invitedAdmin],
    tokens: {
      "invite-org-admin-valid": {
        status: "valid",
        action_type: "invite_set_password",
        email: invitedAdmin.email,
        full_name: invitedAdmin.full_name,
      },
    },
    currentUser: null,
  };
}

function buildEntitlements(role) {
  const modules = role === "super_admin"
    ? ["machines", "calendar", "rules", "reports", "settings", "analytics", "copilot", "waste_analysis"]
    : ["machines", "calendar", "rules", "reports", "settings"];
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
    available_features: Array.from(new Set([
      "machines",
      "calendar",
      "rules",
      "reports",
      "settings",
      ...(role === "super_admin" ? ["analytics", "copilot", "waste_analysis"] : []),
    ])),
    entitlements_version: 1,
  };
}

function buildMe(state, user) {
  return {
    user: {
      id: user.id,
      email: user.email,
      full_name: user.full_name,
      role: user.role,
      tenant_id: user.tenant_id,
      is_active: user.is_active,
      created_at: user.created_at,
      last_login_at: user.last_login_at,
      lifecycle_state: user.lifecycle_state ?? "active",
      invite_status: user.invite_status ?? "none",
    },
    tenant: user.tenant_id ? state.tenant : null,
    plant_ids: user.role === "org_admin" ? state.plants.map((plant) => plant.id) : [],
    entitlements: buildEntitlements(user.role),
  };
}

function buildAccessToken(user) {
  return `header.${base64Json({
    sub: user.id,
    role: user.role,
    tenant_id: user.tenant_id,
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}.signature`;
}

async function installOrgAdminInviteHarness(page) {
  const state = createState();

  await page.route("**/backend/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/backend/auth/api/v1/auth/login" && method === "POST") {
      const body = request.postDataJSON();
      if (body.email === state.superAdmin.email && body.password === state.superAdmin.password) {
        state.currentUser = state.superAdmin;
        state.superAdmin.last_login_at = iso(0);
        await fulfillJson(route, {
          access_token: buildAccessToken(state.superAdmin),
          refresh_token: "refresh-token",
          token_type: "bearer",
          expires_in: 3600,
        });
        return;
      }
      const invited = state.users.find((user) => user.email === body.email);
      if (invited && invited.password === body.password && invited.is_active) {
        state.currentUser = invited;
        invited.last_login_at = iso(0);
        await fulfillJson(route, {
          access_token: buildAccessToken(invited),
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
        access_token: buildAccessToken(state.currentUser),
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
      await fulfillJson(route, buildMe(state, state.currentUser));
      return;
    }

    if (path === "/backend/auth/api/v1/platform-maintenance/current" && method === "GET") {
      await fulfillJson(route, { tenant_id: state.tenant.id, announcements: [] });
      return;
    }

    if (path === "/backend/auth/api/admin/tenants" && method === "GET") {
      await fulfillJson(route, [state.tenant]);
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/plants` && method === "GET") {
      await fulfillJson(route, state.plants);
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/entitlements` && method === "GET") {
      await fulfillJson(route, buildEntitlements("org_admin"));
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/users` && method === "GET") {
      await fulfillJson(route, state.users.map((user) => ({
        id: user.id,
        email: user.email,
        full_name: user.full_name,
        role: user.role,
        tenant_id: user.tenant_id,
        is_active: user.is_active,
        created_at: user.created_at,
        last_login_at: user.last_login_at,
        lifecycle_state: user.lifecycle_state,
        invite_status: user.invite_status,
        can_resend_invite: true,
        can_reactivate: !user.is_active,
        can_deactivate: user.is_active,
      })));
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/users` && method === "POST") {
      const body = request.postDataJSON();
      const newUser = {
        id: `user-org-${state.users.length + 1}`,
        email: body.email,
        password: null,
        full_name: body.full_name,
        role: "org_admin",
        tenant_id: state.tenant.id,
        is_active: false,
        created_at: iso(1),
        last_login_at: null,
        lifecycle_state: "invited",
        invite_status: "pending",
      };
      state.users.unshift(newUser);
      state.tokens["invite-created-org-admin"] = {
        status: "valid",
        action_type: "invite_set_password",
        email: newUser.email,
        full_name: newUser.full_name,
      };
      await fulfillJson(route, {
        id: newUser.id,
        email: newUser.email,
        full_name: newUser.full_name,
        role: newUser.role,
        tenant_id: newUser.tenant_id,
        is_active: newUser.is_active,
        created_at: newUser.created_at,
        last_login_at: null,
        lifecycle_state: "invited",
        invite_status: "pending",
        can_resend_invite: true,
        can_reactivate: false,
        can_deactivate: false,
      }, 201);
      return;
    }

    if (path.startsWith("/backend/auth/api/v1/auth/action-token/") && path.endsWith("/status") && method === "GET") {
      const token = decodeURIComponent(path.split("/").slice(-2, -1)[0]);
      const details = state.tokens[token];
      if (!details) {
        await fulfillJson(route, {
          status: "invalid",
          action_type: null,
          email: null,
          full_name: null,
        });
        return;
      }
      await fulfillJson(route, details);
      return;
    }

    if (path === "/backend/auth/api/v1/auth/invitations/accept" && method === "POST") {
      const body = request.postDataJSON();
      const token = state.tokens[body.token];
      if (!token || token.status !== "valid") {
        await fulfillJson(route, { message: "This invite link is invalid." }, 400);
        return;
      }
      const invitedUser = state.users.find((user) => user.email === token.email);
      if (!invitedUser) {
        await fulfillJson(route, { message: "This invite link is invalid." }, 400);
        return;
      }
      invitedUser.password = body.password;
      invitedUser.is_active = true;
      invitedUser.lifecycle_state = "active";
      invitedUser.invite_status = "none";
      token.status = "used";
      await fulfillJson(route, { message: "accepted" });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: "text/plain",
      body: `Unmocked route: ${method} ${path}`,
    });
  });

  return {
    getLatestInviteToken() {
      return Object.entries(state.tokens).find(([, value]) => value.email === "new-org-admin@example.com" && value.status === "valid")?.[0] ?? null;
    },
  };
}

test("super admin can invite an org admin, who accepts the invite and signs in", async ({ page }) => {
  const harness = await installOrgAdminInviteHarness(page);

  await page.goto("/login");
  await page.getByLabel("Email").fill("super@example.com");
  await page.getByLabel("Password").fill("FactoryOps#123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);

  await page.goto("/admin/orgs/SH00000001");
  await expect(page.getByRole("heading", { name: "Factory Ops" })).toBeVisible();
  await page.getByRole("button", { name: /Org Admins/ }).click();
  await page.getByRole("button", { name: "Invite Org Admin" }).click();
  await page.getByLabel("Email").fill("new-org-admin@example.com");
  await page.getByLabel("Full name").fill("New Org Admin");
  await page.getByRole("button", { name: "Create org admin" }).click();
  await expect(page.getByText("Org admin invite issued.")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "new-org-admin@example.com" }).first()).toBeVisible();

  const inviteToken = harness.getLatestInviteToken();
  expect(inviteToken).toBeTruthy();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto(`/accept-invite?token=${inviteToken}`);
  await expect(page.getByLabel("New password")).toBeVisible();
  await page.getByLabel("New password").fill("OrgAdmin#123");
  await page.getByLabel("Confirm password").fill("OrgAdmin#123");
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByText("Password set successfully. Redirecting to sign in...")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("Email").fill("new-org-admin@example.com");
  await page.getByLabel("Password").fill("OrgAdmin#123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
});
