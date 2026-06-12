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
      id: "user-1",
      email: "admin@example.com",
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
        super_admin: ["machines", "analytics", "reports", "rules", "settings", "copilot", "calendar", "waste_analysis"],
        org_admin: ["machines"],
        plant_manager: [],
        operator: [],
        viewer: [],
      },
      baseline_features_by_role: {
        super_admin: ["machines", "analytics", "reports", "rules", "settings", "copilot", "calendar", "waste_analysis"],
        org_admin: ["machines"],
        plant_manager: [],
        operator: [],
        viewer: [],
      },
      effective_features_by_role: {
        super_admin: ["machines", "analytics", "reports", "rules", "settings", "copilot", "calendar", "waste_analysis"],
        org_admin: ["machines"],
        plant_manager: [],
        operator: [],
        viewer: [],
      },
      available_features: ["machines", "analytics", "reports", "rules", "settings", "copilot", "calendar", "waste_analysis"],
      entitlements_version: 1,
    },
  };
}

test("super admin can manage org hardware inventory, installation, decommission, and history from the org detail page", async ({ page }) => {
  const me = buildSuperAdminMe();
  const orgs = [
    {
      id: "SH00000001",
      name: "Factory Ops",
      slug: "factory-ops",
      is_active: true,
      created_at: "2026-04-07T00:00:00Z",
    },
  ];
  const plants = [
    {
      id: "plant-1",
      tenant_id: "SH00000001",
      name: "Plant One",
      location: "Pune",
      timezone: "Asia/Kolkata",
      is_active: true,
      created_at: "2026-04-07T00:00:00Z",
    },
    {
      id: "plant-2",
      tenant_id: "SH00000001",
      name: "Plant Two",
      location: "Mumbai",
      timezone: "Asia/Kolkata",
      is_active: true,
      created_at: "2026-04-07T00:00:00Z",
    },
  ];
  const users = [
    {
      id: "org-admin-1",
      email: "org-admin@example.com",
      full_name: "Org Admin",
      role: "org_admin",
      tenant_id: "SH00000001",
      is_active: true,
      created_at: "2026-04-07T00:00:00Z",
      last_login_at: null,
    },
  ];
  const devices = [
    {
      device_id: "AD00000001",
      device_name: "Compressor Alpha",
      device_type: "compressor",
      device_id_class: "active",
      plant_id: "plant-1",
      data_source_type: "metered",
      status: "active",
      runtime_status: "running",
      last_seen_timestamp: null,
      location: "Line A",
    },
    {
      device_id: "AD00000002",
      device_name: "Compressor Beta",
      device_type: "compressor",
      device_id_class: "active",
      plant_id: "plant-1",
      data_source_type: "sensor",
      status: "active",
      runtime_status: "stopped",
      last_seen_timestamp: null,
      location: "Line B",
    },
    {
      device_id: "TD00000001",
      device_name: "Test Bench",
      device_type: "compressor",
      device_id_class: "test",
      plant_id: "plant-2",
      data_source_type: "metered",
      status: "active",
      runtime_status: "running",
      last_seen_timestamp: null,
      location: "Lab",
    },
  ];
  const hardwareUnits = [
    {
      id: 1,
      hardware_unit_id: "HW-METER-001",
      tenant_id: "SH00000001",
      plant_id: "plant-1",
      unit_type: "energy_meter",
      unit_name: "Main Energy Meter",
      manufacturer: "Schneider",
      model: "PM5000",
      serial_number: "SER-001",
      status: "available",
      created_at: "2026-04-07T00:00:00Z",
      updated_at: "2026-04-07T00:00:00Z",
    },
    {
      id: 2,
      hardware_unit_id: "HW-CT-001",
      tenant_id: "SH00000001",
      plant_id: "plant-2",
      unit_type: "ct_sensor",
      unit_name: "CT1",
      manufacturer: "ABB",
      model: "CT-200",
      serial_number: "SER-002",
      status: "available",
      created_at: "2026-04-07T00:00:00Z",
      updated_at: "2026-04-07T00:00:00Z",
    },
  ];
  const installationHistory = {
    AD00000001: [
      {
        id: 101,
        tenant_id: "SH00000001",
        plant_id: "plant-1",
        device_id: "AD00000001",
        hardware_unit_id: "HW-METER-001",
        installation_role: "main_meter",
        commissioned_at: "2026-04-07T09:00:00Z",
        decommissioned_at: null,
        is_active: true,
        notes: "Primary meter",
        created_at: "2026-04-07T09:00:00Z",
        updated_at: "2026-04-07T09:00:00Z",
      },
    ],
    AD00000002: [],
    TD00000001: [],
  };

  function currentMappings() {
    return Object.values(installationHistory)
      .flat()
      .filter((row) => row.is_active)
      .map((row) => {
        const unit = hardwareUnits.find((item) => item.hardware_unit_id === row.hardware_unit_id);
        const plant = plants.find((item) => item.id === row.plant_id);
        return {
          device_id: row.device_id,
          plant_id: row.plant_id,
          plant_name: plant?.name || row.plant_id,
          installation_role: row.installation_role,
          installation_role_label: row.installation_role === "main_meter" ? "Main Meter" : row.installation_role === "controller" ? "Controller" : row.installation_role.toUpperCase(),
          hardware_unit_id: row.hardware_unit_id,
          hardware_type: unit?.unit_type || "",
          hardware_type_label: unit?.unit_type === "energy_meter" ? "Energy Meter" : unit?.unit_type === "ct_sensor" ? "CT Sensor" : unit?.unit_type === "esp32" ? "ESP32" : unit?.unit_type,
          hardware_name: unit?.unit_name || "",
          manufacturer: unit?.manufacturer || null,
          model: unit?.model || null,
          serial_number: unit?.serial_number || null,
          status: "Active",
          is_active: true,
        };
      });
  }

  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem("factoryops_access_token", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_selected_tenant", "SH00000001");
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot.me));
  }, {
    accessToken: `header.${base64Json({ role: "super_admin", tenant_id: null, exp: Math.floor(Date.now() / 1000) + 3600 })}.signature`,
    me,
  });

  await page.route("**/backend/auth/api/v1/auth/me", async (route) => {
    await fulfillJson(route, me);
  });
  await page.route("**/backend/auth/api/v1/auth/refresh", async (route) => {
    await fulfillJson(route, {
      access_token: `header.${base64Json({ role: "super_admin", tenant_id: null, exp: Math.floor(Date.now() / 1000) + 3600 })}.signature`,
      token_type: "bearer",
      expires_in: 3600,
    });
  });
  await page.route("**/backend/auth/api/v1/platform-maintenance/current", async (route) => {
    await fulfillJson(route, {
      tenant_id: "SH00000001",
      announcements: [],
    });
  });
  await page.route("**/backend/auth/api/admin/tenants", async (route) => {
    await fulfillJson(route, orgs);
  });
  await page.route("**/backend/auth/api/v1/tenants/SH00000001/plants", async (route) => {
    await fulfillJson(route, plants);
  });
  await page.route("**/backend/auth/api/v1/tenants/SH00000001/users", async (route) => {
    await fulfillJson(route, users);
  });
  await page.route("**/backend/auth/api/v1/tenants/SH00000001/entitlements", async (route) => {
    await fulfillJson(route, me.entitlements);
  });
  await page.route("**/backend/device/api/v1/devices", async (route) => {
    if (route.request().method() === "GET") {
      await fulfillJson(route, { success: true, data: devices, total: devices.length, page: 1, page_size: 20, total_pages: 1 });
      return;
    }
    await route.abort();
  });
  await page.route("**/backend/device/api/v1/devices/hardware-units/list**", async (route) => {
    const url = new URL(route.request().url());
    const plantId = url.searchParams.get("plant_id");
    const filtered = plantId ? hardwareUnits.filter((unit) => unit.plant_id === plantId) : hardwareUnits;
    await fulfillJson(route, { success: true, data: filtered, total: filtered.length });
  });
  await page.route("**/backend/device/api/v1/devices/hardware-units", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    const payload = route.request().postDataJSON();
    const created = {
      id: hardwareUnits.length + 1,
      hardware_unit_id: `HWU${String(hardwareUnits.length + 1).padStart(8, "0")}`,
      tenant_id: "SH00000001",
      created_at: "2026-04-07T11:00:00Z",
      updated_at: "2026-04-07T11:00:00Z",
      manufacturer: null,
      model: null,
      serial_number: null,
      status: "available",
      ...payload,
    };
    hardwareUnits.push(created);
    await fulfillJson(route, { success: true, data: created }, 201);
  });
  await page.route("**/backend/device/api/v1/devices/hardware-units/*", async (route) => {
    if (route.request().method() !== "PUT") {
      await route.fallback();
      return;
    }
    const hardwareUnitId = decodeURIComponent(route.request().url().split("/").pop());
    const payload = route.request().postDataJSON();
    const target = hardwareUnits.find((unit) => unit.hardware_unit_id === hardwareUnitId);
    Object.assign(target, payload, { updated_at: "2026-04-07T12:00:00Z" });
    await fulfillJson(route, { success: true, data: target });
  });
  await page.route("**/backend/device/api/v1/devices/hardware-installations/history**", async (route) => {
    const rows = Object.values(installationHistory).flat();
    await fulfillJson(route, { success: true, data: rows, total: rows.length });
  });
  await page.route("**/backend/device/api/v1/devices/hardware-mappings**", async (route) => {
    const url = new URL(route.request().url());
    const plantId = url.searchParams.get("plant_id");
    const deviceId = url.searchParams.get("device_id");
    const rows = currentMappings().filter((row) => (!plantId || row.plant_id === plantId) && (!deviceId || row.device_id === deviceId));
    await fulfillJson(route, { success: true, data: rows, total: rows.length });
  });
  await page.route("**/backend/device/api/v1/devices/*/hardware-installations", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    const deviceId = decodeURIComponent(route.request().url().split("/devices/")[1].split("/hardware-installations")[0]);
    const payload = route.request().postDataJSON();
    const targetUnit = hardwareUnits.find((unit) => unit.hardware_unit_id === payload.hardware_unit_id);
    const installation = {
      id: 200 + Object.values(installationHistory).flat().length + 1,
      tenant_id: "SH00000001",
      plant_id: targetUnit.plant_id,
      device_id: deviceId,
      hardware_unit_id: payload.hardware_unit_id,
      installation_role: payload.installation_role,
      commissioned_at: payload.commissioned_at || "2026-04-07T13:00:00Z",
      decommissioned_at: null,
      is_active: true,
      notes: payload.notes || null,
      created_at: "2026-04-07T13:00:00Z",
      updated_at: "2026-04-07T13:00:00Z",
    };
    installationHistory[deviceId] = [installation, ...(installationHistory[deviceId] || [])];
    await fulfillJson(route, { success: true, data: installation }, 201);
  });
  await page.route("**/backend/device/api/v1/devices/hardware-installations/*/decommission", async (route) => {
    const installationId = Number(route.request().url().split("/hardware-installations/")[1].split("/decommission")[0]);
    const payload = route.request().postDataJSON();
    let targetInstallation = null;
    for (const rows of Object.values(installationHistory)) {
      const match = rows.find((row) => row.id === installationId);
      if (match) {
        targetInstallation = match;
        break;
      }
    }
    targetInstallation.decommissioned_at = payload.decommissioned_at || "2026-04-07T14:00:00Z";
    targetInstallation.is_active = false;
    targetInstallation.notes = payload.notes || targetInstallation.notes;
    await fulfillJson(route, { success: true, data: targetInstallation });
  });

  await page.goto("/admin/orgs/SH00000001");

  await expect(page.getByRole("button", { name: "Hardware" })).toBeVisible();
  await page.getByRole("button", { name: "Hardware" }).click();

  await expect(page.getByText("Hardware inventory")).toBeVisible();
  await expect(page.getByText("Current device mappings")).toBeVisible();
  const meterRow = page.locator("tr", { hasText: "HW-METER-001" }).first();
  await expect(meterRow).toBeVisible();
  await expect(meterRow.getByText("AD00000001")).toBeVisible();
  await expect(page.locator("tr", { hasText: "AD00000001" }).filter({ hasText: "Main Meter" }).first()).toBeVisible();

  await page.getByLabel("Filter hardware by plant").selectOption("plant-2");
  await expect(page.locator("tr", { hasText: "HW-CT-001" }).first()).toBeVisible();
  await expect(page.locator("tr", { hasText: "HW-METER-001" }).first()).toHaveCount(0);
  await page.getByLabel("Filter hardware by plant").selectOption("all");

  await page.getByRole("button", { name: "Add hardware" }).click();
  const createDialog = page.getByRole("dialog", { name: "Create hardware unit" });
  await createDialog.getByLabel("Plant").selectOption("plant-1");
  await expect(createDialog.getByText("Track the hardware category, this unit's label, and the plant that owns the inventory record.")).toBeVisible();
  await createDialog.getByLabel("Unit type").selectOption("esp32");
  await createDialog.getByLabel("Unit name").fill("ESP32 Main");
  await createDialog.getByLabel("Manufacturer").fill("Espressif");
  await createDialog.getByLabel("Model").fill("ESP32-WROOM");
  await createDialog.getByLabel("Serial number").fill("SER-003");
  await createDialog.getByRole("button", { name: "Create hardware" }).click();

  await expect(page.getByText("Hardware unit HWU00000003 created.")).toBeVisible();
  const espRow = page.locator("tr", { hasText: "HWU00000003" }).first();
  await expect(espRow).toBeVisible();
  await expect(espRow.getByRole("cell", { name: "ESP32", exact: true })).toBeVisible();
  await espRow.getByRole("button", { name: "Edit" }).click();
  const editDialog = page.getByRole("dialog", { name: "Edit hardware unit" });
  await expect(editDialog.getByLabel("Hardware unit ID")).toHaveValue("HWU00000003");
  await expect(editDialog.getByLabel("Unit type")).toHaveValue("esp32");
  await editDialog.getByLabel("Unit name").fill("ESP32 Main Updated");
  await editDialog.getByLabel("Manufacturer").fill("Espressif Systems");
  await editDialog.getByRole("button", { name: "Save changes" }).click();

  await expect(page.getByText("Hardware unit updated.")).toBeVisible();
  await expect(espRow.getByText("ESP32 Main Updated")).toBeVisible();
  await expect(espRow.getByText("Espressif Systems / ESP32-WROOM")).toBeVisible();

  await espRow.getByRole("button", { name: "Install" }).click();
  const installDialog = page.getByRole("dialog", { name: "Install hardware on device" });
  await expect(installDialog.getByText("Assign this hardware unit to a device role. Only devices from Plant One are available for selection.")).toBeVisible();
  await expect(installDialog.getByLabel("Plant")).toHaveValue("Plant One");
  await expect(installDialog.getByLabel("Unit type")).toHaveValue("ESP32");
  await expect(installDialog.getByLabel("Unit name")).toHaveValue("ESP32 Main Updated");
  await expect(installDialog.getByLabel("Device")).toContainText("AD00000002 · Compressor Beta");
  await installDialog.getByLabel("Device").selectOption("AD00000002");
  await installDialog.getByLabel("Installation role").selectOption("controller");
  await installDialog.getByLabel("Commissioned at").fill("2026-04-07T18:30");
  await installDialog.getByRole("button", { name: "Install hardware" }).evaluate((button) => button.click());

  await expect(page.getByText("Hardware installed on device.")).toBeVisible();
  await expect(espRow.getByText("AD00000002")).toBeVisible();
  await expect(espRow.getByText("role Controller")).toBeVisible();
  const mappingSection = page.locator("section", { hasText: "Current device mappings" });
  const mappingRow = mappingSection.locator("tr", { hasText: "AD00000002" }).filter({ hasText: "Controller" }).first();
  await expect(mappingRow.getByText("Plant One")).toBeVisible();
  await expect(mappingRow.getByText("HWU00000003")).toBeVisible();
  await expect(mappingRow.getByRole("cell", { name: "ESP32", exact: true })).toBeVisible();
  await expect(mappingRow.getByText("ESP32 Main Updated")).toBeVisible();

  await page.getByLabel("Filter history by hardware unit").selectOption("HWU00000003");
  const espHistoryRow = page.locator("tr", { hasText: "HWU00000003" }).last();
  await expect(espHistoryRow.getByText("AD00000002")).toBeVisible();
  await expect(espHistoryRow.getByText("Plant One")).toBeVisible();
  await expect(espHistoryRow.getByText("Controller")).toBeVisible();
  await expect(espHistoryRow.getByText("Active")).toBeVisible();

  await espRow.getByRole("button", { name: "Decommission" }).click();
  const decommissionDialog = page.getByRole("dialog", { name: "Decommission installation" });
  await decommissionDialog.getByLabel("Decommissioned at").fill("2026-04-07T19:00");
  await decommissionDialog.getByLabel("Notes").fill("Removed after validation");
  await decommissionDialog.getByRole("button", { name: "Decommission" }).click();

  await expect(page.getByText("Installation decommissioned.")).toBeVisible();
  await expect(espRow.getByText("Not currently assigned")).toBeVisible();
  await expect(mappingSection.locator("tr", { hasText: "AD00000002" }).filter({ hasText: "HWU00000003" })).toHaveCount(0);
  await page.getByLabel("Filter history by state").selectOption("decommissioned");
  await expect(espHistoryRow.getByText("Decommissioned")).toBeVisible();
  await expect(espHistoryRow.getByText("Removed after validation")).toBeVisible();
});

