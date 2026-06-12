/* eslint-disable @typescript-eslint/no-require-imports */
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const { expect, test } = require("@playwright/test");

test.skip(process.env.PLAYWRIGHT_LIVE_E2E !== "1", "Requires live auth/device services and Docker-managed restart support.");

const repoRoot = path.resolve(__dirname, "../../..");
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

async function createTenantScopedSession() {
  const superAdmin = await login(superAdminEmail, superAdminPassword);
  const superAdminToken = superAdmin.access_token;
  const orgs = await fetchJson(`${authServiceApiBase}/api/admin/tenants`, {
    headers: { Authorization: `Bearer ${superAdminToken}` },
  });

  const preferredSlugs = new Set(["cittagent-pvt-ltd", "tata"]);
  const discovered = [];

  for (const org of orgs) {
    const snapshot = await fetchJson(
      `${deviceServiceApiBase}/api/v1/devices/dashboard/fleet-snapshot?page=1&page_size=10&tenant_id=${encodeURIComponent(org.id)}`,
      { headers: { Authorization: `Bearer ${superAdminToken}` } },
    );
    if ((snapshot.devices || []).length > 0) {
      discovered.push(org);
    }
  }

  const targetOrg =
    discovered.find((org) => preferredSlugs.has(org.slug)) ||
    discovered[0];

  if (!targetOrg) {
    throw new Error("No organisation with devices is available for reconnect E2E.");
  }

  const uniqueEmail = `validate+machines-reconnect-${Date.now()}@factoryops.local`;
  await fetchJson(`${authServiceApiBase}/api/admin/users`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${superAdminToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      email: uniqueEmail,
      full_name: "Machines Reconnect E2E",
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

async function fetchFleetSnapshot(accessToken) {
  const response = await fetch(
    `${deviceServiceApiBase}/api/v1/devices/dashboard/fleet-snapshot?page=1&page_size=60&sort=device_name`,
    {
      cache: "no-store",
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );

  if (!response.ok) {
    throw new Error(`Fleet snapshot failed with HTTP ${response.status}`);
  }

  return response.json();
}

async function fetchServiceStartedAt(accessToken) {
  const response = await fetch(
    `${deviceServiceApiBase}/api/v1/devices/dashboard/fleet-snapshot?page=1&page_size=1&sort=device_name`,
    {
      cache: "no-store",
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );

  if (!response.ok) {
    throw new Error(`Service session check failed with HTTP ${response.status}`);
  }

  return response.headers.get("x-service-started-at");
}

test("machines page reconnects cleanly after device-service restart", async ({ page }) => {
  const session = await createTenantScopedSession();
  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem("factoryops_access_token", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", snapshot.refreshToken);
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot.me));
  }, session);

  await page.goto("/machines");

  const cards = page.locator("[data-device-id]");
  await expect(cards.first()).toBeVisible({ timeout: 60_000 });

  const previousStartedAt = await fetchServiceStartedAt(session.accessToken);
  if (!previousStartedAt) {
    throw new Error("X-Service-Started-At header is missing before restart");
  }

  execFileSync("docker", ["compose", "restart", "device-service"], {
    cwd: repoRoot,
    stdio: "inherit",
  });

  await expect
    .poll(async () => {
      try {
        const startedAt = await fetchServiceStartedAt(session.accessToken);
        return Boolean(startedAt && startedAt !== previousStartedAt);
      } catch {
        return false;
      }
    }, { timeout: 30_000 })
    .toBeTruthy();

  const reconnectingBanner = page.getByTestId("machines-reconnecting-banner");
  const bannerAppeared = await reconnectingBanner
    .waitFor({ state: "visible", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);

  if (bannerAppeared) {
    await expect(reconnectingBanner).toBeHidden({ timeout: 30_000 });
  }

  await expect(cards.first()).toBeVisible({ timeout: 30_000 });

  const snapshot = await page.evaluate(async () => {
    const accessToken = window.sessionStorage.getItem("factoryops_access_token");
    const response = await fetch("/backend/device/api/v1/devices/dashboard/fleet-snapshot?page=1&page_size=60&sort=device_name", {
      cache: "no-store",
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    });
    if (!response.ok) {
      throw new Error(`Fleet snapshot failed with HTTP ${response.status}`);
    }
    return response.json();
  });
  const expectedVersions = new Map(
    (snapshot.devices || []).map((device) => [device.device_id, Number(device.version || 0)]),
  );

  await expect
    .poll(async () => {
      const renderedVersions = await cards.evaluateAll((nodes) =>
        nodes.map((node) => ({
          deviceId: node.getAttribute("data-device-id") || "",
          version: Number(node.getAttribute("data-device-version") || "0"),
        })),
      );

      if (renderedVersions.length === 0) {
        return false;
      }

      return renderedVersions.every((rendered) => {
        const expectedVersion = expectedVersions.get(rendered.deviceId);
        return expectedVersion !== undefined && rendered.version >= expectedVersion;
      });
    }, { timeout: 10_000 })
    .toBeTruthy();
});
