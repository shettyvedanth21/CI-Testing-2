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

function fieldValue(page, label) {
  return page.locator("div.space-y-1").filter({ has: page.getByText(label, { exact: true }) }).locator("div").last();
}

function modalFieldSelect(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::select[1]");
}

function expectedQrPayload({ mqttHost, mqttPort, createdDeviceId, mqttUsername, mqttPassword }) {
  return JSON.stringify({
    version: 1,
    broker: mqttHost,
    port: mqttPort,
    tenant_id: "SH00000001",
    device_id: createdDeviceId,
    username: mqttUsername,
    password: mqttPassword,
    topic: `SH00000001/devices/${createdDeviceId}/telemetry`,
    status_topic: `SH00000001/devices/${createdDeviceId}/status`,
    subscribe_topics: [
      `SH00000001/devices/${createdDeviceId}/cmd`,
      `SH00000001/devices/${createdDeviceId}/config`,
      `SH00000001/devices/${createdDeviceId}/ota`,
    ],
  });
}

test("device onboarding generates and displays the device ID after create", async ({ page }) => {
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
    plant_ids: ["plant-1"],
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

  const createdDeviceId = "AD00000001";
  const mqttUsername = `device:SH00000001:${createdDeviceId}`;
  const mqttPassword = "one-time-mqtt-secret";
  const mqttHost = "broker.factory.local";
  const mqttPort = 1883;
  let createRequestBody = null;
  let onboardCallCount = 0;

  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem("factoryops_access_token", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_selected_tenant", "SH00000001");
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
      refresh_token: "refresh-token",
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
  await page.route("**/backend/auth/api/v1/tenants/SH00000001/plants", async (route) => {
    await fulfillJson(route, [{
      id: "plant-1",
      tenant_id: "SH00000001",
      name: "Plant One",
      location: "Building A",
      timezone: "Asia/Kolkata",
      is_active: true,
      created_at: new Date().toISOString(),
    }]);
  });
  await page.route("**/backend/device/api/v1/devices**", async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (method === "POST" && url.endsWith("/backend/device/api/v1/devices/onboard")) {
      onboardCallCount += 1;
      createRequestBody = route.request().postDataJSON();
      await fulfillJson(route, {
        success: true,
        data: {
          device: {
            device_id: createdDeviceId,
            device_name: "Compressor Line A",
            device_type: "compressor",
            device_id_class: "active",
            data_source_type: "metered",
            status: "active",
            runtime_status: "stopped",
            last_seen_timestamp: null,
            location: "Building A",
          },
          mqtt: {
            broker_host: mqttHost,
            broker_port: mqttPort,
            tenant_id: "SH00000001",
            device_id: createdDeviceId,
            username: mqttUsername,
            password: mqttPassword,
            publish_topic: `SH00000001/devices/${createdDeviceId}/telemetry`,
            status_topic: `SH00000001/devices/${createdDeviceId}/status`,
            subscribe_topics: [
              `SH00000001/devices/${createdDeviceId}/cmd`,
              `SH00000001/devices/${createdDeviceId}/config`,
              `SH00000001/devices/${createdDeviceId}/ota`,
            ],
          },
        },
      }, 201);
      return;
    }

    if (method === "GET" && url.includes(`/backend/device/api/v1/devices/${createdDeviceId}/mqtt-credential`)) {
      await fulfillJson(route, {
        success: true,
        data: {
          id: 42,
          tenant_id: "SH00000001",
          device_id: createdDeviceId,
          mqtt_username: mqttUsername,
          password_algorithm: "sha256",
          publish_topic: `SH00000001/devices/${createdDeviceId}/telemetry`,
          status_topic: `SH00000001/devices/${createdDeviceId}/status`,
          subscribe_topic: `SH00000001/devices/${createdDeviceId}/cmd`,
          subscribe_topics: [
            `SH00000001/devices/${createdDeviceId}/cmd`,
            `SH00000001/devices/${createdDeviceId}/config`,
            `SH00000001/devices/${createdDeviceId}/ota`,
          ],
          chip_id: null,
          is_active: true,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          rotated_at: null,
          revoked_at: null,
          acl_entries: [],
        },
      });
      return;
    }

    await route.fallback();
  });
  await page.route("**/backend/device/api/v1/devices/dashboard/summary", async (route) => {
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
  await page.route("**/backend/rule-engine/api/v1/alerts/events/unread-count**", async (route) => {
    await fulfillJson(route, { data: { count: 0 } });
  });
  await page.route("**/backend/rule-engine/api/v1/alerts/events**", async (route) => {
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
  await page.getByRole("button", { name: "+ Add Device" }).click();
  const modal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });

  await expect(page.locator("label").filter({ hasText: /^Device ID$/ })).toHaveCount(0);
  await expect(page.locator("label").filter({ hasText: /^Device ID Class \*$/ })).toBeVisible();
  await expect(page.getByText("MQTT topics after provisioning:")).toBeVisible();

  await modal.locator('input[placeholder="e.g. Compressor Line A"]').fill("Compressor Line A");
  await modalFieldSelect(modal, "Plant").selectOption("plant-1");
  await modal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(modal, "Device ID Class").selectOption("active");
  await modal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await modal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await modal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Building A");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();

  expect(createRequestBody).toBeTruthy();
  expect(createRequestBody.device_id).toBeUndefined();
  expect(createRequestBody.device_id_class).toBe("active");
  expect(onboardCallCount).toBe(1);
  await expect(page.getByText("Generated Device ID", { exact: true })).toBeVisible();
  await expect(fieldValue(page, "Generated Device ID")).toHaveText(createdDeviceId);
  await expect(page.getByText("Broker Host", { exact: true })).toBeVisible();
  await expect(fieldValue(page, "Broker Host")).toHaveText(mqttHost);
  await expect(page.getByText("Port", { exact: true })).toBeVisible();
  await expect(fieldValue(page, "Port")).toHaveText(String(mqttPort));
  await expect(page.getByText("Tenant ID", { exact: true })).toBeVisible();
  await expect(fieldValue(page, "Tenant ID")).toHaveText("SH00000001");
  await expect(fieldValue(page, "Canonical Publish Topic")).toHaveText(`SH00000001/devices/${createdDeviceId}/telemetry`);
  await expect(fieldValue(page, "Status Publish Topic")).toHaveText(`SH00000001/devices/${createdDeviceId}/status`);
  await expect(fieldValue(page, "Control Subscribe Topics")).toContainText(`SH00000001/devices/${createdDeviceId}/cmd`);
  await expect(fieldValue(page, "Control Subscribe Topics")).toContainText(`SH00000001/devices/${createdDeviceId}/config`);
  await expect(fieldValue(page, "Control Subscribe Topics")).toContainText(`SH00000001/devices/${createdDeviceId}/ota`);
  await expect(page.getByText("MQTT Username", { exact: true })).toBeVisible();
  await expect(fieldValue(page, "MQTT Username")).toHaveText(mqttUsername);
  await expect(page.getByText("One-Time MQTT Password", { exact: true })).toBeVisible();
  await expect(fieldValue(page, "One-Time MQTT Password")).toHaveText(mqttPassword);
  await expect(page.getByText("Provisioning QR", { exact: true })).toBeVisible();
  await expect(page.getByTestId("mqtt-provisioning-qr")).toBeVisible();
  await expect(page.getByTestId("mqtt-provisioning-qr")).toHaveAttribute(
    "data-qr-payload",
    expectedQrPayload({ mqttHost, mqttPort, createdDeviceId, mqttUsername, mqttPassword }),
  );
  await expect(page.getByText("Password shown only once", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("button", { name: "+ Add Device" }).click();
  await expect(page.getByText(mqttPassword, { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("mqtt-provisioning-qr")).toHaveCount(0);
});