test("non super-admin users cannot access the admin org hardware page", async ({ page }) => {
  const me = {
    ...buildSuperAdminMe(),
    user: {
      id: "user-2",
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
      created_at: "2026-04-07T00:00:00Z",
    },
    plant_ids: ["plant-1"],
  };

  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem("factoryops_access_token", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot.me));
  }, {
    accessToken: `header.${base64Json({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 })}.signature`,
    me,
  });

  await page.route("**/backend/auth/api/v1/auth/me", async (route) => {
    await fulfillJson(route, me);
  });
  await page.route("**/backend/auth/api/v1/auth/refresh", async (route) => {
    await fulfillJson(route, {
      access_token: `header.${base64Json({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 })}.signature`,
      token_type: "bearer",
      expires_in: 3600,
    });
  });
  await page.route("**/backend/auth/api/v1/platform-maintenance/current", async (route) => {
    await fulfillJson(route, {
      tenant_id: "SH00000001",
      announcements: [],
    });
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/summary", async (route) => {
    await fulfillJson(route, {
      generated_at: new Date().toISOString(),
      stale: false,
      warnings: [],
      summary: { total_devices: 0, system_health: 100 },
      alerts: { active_alerts: 0 },
      devices: [],
      cost_data_state: "fresh",
      cost_data_reasons: [],
      cost_generated_at: null,
      energy_widgets: { today_loss_kwh: 0, today_loss_cost_inr: 0, currency: "INR" },
    });
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/fleet-snapshot**", async (route) => {
    await fulfillJson(route, {
      generated_at: new Date().toISOString(),
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
        "id: 1\n" +
        "event: heartbeat\n" +
        'data: {"id":"1","event":"heartbeat","generated_at":"2026-04-02T00:00:00.000Z","freshness_ts":"2026-04-02T00:00:00.000Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":0}\n\n',
    });
  });
  await page.route("**/backend/device/api/v1/devices", async (route) => {
    await fulfillJson(route, { success: true, data: [], total: 0, page: 1, page_size: 20, total_pages: 1 });
  });

  await page.goto("/admin/orgs/SH00000001");

  await page.waitForURL("**/machines");
  await expect(page).toHaveURL(/\/machines$/);
});
