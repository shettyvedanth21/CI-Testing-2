/* eslint-disable @typescript-eslint/no-require-imports */

function base64Json(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function iso(minutesOffset = 0) {
  return new Date(Date.UTC(2026, 4, 2, 6, 0 + minutesOffset, 0)).toISOString();
}

async function fulfillJson(route, data, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function createEntitlements(role) {
  const modulesByRole = {
    super_admin: ["machines", "calendar", "rules", "reports", "settings"],
    org_admin: ["machines", "calendar", "rules", "reports", "settings"],
    plant_manager: ["machines", "calendar", "rules", "reports"],
    operator: ["machines", "calendar", "rules"],
    viewer: ["machines", "calendar"],
  };
  const availableModules = clone(modulesByRole[role] ?? []);

  return {
    premium_feature_grants: [],
    role_feature_matrix: clone(modulesByRole),
    baseline_features_by_role: clone(modulesByRole),
    effective_features_by_role: clone(modulesByRole),
    available_features: availableModules,
    entitlements_version: 1,
  };
}

function createHarnessState(options = {}) {
  const tenant = {
    id: "SH00000001",
    name: "Factory Ops",
    slug: "factory-ops",
    is_active: true,
    created_at: iso(-120),
  };

  const plants = options.initialPlants ?? [
    {
      id: "plant-1",
      tenant_id: tenant.id,
      name: "Plant One",
      slug: "plant-one",
      location: "Pune",
      timezone: "Asia/Kolkata",
      is_active: true,
      created_at: iso(-100),
      updated_at: iso(-100),
    },
  ];

  const foreignTenant = {
    id: "SH00000077",
    name: "Shadow Works",
    slug: "shadow-works",
    is_active: true,
    created_at: iso(-115),
  };

  const users = [
    {
      id: "user-super",
      email: "super@example.com",
      password: "FactoryOps#123",
      full_name: "Super Admin",
      role: "super_admin",
      tenant_id: null,
      is_active: true,
      created_at: iso(-120),
      last_login_at: null,
      lifecycle_state: "active",
      invite_status: "none",
      plant_ids: [],
    },
    {
      id: "user-org-admin",
      email: "ops@example.com",
      password: "FactoryOps#123",
      full_name: "Org Admin",
      role: "org_admin",
      tenant_id: tenant.id,
      is_active: true,
      created_at: iso(-110),
      last_login_at: null,
      lifecycle_state: "active",
      invite_status: "none",
      plant_ids: plants.map((plant) => plant.id),
    },
    {
      id: "user-plant-manager",
      email: "pm@example.com",
      password: "FactoryOps#123",
      full_name: "Plant Manager",
      role: "plant_manager",
      tenant_id: tenant.id,
      is_active: true,
      created_at: iso(-90),
      last_login_at: null,
      lifecycle_state: "active",
      invite_status: "none",
      plant_ids: ["plant-1"],
    },
    {
      id: "user-viewer",
      email: "viewer@example.com",
      password: "FactoryOps#123",
      full_name: "Viewer User",
      role: "viewer",
      tenant_id: tenant.id,
      is_active: true,
      created_at: iso(-80),
      last_login_at: null,
      lifecycle_state: "active",
      invite_status: "none",
      plant_ids: ["plant-1"],
    },
    {
      id: "user-disabled",
      email: "disabled@example.com",
      password: "FactoryOps#123",
      full_name: "Disabled User",
      role: "viewer",
      tenant_id: tenant.id,
      is_active: false,
      created_at: iso(-70),
      last_login_at: null,
      lifecycle_state: "deactivated",
      invite_status: "none",
      plant_ids: ["plant-1"],
    },
    {
      id: "user-seeded-admin",
      email: "seeded-admin@example.com",
      password: null,
      full_name: "Seeded Admin",
      role: "org_admin",
      tenant_id: tenant.id,
      is_active: false,
      created_at: iso(-60),
      last_login_at: null,
      lifecycle_state: "invited",
      invite_status: "pending",
      plant_ids: plants.map((plant) => plant.id),
    },
  ];

  if (options.includeForeignTenantFixtures) {
    plants.push({
      id: "plant-shadow-1",
      tenant_id: foreignTenant.id,
      name: "Shadow Plant",
      slug: "shadow-plant",
      location: "Mumbai",
      timezone: "Asia/Kolkata",
      is_active: true,
      created_at: iso(-95),
      updated_at: iso(-95),
    });

    users.push({
      id: "user-shadow-admin",
      email: "other-admin@example.com",
      password: "FactoryOps#123",
      full_name: "Shadow Admin",
      role: "org_admin",
      tenant_id: foreignTenant.id,
      is_active: true,
      created_at: iso(-85),
      last_login_at: null,
      lifecycle_state: "active",
      invite_status: "none",
      plant_ids: ["plant-shadow-1"],
    });
  }

  return {
    tenant,
    foreignTenant,
    users,
    plants,
    currentUserEmail: null,
    inviteCounter: 1,
    plantCounter: 2,
    deviceCounter: 2,
    ruleCounter: 2,
    tokens: {
      "reset-valid": {
        type: "password_reset",
        status: "valid",
        email: "ops@example.com",
        full_name: "Org Admin",
      },
      "reset-used": {
        type: "password_reset",
        status: "used",
        email: "ops@example.com",
        full_name: "Org Admin",
      },
      "invite-valid-seeded": {
        type: "invite_set_password",
        status: "valid",
        email: "seeded-admin@example.com",
        full_name: "Seeded Admin",
      },
    },
    devices: [
      {
        device_id: "AD00000010",
        device_name: "Packaging Line A",
        device_type: "compressor",
        plant_id: "plant-1",
        runtime_status: "running",
        status: "active",
        location: "Pune Bay 1",
        first_telemetry_timestamp: iso(-30),
        last_seen_timestamp: iso(-1),
      },
    ],
    rulesByDevice: {
      AD00000010: [
        {
          rule_id: "rule-1",
          rule_name: "Idle longer than 30 minutes",
          description: null,
          rule_type: "continuous_idle_duration",
          scope: "selected_devices",
          property: null,
          condition: null,
          threshold: null,
          time_window_start: null,
          time_window_end: null,
          timezone: "Asia/Kolkata",
          time_condition: null,
          duration_minutes: 30,
          notification_channels: ["email"],
          notification_recipients: [{ channel: "email", value: "alerts@example.com" }],
          cooldown_minutes: 15,
          cooldown_seconds: 900,
          cooldown_unit: "minutes",
          cooldown_mode: "interval",
          device_ids: ["AD00000010"],
          status: "active",
          created_at: iso(-25),
          updated_at: iso(-25),
        },
      ],
    },
    maintenanceByDevice: {
      AD00000010: [
        {
          id: 1,
          tenant_id: tenant.id,
          device_id: "AD00000010",
          maintenance_date: "2026-05-01",
          title: "Filter replacement",
          description: "Replaced intake filter and inspected coupling.",
          cost: 1250,
          performed_by: "Ajay",
          status: "completed",
          next_due_date: "2026-06-01",
          created_by: "user-org-admin",
          created_at: iso(-20),
          updated_at: iso(-20),
        },
      ],
    },
    ruleMutationDenied: false,
    nextMaintenanceDeleteNotFound: false,
  };
}

function findUser(state, email) {
  return state.users.find((user) => user.email === email) ?? null;
}

function currentUser(state) {
  return state.currentUserEmail ? findUser(state, state.currentUserEmail) : null;
}

function buildMe(state) {
  const user = currentUser(state);
  if (!user) return null;
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
      lifecycle_state: user.lifecycle_state,
      invite_status: user.invite_status,
      can_resend_invite: user.invite_status === "pending" || user.invite_status === "expired",
      can_reactivate: !user.is_active,
      can_deactivate: user.is_active,
    },
    tenant: user.tenant_id ? clone(state.tenant) : null,
    plant_ids: clone(user.plant_ids ?? []),
    entitlements: createEntitlements(user.role),
  };
}

