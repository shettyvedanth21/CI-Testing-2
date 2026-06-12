/* eslint-disable @typescript-eslint/no-require-imports */
const { expect, test } = require("@playwright/test");

test.skip(process.env.PLAYWRIGHT_LIVE_E2E !== "1", "Requires live auth and device services.");

const authServiceApiBase = process.env.AUTH_SERVICE_BASE_URL || "http://localhost:8090";
const deviceServiceApiBase = process.env.DEVICE_SERVICE_BASE_URL || "http://localhost:8000";
const superAdminEmail = process.env.VALIDATE_SUPER_ADMIN_EMAIL || process.env.BOOTSTRAP_SUPER_ADMIN_EMAIL || "manash.ray@cittagent.com";
const superAdminPassword = process.env.VALIDATE_SUPER_ADMIN_PASSWORD || process.env.BOOTSTRAP_SUPER_ADMIN_PASSWORD || "Shivex@2706";
const tempPassword = process.env.VALIDATE_TEMP_PASSWORD || "Validate123!";

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${url} failed with HTTP ${response.status}: ${JSON.stringify(body)}`);
  }
  return body;
}

async function login(email, password) {
  return fetchJson(`${authServiceApiBase}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

async function createEmptyTenantSession() {
  const superAdmin = await login(superAdminEmail, superAdminPassword);
  const superAdminToken = superAdmin.access_token;
  const orgs = await fetchJson(`${authServiceApiBase}/api/admin/tenants`, {
    headers: { Authorization: `Bearer ${superAdminToken}` },
  });

  let targetOrg = null;
  for (const org of orgs) {
    const snapshot = await fetchJson(
      `${deviceServiceApiBase}/api/v1/devices/dashboard/fleet-snapshot?page=1&page_size=1&tenant_id=${encodeURIComponent(org.id)}`,
      { headers: { Authorization: `Bearer ${superAdminToken}` } },
    );
    if ((snapshot.devices || []).length === 0) {
      targetOrg = org;
      break;
    }
  }

  if (!targetOrg) {
    throw new Error("No empty organisation is available for machines empty-tenant reconnect E2E.");
  }

  const uniqueEmail = `validate+machines-empty-${Date.now()}@factoryops.local`;
  await fetchJson(`${authServiceApiBase}/api/admin/users`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${superAdminToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      email: uniqueEmail,
      full_name: "Machines Empty Tenant E2E",
      role: "org_admin",
      tenant_id: targetOrg.id,
      password: tempPassword,
      plant_ids: [],
    }),
  });

  const orgAdmin = await login(uniqueEmail, tempPassword);
  const me = await fetchJson(`${authServiceApiBase}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${orgAdmin.access_token}` },
  });

  return {
    accessToken: orgAdmin.access_token,
    refreshToken: orgAdmin.refresh_token,
    me,
  };
}

test("machines page stays connected for a healthy empty tenant", async ({ page }) => {
  const session = await createEmptyTenantSession();
  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem("factoryops_access_token", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", snapshot.refreshToken);
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot.me));
  }, session);

  await page.goto("/machines");

  await expect(page.getByText("0 devices")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("machines-reconnecting-banner")).toBeHidden();

  await page.waitForTimeout(12_000);

  await expect(page.getByText("0 devices")).toBeVisible();
  await expect(page.getByTestId("machines-reconnecting-banner")).toBeHidden();
});
