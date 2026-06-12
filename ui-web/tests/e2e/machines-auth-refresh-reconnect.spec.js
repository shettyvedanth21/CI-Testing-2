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

test("fleet stream refreshes auth before reconnecting after idle disconnect", async ({ page }) => {
  test.skip(!process.env.RUN_STREAM_AUTH_E2E, "Set RUN_STREAM_AUTH_E2E=1 against a fresh UI server to validate browser reconnect auth refresh.");

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

  const accessTokenV1 = `header.${base64Json({
    role: "org_admin",
    tenant_id: "SH00000001",
    exp: Math.floor(Date.now() / 1000) - 60,
  })}.signature`;
  const accessTokenV2 = `header.${base64Json({
    role: "org_admin",
    tenant_id: "SH00000001",
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}.signature`;

  let refreshCalls = 0;
  let streamCalls = 0;

  await page.addInitScript(({ staleToken, meSnapshot }) => {
    window.sessionStorage.setItem("factoryops_access_token", staleToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(meSnapshot));
  }, { staleToken: accessTokenV1, meSnapshot: me });

  await page.route("**/backend/auth/api/v1/auth/refresh", async (route) => {
    refreshCalls += 1;
    await fulfillJson(route, {
      access_token: accessTokenV2,
      refresh_token: "refresh-token-2",
      token_type: "bearer",
      expires_in: 3600,
    });
  });
  await page.route("**/api/v1/devices/dashboard/fleet-stream**", async (route) => {
    streamCalls += 1;
    const authHeader = route.request().headers().authorization || "";
    if (streamCalls === 1) {
      await route.fulfill({ status: 401, contentType: "text/plain", body: "expired token" });
      return;
    }

    if (authHeader !== `Bearer ${accessTokenV2}`) {
      await route.fulfill({ status: 401, contentType: "text/plain", body: "stale token" });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body:
        "id: 2\n" +
        "event: fleet_update\n" +
        'data: {"id":"2","event":"fleet_update","generated_at":"2026-04-03T00:00:03.000Z","freshness_ts":"2026-04-03T00:00:03.000Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":2}\n\n',
    });
  });
  await page.route("**/backend/auth/api/v1/auth/me", async (route) => {
    await fulfillJson(route, me);
  });

  await page.goto("/test-support/fleet-stream-reconnect");

  await expect(page.getByTestId("harness-load-count")).toHaveText("1");
  await expect(page.getByTestId("harness-open-count")).toHaveText("0");
  await expect(page.getByTestId("harness-event-version")).toHaveText("0");

  await expect.poll(() => refreshCalls, { timeout: 10_000 }).toBe(1);
  await expect(page.getByTestId("harness-event-version")).toHaveText("2", { timeout: 10_000 });
  await expect(page.getByTestId("harness-open-count")).toHaveText("1", { timeout: 10_000 });
  await expect.poll(() => streamCalls, { timeout: 10_000 }).toBe(2);
  await expect(page.getByTestId("harness-load-count")).toHaveText("1");
});