function buildToken(user) {
  return `header.${base64Json({
    sub: user.id,
    role: user.role,
    tenant_id: user.tenant_id,
    plant_ids: user.plant_ids ?? [],
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}.signature`;
}

function summarizeMaintenance(records) {
  const totalCost = records.reduce((sum, record) => sum + Number(record.cost || 0), 0);
  const sorted = [...records].sort((a, b) => String(b.maintenance_date).localeCompare(String(a.maintenance_date)));
  return {
    total_records: records.length,
    total_cost: totalCost,
    latest_maintenance_date: sorted[0]?.maintenance_date ?? null,
    last_recorded_at: sorted[0]?.updated_at ?? null,
    next_due_date: sorted.map((record) => record.next_due_date).filter(Boolean).sort()[0] ?? null,
  };
}

function listTenantUsers(state) {
  return state.users
    .filter((user) => user.tenant_id === state.tenant.id)
    .map((user) => ({
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
      can_resend_invite: user.invite_status === "pending" || user.invite_status === "expired",
      can_reactivate: !user.is_active,
      can_deactivate: user.is_active,
    }));
}

function listTenantPlants(state) {
  return state.plants
    .filter((plant) => plant.tenant_id === state.tenant.id)
    .map((plant) => ({
      id: plant.id,
      tenant_id: plant.tenant_id,
      name: plant.name,
      location: plant.location,
      timezone: plant.timezone,
      is_active: plant.is_active,
      created_at: plant.created_at,
    }));
}

function listFleetItems(state) {
  return state.devices.map((device) => ({
    device_id: device.device_id,
    device_name: device.device_name,
    device_type: device.device_type,
    plant_id: device.plant_id,
    runtime_status: device.runtime_status,
    load_state: "running",
    current_band: "in_load",
    operational_status: "running",
    location: device.location,
    first_telemetry_timestamp: device.first_telemetry_timestamp,
    last_seen_timestamp: device.last_seen_timestamp,
    health_score: 92,
    has_uptime_config: false,
    data_freshness_ts: device.last_seen_timestamp,
    version: 1,
  }));
}

function makeActivityEvent() {
  return {
    event_id: "evt-1",
    device_id: "AD00000010",
    event_type: "alert_triggered",
    title: "Idle duration alert",
    message: "Packaging Line A remained idle for 30 minutes.",
    severity: "warning",
    is_read: false,
    acknowledged_by: null,
    acknowledged_at: null,
    created_at: iso(-5),
  };
}

async function installPhase3Harness(page, options = {}) {
  const state = createHarnessState(options);

  await page.route("**/backend/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/backend/auth/api/v1/auth/login" && method === "POST") {
      const body = request.postDataJSON();
      const user = findUser(state, body.email);
      if (!user || user.password !== body.password) {
        await fulfillJson(route, { message: "Invalid email or password" }, 401);
        return;
      }
      if (!user.is_active) {
        await fulfillJson(route, { message: "Account is disabled" }, 403);
        return;
      }
      state.currentUserEmail = user.email;
      user.last_login_at = iso(0);
      await fulfillJson(route, {
        access_token: buildToken(user),
        refresh_token: "refresh-token",
        token_type: "bearer",
        expires_in: 3600,
      });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/logout" && method === "POST") {
      state.currentUserEmail = null;
      await fulfillJson(route, { message: "logged out" });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/refresh" && method === "POST") {
      const user = currentUser(state);
      if (!user || !user.is_active) {
        await fulfillJson(route, { message: "Session expired" }, 401);
        return;
      }
      await fulfillJson(route, {
        access_token: buildToken(user),
        refresh_token: "refresh-token",
        token_type: "bearer",
        expires_in: 3600,
      });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/me" && method === "GET") {
      const me = buildMe(state);
      if (!me) {
        await fulfillJson(route, { message: "Unauthenticated" }, 401);
        return;
      }
      await fulfillJson(route, me);
      return;
    }

    if (
      (path === "/backend/auth/api/v1/platform-maintenance/current" ||
        path === "/api/v1/platform-maintenance/current") &&
      method === "GET"
    ) {
      await fulfillJson(route, { tenant_id: state.tenant.id, announcements: [] });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/password/forgot" && method === "POST") {
      const body = request.postDataJSON();
      if (body.email === "mailer-fail@example.com") {
        await fulfillJson(route, { message: "Email service is unavailable right now." }, 500);
        return;
      }
      await fulfillJson(route, { message: "ok" });
      return;
    }

    if (path.startsWith("/backend/auth/api/v1/auth/action-token/") && path.endsWith("/status") && method === "GET") {
      const token = decodeURIComponent(path.split("/").slice(-2, -1)[0]);
      const entry = state.tokens[token];
      if (!entry) {
        await fulfillJson(route, {
          status: "invalid",
          action_type: null,
          email: null,
          full_name: null,
        });
        return;
      }
      await fulfillJson(route, {
        status: entry.status,
        action_type: entry.type,
        email: entry.email,
        full_name: entry.full_name,
      });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/password/reset" && method === "POST") {
      const body = request.postDataJSON();
      const token = state.tokens[body.token];
      if (!token || token.type !== "password_reset" || token.status !== "valid") {
        await fulfillJson(route, { message: "This reset link is invalid." }, 400);
        return;
      }
      if (body.password !== body.confirm_password) {
        await fulfillJson(route, { message: "Passwords do not match" }, 400);
        return;
      }
      const user = findUser(state, token.email);
      if (user) {
        user.password = body.password;
      }
      token.status = "used";
      await fulfillJson(route, { message: "Password reset" });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/invitations/accept" && method === "POST") {
      const body = request.postDataJSON();
      const token = state.tokens[body.token];
      if (!token || token.type !== "invite_set_password" || token.status !== "valid") {
        await fulfillJson(route, { message: "This invite link is invalid." }, 400);
        return;
      }
      if (body.password !== body.confirm_password) {
        await fulfillJson(route, { message: "Passwords do not match" }, 400);
        return;
      }
      const user = findUser(state, token.email);
      if (user) {
        user.password = body.password;
        user.is_active = true;
        user.lifecycle_state = "active";
        user.invite_status = "none";
      }
      token.status = "used";
      await fulfillJson(route, { message: "Invitation accepted" });
      return;
    }

    if (path === "/backend/auth/api/admin/tenants" && method === "GET") {
      await fulfillJson(route, [state.tenant]);
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/entitlements`) {
      await fulfillJson(route, createEntitlements("org_admin"));
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/plants` && method === "GET") {
      await fulfillJson(route, listTenantPlants(state));
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/plants` && method === "POST") {
      const body = request.postDataJSON();
      if ((body.name || "").trim().length < 2) {
        await fulfillJson(route, { message: "Plant name must be at least 2 characters." }, 400);
        return;
      }
      if (
        state.plants.some(
          (plant) =>
            plant.tenant_id === state.tenant.id &&
            plant.name.toLowerCase() === String(body.name).trim().toLowerCase(),
        )
      ) {
        await fulfillJson(route, { message: "Plant name already exists for this organisation." }, 409);
        return;
      }
      const plant = {
        id: `plant-${state.plantCounter++}`,
        tenant_id: state.tenant.id,
        name: String(body.name).trim(),
        slug: String(body.name).trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""),
        location: body.location ?? null,
        timezone: body.timezone ?? "Asia/Kolkata",
        is_active: true,
        created_at: iso(0),
        updated_at: iso(0),
      };
      state.plants.unshift(plant);
      await fulfillJson(route, plant, 201);
      return;
    }

    const plantUpdateMatch = path.match(/\/backend\/auth\/api\/v1\/tenants\/SH00000001\/plants\/([^/]+)$/);
    if (plantUpdateMatch && method === "PUT") {
      const plant = state.plants.find((entry) => entry.id === plantUpdateMatch[1] && entry.tenant_id === state.tenant.id);
      if (!plant) {
        await fulfillJson(route, { message: "PLANT_NOT_FOUND" }, 404);
        return;
      }
      const body = request.postDataJSON();
      const proposedName = String(body.name ?? plant.name).trim();
      if (proposedName.length < 2) {
        await fulfillJson(route, { message: "Plant name must be at least 2 characters." }, 400);
        return;
      }
      if (
        state.plants.some(
          (entry) =>
            entry.id !== plant.id &&
            entry.tenant_id === state.tenant.id &&
            entry.name.toLowerCase() === proposedName.toLowerCase(),
        )
      ) {
        await fulfillJson(route, { message: "Plant name already exists for this organisation." }, 409);
        return;
      }
      plant.name = proposedName;
      plant.slug = proposedName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      plant.location = body.location ?? null;
      plant.timezone = body.timezone ?? plant.timezone;
      plant.updated_at = iso(2);
      await fulfillJson(route, plant);
      return;
    }

    const plantDeactivateMatch = path.match(/\/backend\/auth\/api\/v1\/tenants\/SH00000001\/plants\/([^/]+)\/(deactivate|reactivate)$/);
    if (plantDeactivateMatch && method === "PATCH") {
      const [, plantId, action] = plantDeactivateMatch;
      const plant = state.plants.find((entry) => entry.id === plantId && entry.tenant_id === state.tenant.id);
      if (!plant) {
        await fulfillJson(route, { message: "PLANT_NOT_FOUND" }, 404);
        return;
      }
      plant.is_active = action === "reactivate";
      await fulfillJson(route, plant);
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/users` && method === "GET") {
      await fulfillJson(route, listTenantUsers(state));
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenant.id}/users` && method === "POST") {
      const body = request.postDataJSON();
      if (state.users.some((user) => user.email.toLowerCase() === String(body.email).trim().toLowerCase() && user.is_active)) {
        await fulfillJson(route, { message: "This email is already registered." }, 409);
        return;
      }
      const tokenValue = `invite-${state.inviteCounter++}`;
      const user = {
        id: `user-invite-${state.inviteCounter}`,
        email: String(body.email).trim(),
        password: null,
        full_name: String(body.full_name).trim(),
        role: body.role,
        tenant_id: state.tenant.id,
        is_active: false,
        created_at: iso(0),
        last_login_at: null,
        lifecycle_state: "invited",
        invite_status: "pending",
        plant_ids: clone(body.plant_ids ?? []),
      };
      state.users.unshift(user);
      state.tokens[tokenValue] = {
        type: "invite_set_password",
        status: "valid",
        email: user.email,
        full_name: user.full_name,
      };
      await fulfillJson(route, {
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
        can_reactivate: false,
        can_deactivate: false,
      }, 201);
      return;
    }

    const userPlantAccessMatch = path.match(/\/backend\/auth\/api\/v1\/tenants\/SH00000001\/users\/([^/]+)\/plant-access$/);
    if (userPlantAccessMatch && method === "GET") {
      const user = state.users.find((entry) => entry.id === userPlantAccessMatch[1]);
      await fulfillJson(route, { plant_ids: clone(user?.plant_ids ?? []) });
      return;
    }

    const userUpdateMatch = path.match(/\/backend\/auth\/api\/v1\/tenants\/SH00000001\/users\/([^/]+)$/);
    if (userUpdateMatch && method === "PUT") {
      const user = state.users.find((entry) => entry.id === userUpdateMatch[1]);
      if (!user) {
        await fulfillJson(route, { message: "USER_NOT_FOUND" }, 404);
        return;
      }
      const body = request.postDataJSON();
      user.full_name = body.full_name ?? user.full_name;
      user.role = body.role ?? user.role;
      user.is_active = body.is_active ?? user.is_active;
      user.plant_ids = clone(body.plant_ids ?? user.plant_ids);
      user.lifecycle_state = user.is_active ? "active" : "deactivated";
      user.invite_status = user.is_active ? "none" : user.invite_status;
      await fulfillJson(route, {
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
        can_resend_invite: user.invite_status === "pending" || user.invite_status === "expired",
        can_reactivate: !user.is_active,
        can_deactivate: user.is_active,
      });
      return;
    }

    const userActionMatch = path.match(/\/backend\/auth\/api\/v1\/tenants\/SH00000001\/users\/([^/]+)\/(deactivate|reactivate|resend-invite)$/);
    if (userActionMatch) {
      const [, userId, action] = userActionMatch;
      const user = state.users.find((entry) => entry.id === userId);
      if (!user) {
        await fulfillJson(route, { message: "USER_NOT_FOUND" }, 404);
        return;
      }
      if (action === "deactivate" && method === "PATCH") {
        user.is_active = false;
        user.lifecycle_state = "deactivated";
        if (state.currentUserEmail === user.email) {
          state.currentUserEmail = null;
        }
        await fulfillJson(route, { message: "User deactivated" });
        return;
      }
      if (action === "reactivate" && method === "PATCH") {
        user.is_active = true;
        user.lifecycle_state = "active";
        user.invite_status = "none";
        await fulfillJson(route, { message: "User reactivated" });
        return;
      }
      if (action === "resend-invite" && method === "POST") {
        const tokenValue = `invite-${state.inviteCounter++}`;
        user.lifecycle_state = "invited";
        user.invite_status = "pending";
        state.tokens[tokenValue] = {
          type: "invite_set_password",
          status: "valid",
          email: user.email,
          full_name: user.full_name,
        };
        await fulfillJson(route, { message: "Invite resent" });
        return;
      }
    }

    if (path === "/backend/device/api/v1/devices/dashboard/summary" && method === "GET") {
      const activeAlerts = state.rulesByDevice.AD00000010?.length ? 1 : 0;
      await fulfillJson(route, {
        generated_at: iso(0),
        stale: false,
        warnings: [],
        summary: {
          total_devices: state.devices.length,
          system_health: state.devices.length ? 92 : 100,
        },
        alerts: { active_alerts: activeAlerts },
        devices: [],
        cost_data_state: "fresh",
        cost_data_reasons: [],
        cost_generated_at: iso(0),
        energy_widgets: {
          today_loss_kwh: 0,
          today_loss_cost_inr: 0,
          currency: "INR",
        },
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices" && method === "GET") {
      await fulfillJson(route, {
        success: true,
        data: clone(state.devices),
        total: state.devices.length,
        page: 1,
        page_size: 20,
        total_pages: 1,
      });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/hardware-units/list") && method === "GET") {
      await fulfillJson(route, { success: true, data: [], total: 0 });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/hardware-installations/history") && method === "GET") {
      await fulfillJson(route, { success: true, data: [], total: 0 });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/hardware-mappings") && method === "GET") {
      await fulfillJson(route, { success: true, data: [], total: 0 });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/dashboard/fleet-snapshot") && method === "GET") {
      const devices = listFleetItems(state);
      await fulfillJson(route, {
        generated_at: iso(0),
        total: devices.length,
        page: 1,
        page_size: 60,
        total_pages: 1,
        devices,
      });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/dashboard/fleet-stream") && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body:
          "id: 1\n" +
          "event: heartbeat\n" +
          `data: ${JSON.stringify({
            id: "1",
            event: "heartbeat",
            generated_at: iso(0),
            freshness_ts: iso(0),
            stale: false,
            warnings: [],
            devices: [],
            partial: false,
            version: 1,
          })}\n\n`,
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/onboard" && method === "POST") {
      const body = request.postDataJSON();
      if (String(body.device_name).toLowerCase().includes("conflict")) {
        await fulfillJson(route, { message: "DEVICE_CONFLICT: a machine with this name already exists." }, 409);
        return;
      }
      if (String(body.device_name).toLowerCase().includes("allocation")) {
        await fulfillJson(route, { message: "Unable to allocate a unique device ID." }, 500);
        return;
      }
      const plant = state.plants.find((entry) => entry.id === body.plant_id);
      if (!plant || !plant.is_active) {
        await fulfillJson(route, { message: "PLANT_INACTIVE: device onboarding is blocked for inactive plants." }, 409);
        return;
      }
      const deviceId = `AD000000${String(state.deviceCounter++).padStart(2, "0")}`;
      const device = {
        device_id: deviceId,
        device_name: body.device_name,
        device_type: body.device_type,
        plant_id: body.plant_id,
        runtime_status: "stopped",
        status: "active",
        location: body.location ?? null,
        first_telemetry_timestamp: null,
        last_seen_timestamp: null,
      };
      state.devices.unshift(device);
      await fulfillJson(route, {
        success: true,
        data: {
          device,
          mqtt: {
            broker_host: "broker.factory.local",
            broker_port: 1883,
            tenant_id: state.tenant.id,
            device_id: deviceId,
            username: `device:${state.tenant.id}:${deviceId}`,
            password: "one-time-mqtt-secret",
            publish_topic: `${state.tenant.id}/devices/${deviceId}/telemetry`,
            status_topic: `${state.tenant.id}/devices/${deviceId}/status`,
            subscribe_topics: [
              `${state.tenant.id}/devices/${deviceId}/cmd`,
              `${state.tenant.id}/devices/${deviceId}/config`,
              `${state.tenant.id}/devices/${deviceId}/ota`,
            ],
          },
        },
      }, 201);
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/dashboard-bootstrap" && method === "GET") {
      await fulfillJson(route, {
        generated_at: iso(0),
        version: 1,
        device: {
          device_id: "AD00000010",
          tenant_id: state.tenant.id,
          device_name: "Packaging Line A",
          device_type: "compressor",
          status: "active",
          runtime_status: "running",
          last_seen_timestamp: iso(-1),
          location: "Pune Bay 1",
          fla_current_amps: 15,
        },
        telemetry: [{ timestamp: iso(-1), current: 2.4, power: 120, voltage: 230 }],
        uptime: {
          shifts_configured: 0,
          uptime_percentage: null,
          total_planned_minutes: 0,
          total_effective_minutes: 0,
          actual_running_minutes: 0,
          message: "No active shift window right now.",
        },
        shifts: [],
        health_configs: [],
        health_score: {
          health_score: 92,
          status: "Healthy",
          status_color: "🟢",
          machine_state: "RUNNING",
          parameters_included: 1,
          parameters_skipped: 0,
          total_weight_configured: 100,
          parameter_scores: [],
        },
        widget_config: {
          selected_fields: ["power"],
          effective_fields: ["power"],
          available_fields: ["power"],
        },
        current_state: {
          machine_state: "RUNNING",
          load_state: "in_load",
          current_amps: 2.4,
        },
        idle_stats: null,
        idle_config: null,
        waste_config: null,
        loss_stats: {
          device_id: "AD00000010",
          day_bucket: "2026-05-02",
          last_telemetry_ts: iso(-1),
          updated_at: iso(-1),
          tariff_configured: true,
          currency: "INR",
          today: {
            idle_kwh: 0,
            idle_cost_inr: 0,
            off_hours_kwh: 0,
            off_hours_cost_inr: 0,
            overconsumption_kwh: 0,
            overconsumption_cost_inr: 0,
            total_loss_kwh: 0,
            total_loss_cost_inr: 0,
            today_energy_kwh: 0,
            today_energy_cost_inr: 0,
          },
        },
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010" && method === "GET") {
      await fulfillJson(route, {
        success: true,
        data: {
          device_id: "AD00000010",
          device_name: "Packaging Line A",
          device_type: "compressor",
          plant_id: "plant-1",
          runtime_status: "running",
          status: "active",
          location: "Pune Bay 1",
          first_telemetry_timestamp: iso(-30),
          last_seen_timestamp: iso(-1),
        },
      });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/AD00000010/performance-trends") && method === "GET") {
      await fulfillJson(route, { metric: "health", range: "1h", points: [], summary: null });
      return;
    }

    if (path.startsWith("/backend/data/api/v1/data/telemetry/AD00000010") && method === "GET") {
      await fulfillJson(route, {
        success: true,
        data: {
          items: [
            {
              timestamp: iso(-1),
              device_id: "AD00000010",
              power: 120,
              current: 2.4,
              voltage: 230,
            },
          ],
        },
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/idle-config" && method === "GET") {
      await fulfillJson(route, {
        device_id: "AD00000010",
        full_load_current_a: 15,
        idle_threshold_pct_of_fla: 25,
        derived_idle_threshold_a: 3.75,
        derived_overconsumption_threshold_a: 15,
        configured: true,
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/current-state" && method === "GET") {
      await fulfillJson(route, {
        device_id: "AD00000010",
        state: "running",
        current_band: "in_load",
        current: 2.4,
        voltage: 230,
        threshold: 3.75,
        full_load_current_a: 15,
        idle_threshold_pct_of_fla: 25,
        derived_idle_threshold_a: 3.75,
        derived_overconsumption_threshold_a: 15,
        timestamp: iso(-1),
        current_field: "current",
        voltage_field: "voltage",
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/loss-stats" && method === "GET") {
      await fulfillJson(route, {
        device_id: "AD00000010",
        day_bucket: "2026-05-02",
        last_telemetry_ts: iso(-1),
        updated_at: iso(-1),
        tariff_configured: true,
        currency: "INR",
        today: {
          idle_kwh: 0,
          idle_cost_inr: 0,
          off_hours_kwh: 0,
          off_hours_cost_inr: 0,
          overconsumption_kwh: 0,
          overconsumption_cost_inr: 0,
          total_loss_kwh: 0,
          total_loss_cost_inr: 0,
          today_energy_kwh: 0,
          today_energy_cost_inr: 0,
        },
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/dashboard-widgets" && method === "GET") {
      await fulfillJson(route, {
        device_id: "AD00000010",
        available_fields: ["power", "current", "voltage"],
        selected_fields: ["power"],
        effective_fields: ["power"],
        default_applied: false,
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/shifts" && method === "GET") {
      await fulfillJson(route, { data: [] });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/uptime" && method === "GET") {
      await fulfillJson(route, {
        device_id: "AD00000010",
        uptime_percentage: null,
        total_planned_minutes: 0,
        total_effective_minutes: 0,
        actual_running_minutes: 0,
        shifts_configured: 0,
        message: "No active shift window right now.",
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/health-config" && method === "GET") {
      await fulfillJson(route, { data: [] });
      return;
    }

    if (path === "/backend/data/api/v1/devices/AD00000010/fields" && method === "GET") {
      await fulfillJson(route, { fields: ["power", "current"] });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/mqtt-credential" && method === "GET") {
      await fulfillJson(route, { message: "DEVICE_MQTT_CREDENTIAL_NOT_FOUND" }, 404);
      return;
    }

    if (path === "/backend/rule-engine/api/v1/rules" && method === "GET") {
      await fulfillJson(route, {
        data: clone(state.rulesByDevice.AD00000010 ?? []),
        total: (state.rulesByDevice.AD00000010 ?? []).length,
      });
      return;
    }

    if (path === "/backend/rule-engine/api/v1/rules" && method === "POST") {
      const body = request.postDataJSON();
      const rule = {
        rule_id: `rule-${state.ruleCounter++}`,
        rule_name: body.rule_name,
        description: body.description ?? null,
        rule_type: body.rule_type ?? "threshold",
        scope: body.scope,
        property: body.property ?? null,
        condition: body.condition ?? null,
        threshold: body.threshold ?? null,
        time_window_start: body.time_window_start ?? null,
        time_window_end: body.time_window_end ?? null,
        timezone: body.timezone ?? "Asia/Kolkata",
        time_condition: body.time_condition ?? null,
        duration_minutes: body.duration_minutes ?? null,
        notification_channels: clone(body.notification_channels ?? []),
        notification_recipients: clone(body.notification_recipients ?? []),
        cooldown_minutes: body.cooldown_minutes ?? 15,
        cooldown_seconds: body.cooldown_seconds ?? 900,
        cooldown_unit: body.cooldown_unit ?? "minutes",
        cooldown_mode: body.cooldown_mode ?? "interval",
        device_ids: clone(body.device_ids ?? ["AD00000010"]),
        status: "active",
        created_at: iso(0),
        updated_at: iso(0),
      };
      state.rulesByDevice.AD00000010 = [rule, ...(state.rulesByDevice.AD00000010 ?? [])];
      await fulfillJson(route, { data: rule }, 201);
      return;
    }

    const ruleMatch = path.match(/\/backend\/rule-engine\/api\/v1\/rules\/([^/]+)$/);
    if (ruleMatch && method === "PUT") {
      if (state.ruleMutationDenied) {
        state.ruleMutationDenied = false;
        await fulfillJson(route, { message: "Forbidden: you cannot modify rules outside your scope." }, 403);
        return;
      }
      const body = request.postDataJSON();
      state.rulesByDevice.AD00000010 = (state.rulesByDevice.AD00000010 ?? []).map((rule) =>
        rule.rule_id === ruleMatch[1]
          ? {
              ...rule,
              rule_name: body.rule_name ?? rule.rule_name,
              description: body.description ?? rule.description,
              rule_type: body.rule_type ?? rule.rule_type,
              property: body.property ?? rule.property,
              condition: body.condition ?? rule.condition,
              threshold: body.threshold ?? rule.threshold,
              time_window_start: body.time_window_start ?? rule.time_window_start,
              time_window_end: body.time_window_end ?? rule.time_window_end,
              duration_minutes: body.duration_minutes ?? rule.duration_minutes,
              notification_channels: clone(body.notification_channels ?? rule.notification_channels),
              notification_recipients: clone(body.notification_recipients ?? rule.notification_recipients),
              cooldown_minutes: body.cooldown_minutes ?? rule.cooldown_minutes,
              cooldown_seconds: body.cooldown_seconds ?? rule.cooldown_seconds,
              cooldown_unit: body.cooldown_unit ?? rule.cooldown_unit,
              cooldown_mode: body.cooldown_mode ?? rule.cooldown_mode,
              updated_at: iso(0),
            }
          : rule,
      );
      const updated = (state.rulesByDevice.AD00000010 ?? []).find((rule) => rule.rule_id === ruleMatch[1]);
      await fulfillJson(route, { data: updated });
      return;
    }

    if (ruleMatch && method === "DELETE") {
      if (state.ruleMutationDenied) {
        state.ruleMutationDenied = false;
        await fulfillJson(route, { message: "Forbidden: you cannot modify rules outside your scope." }, 403);
        return;
      }
      state.rulesByDevice.AD00000010 = (state.rulesByDevice.AD00000010 ?? []).filter((rule) => rule.rule_id !== ruleMatch[1]);
      await fulfillJson(route, { data: { deleted: true } });
      return;
    }

    const ruleStatusMatch = path.match(/\/backend\/rule-engine\/api\/v1\/rules\/([^/]+)\/status$/);
    if (ruleStatusMatch && method === "PATCH") {
      if (state.ruleMutationDenied) {
        state.ruleMutationDenied = false;
        await fulfillJson(route, { message: "Forbidden: you cannot modify rules outside your scope." }, 403);
        return;
      }
      const body = request.postDataJSON();
      state.rulesByDevice.AD00000010 = (state.rulesByDevice.AD00000010 ?? []).map((rule) =>
        rule.rule_id === ruleStatusMatch[1] ? { ...rule, status: body.status, updated_at: iso(0) } : rule,
      );
      await fulfillJson(route, { data: { updated: true } });
      return;
    }

    if (path.startsWith("/backend/rule-engine/api/v1/alerts/events/unread-count") && method === "GET") {
      await fulfillJson(route, { data: { count: 1 } });
      return;
    }

    if (path.startsWith("/backend/rule-engine/api/v1/alerts/events") && method === "GET") {
      await fulfillJson(route, {
        data: [makeActivityEvent()],
        total: 1,
        page: 1,
        page_size: 25,
        total_pages: 1,
      });
      return;
    }

    if (path.startsWith("/backend/rule-engine/api/v1/alerts/events/mark-all-read") && method === "PATCH") {
      await fulfillJson(route, { data: { updated: 1 } });
      return;
    }

    if (path.startsWith("/backend/rule-engine/api/v1/alerts/events") && method === "DELETE") {
      await fulfillJson(route, { data: { deleted: 1 } });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/maintenance-log/summary" && method === "GET") {
      const records = state.maintenanceByDevice.AD00000010 ?? [];
      await fulfillJson(route, { success: true, data: summarizeMaintenance(records) });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/maintenance-log" && method === "GET") {
      await fulfillJson(route, { success: true, data: clone(state.maintenanceByDevice.AD00000010 ?? []) });
      return;
    }

    if (path === "/backend/device/api/v1/devices/AD00000010/maintenance-log" && method === "POST") {
      const body = request.postDataJSON();
      const nextId = Math.max(0, ...(state.maintenanceByDevice.AD00000010 ?? []).map((record) => record.id)) + 1;
      const record = {
        id: nextId,
        tenant_id: state.tenant.id,
        device_id: "AD00000010",
        maintenance_date: body.maintenance_date,
        title: body.title,
        description: body.description,
        cost: Number(body.cost),
        performed_by: body.performed_by ?? null,
        status: body.status ?? null,
        next_due_date: body.next_due_date ?? null,
        created_by: currentUser(state)?.id ?? "user-org-admin",
        created_at: iso(0),
        updated_at: iso(0),
      };
      state.maintenanceByDevice.AD00000010 = [record, ...(state.maintenanceByDevice.AD00000010 ?? [])];
      await fulfillJson(route, { success: true, data: record }, 201);
      return;
    }

    const maintenanceMatch = path.match(/\/backend\/device\/api\/v1\/devices\/AD00000010\/maintenance-log\/(\d+)$/);
    if (maintenanceMatch && method === "PUT") {
      const recordId = Number(maintenanceMatch[1]);
      const body = request.postDataJSON();
      state.maintenanceByDevice.AD00000010 = (state.maintenanceByDevice.AD00000010 ?? []).map((record) =>
        record.id === recordId
          ? {
              ...record,
              maintenance_date: body.maintenance_date,
              title: body.title,
              description: body.description,
              cost: Number(body.cost),
              performed_by: body.performed_by ?? null,
              status: body.status ?? null,
              next_due_date: body.next_due_date ?? null,
              updated_at: iso(0),
            }
          : record,
      );
      const updated = (state.maintenanceByDevice.AD00000010 ?? []).find((record) => record.id === recordId);
      await fulfillJson(route, { success: true, data: updated });
      return;
    }

    if (maintenanceMatch && method === "DELETE") {
      if (state.nextMaintenanceDeleteNotFound) {
        state.nextMaintenanceDeleteNotFound = false;
        await fulfillJson(route, { message: "maintenance_log_not_found" }, 404);
        return;
      }
      const recordId = Number(maintenanceMatch[1]);
      state.maintenanceByDevice.AD00000010 = (state.maintenanceByDevice.AD00000010 ?? []).filter((record) => record.id !== recordId);
      await fulfillJson(route, { success: true, maintenance_log_id: recordId, message: "deleted" });
      return;
    }

    if (path.startsWith("/backend/") || path.startsWith("/api/")) {
      await fulfillJson(route, { message: `UNMOCKED_API ${method} ${path}` }, 500);
      return;
    }

    await route.fallback();
  });

  return {
    getInviteTokenForEmail(email) {
      const entry = Object.entries(state.tokens).find(([, value]) => value.email === email && value.type === "invite_set_password" && value.status === "valid");
      return entry?.[0] ?? null;
    },
    denyNextRuleMutation() {
      state.ruleMutationDenied = true;
    },
    failNextMaintenanceDeleteAsMissing() {
      state.nextMaintenanceDeleteNotFound = true;
    },
  };
}

module.exports = {
  installPhase3Harness,
};
