/* eslint-disable @typescript-eslint/no-require-imports */

const { chromium } = require("@playwright/test");

const UI_BASE_URL = process.env.CERTIFY_UI_BASE_URL || "http://localhost:3000";
const SUPER_ADMIN_EMAIL = process.env.CERTIFY_SUPER_ADMIN_EMAIL;
const SUPER_ADMIN_PASSWORD = process.env.CERTIFY_SUPER_ADMIN_PASSWORD;
const TENANT_ID = process.env.CERTIFY_TENANT_ID;
const TENANT_LABEL = process.env.CERTIFY_TENANT_LABEL || null;
const EXPECT_GENERATED_DEVICE_ID = process.env.CERTIFY_EXPECTED_GENERATED_DEVICE_ID || "";
const PLANT_MANAGER_EMAIL = process.env.CERTIFY_PM_EMAIL || "";
const PLANT_MANAGER_PASSWORD = process.env.CERTIFY_PM_PASSWORD || "";
const REQUIRE_PLANT_MANAGER_CHECK = process.env.CERTIFY_REQUIRE_PM === "1";

function requireEnv(name, value) {
  if (!value) {
    throw new Error(`Missing required env var ${name}`);
  }
}

function isDeviceOptionDisambiguated(option) {
  const label = option?.label || "";
  const description = option?.description || "";
  return /\([^)]+\)/.test(label) || / · 01[A-HJKMNP-TV-Z0-9]{24}/.test(description);
}

async function login(page, email, password) {
  await page.goto(`${UI_BASE_URL}/login`, { timeout: 60000, waitUntil: "domcontentloaded" });
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/machines$/, { timeout: 30000 });
}

async function ensureTenant(page) {
  await page.goto(`${UI_BASE_URL}/analytics`, { timeout: 60000, waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  if (await page.getByText("Select an organisation to continue").count()) {
    const selector = page.locator("select").first();
    if (TENANT_LABEL) {
      await selector.selectOption({ label: TENANT_LABEL });
    } else {
      await selector.selectOption(TENANT_ID);
    }
    await page.waitForFunction(
      ({ tenantId, tenantLabel }) => {
        const select = document.querySelector("select");
        if (!(select instanceof HTMLSelectElement)) {
          return false;
        }
        const selectedOption = select.selectedOptions.item(0);
        if (!selectedOption) {
          return false;
        }
        return select.value === tenantId || selectedOption.textContent?.trim() === tenantLabel;
      },
      { tenantId: TENANT_ID, tenantLabel: TENANT_LABEL },
      { timeout: 10000 },
    );

    const continueButton = page.getByRole("button", { name: "Continue" });
    await continueButton.waitFor({ state: "visible", timeout: 10000 });
    await continueButton.waitFor({ state: "attached", timeout: 10000 });
    await page.waitForFunction(
      () => {
        const button = Array.from(document.querySelectorAll("button")).find((candidate) =>
          candidate.textContent?.trim() === "Continue",
        );
        return button instanceof HTMLButtonElement && !button.disabled;
      },
      undefined,
      { timeout: 10000 },
    );
    await continueButton.click();
    await page.waitForFunction(
      (tenantId) => window.sessionStorage.getItem("factoryops_selected_tenant") === tenantId,
      TENANT_ID,
      { timeout: 10000 },
    );
    await page.waitForFunction(
      () => !document.body.innerText.includes("Select an organisation to continue"),
      undefined,
      { timeout: 15000 },
    );
    await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  }
}

async function openScopeSurface(page, pagePath) {
  await page.goto(`${UI_BASE_URL}${pagePath}`, { timeout: 60000, waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  if (pagePath === "/reports") {
    await page.getByRole("button", { name: "Schedules" }).click();
    await page.getByRole("button", { name: "New Schedule" }).click();
  }
  await page.getByTestId("device-scope-selector").waitFor({ timeout: 30000 });
}

async function waitForScopeModeReady(page, mode) {
  await page.getByTestId(`device-scope-mode-${mode}`).click({ force: true });
  if (mode === "all") {
    return;
  }

  const contentSelector = mode === "plant"
    ? '[data-testid="device-scope-plant-option"], [data-testid="device-scope-plant-empty"]'
    : '[data-testid="device-scope-device-option"], [data-testid="device-scope-device-empty"]';

  await page.waitForFunction(
    (selector) => {
      const nodes = Array.from(document.querySelectorAll(selector));
      return nodes.some((node) => {
        const text = node.textContent?.trim() || "";
        return text.length > 0;
      });
    },
    contentSelector,
    { timeout: 30000 },
  );
}

async function collectPlantEvidence(page) {
  return page.locator('[data-testid="device-scope-plant-option"]').evaluateAll((nodes) =>
    nodes
      .map((node) => node.textContent?.replace(/\s+/g, " ").trim())
      .filter(Boolean),
  );
}

async function collectDeviceOptions(page) {
  const optionCount = await page.getByTestId("device-scope-device-option").count();
  const options = [];
  for (let index = 0; index < optionCount; index += 1) {
    const option = page.getByTestId("device-scope-device-option").nth(index);
    const label = ((await option.getByTestId("device-scope-device-label").textContent()) || "").replace(/\s+/g, " ").trim();
    const description = ((await option.getByTestId("device-scope-device-description").textContent()) || "").replace(/\s+/g, " ").trim();
    options.push({ label, description });
  }
  return options;
}

async function evaluateSharedSelector(page, pagePath) {
  await openScopeSurface(page, pagePath);

  const allMachinesVisible = await page.getByTestId("device-scope-mode-all").isVisible();
  const plantsVisible = await page.getByTestId("device-scope-mode-plant").isVisible();
  const selectMachinesVisible = await page.getByTestId("device-scope-mode-devices").isVisible();

  await waitForScopeModeReady(page, "plant");
  const plantCards = await collectPlantEvidence(page);

  await waitForScopeModeReady(page, "devices");
  const options = await collectDeviceOptions(page);
  const generatedLabels = options
    .filter((option) => /01[A-HJKMNP-TV-Z0-9]{24}/.test(`${option.label} ${option.description}`))
    .map((option) => `${option.label} ${option.description}`.trim());
  const generatedDeviceVisible = EXPECT_GENERATED_DEVICE_ID
    ? options.some((option) => `${option.label} ${option.description}`.includes(EXPECT_GENERATED_DEVICE_ID))
    : generatedLabels.length > 0;
  const duplicateDisambiguationVisible = options.some((option) => isDeviceOptionDisambiguated(option));

  return {
    path: pagePath,
    allMachinesVisible,
    plantsVisible,
    selectMachinesVisible,
    plantCards,
    generatedLabelsSample: generatedLabels.slice(0, 10),
    generatedDeviceVisible,
    duplicateDisambiguationVisible,
  };
}

async function verifyWasteRun(page) {
  await page.goto(`${UI_BASE_URL}/waste-analysis`, { timeout: 60000, waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.getByTestId("device-scope-selector").waitFor({ timeout: 30000 });
  await waitForScopeModeReady(page, "devices");
  const candidate = page.getByTestId("device-scope-device-option").filter({ hasText: /01[A-HJKMNP-TV-Z0-9]{24}/ }).first();
  if (!(await candidate.count())) {
    return {
      runAttempted: false,
      runObserved: false,
      downloadVisible: false,
      reason: "No generated-ID machine was available in the selector.",
    };
  }

  await candidate.click();
  const rowsBefore = await page.locator("tbody tr").count().catch(() => 0);
  await page.getByRole("button", { name: "Run Wastage Analysis" }).click();
  await page.getByText("Queued").first().waitFor({ timeout: 30000 });

  let downloadVisible = false;
  for (let index = 0; index < 20; index += 1) {
    await page.waitForTimeout(3000);
    const body = await page.locator("body").innerText();
    if (/Download PDF/.test(body)) {
      downloadVisible = true;
      break;
    }
  }

  const rowsAfter = await page.locator("tbody tr").count().catch(() => 0);
  return {
    runAttempted: true,
    runObserved: true,
    downloadVisible,
    rowsBefore,
    rowsAfter,
  };
}

async function verifyPlantManagerAccess() {
  if (!PLANT_MANAGER_EMAIL || !PLANT_MANAGER_PASSWORD) {
    return {
      checked: false,
      blocked: true,
      detail: "Plant manager credentials were not provided.",
    };
  }

  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1400 } });
    await page.goto(`${UI_BASE_URL}/login`, { timeout: 60000, waitUntil: "domcontentloaded" });
    await page.getByLabel("Email").fill(PLANT_MANAGER_EMAIL);
    await page.getByLabel("Password").fill(PLANT_MANAGER_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForTimeout(5000);

    if (/\/login$/.test(page.url())) {
      const body = await page.locator("body").innerText();
      return {
        checked: false,
        blocked: true,
        detail: body.includes("Invalid email or password")
          ? "Plant manager credentials were rejected by the live environment."
          : `Plant manager login did not complete (url=${page.url()}).`,
      };
    }

    let payload = await page.evaluate(() => {
      const raw = window.sessionStorage.getItem("factoryops_me");
      if (!raw) {
        return null;
      }
      try {
        return JSON.parse(raw);
      } catch {
        return "__INVALID_JSON__";
      }
    });

    if (payload === "__INVALID_JSON__") {
      return {
        checked: false,
        blocked: true,
        detail: "Plant manager session contained invalid cached auth state.",
      };
    }

    if (!payload) {
      const authMeResponse = await page.evaluate(async (baseUrl) => {
        const response = await fetch(`${baseUrl}/backend/auth/api/v1/auth/me`, {
          credentials: "include",
        });
        const text = await response.text();
        return {
          status: response.status,
          body: text,
        };
      }, UI_BASE_URL);

      if (authMeResponse.status !== 200) {
        return {
          checked: false,
          blocked: true,
          detail: `Plant manager session could not load auth state (status=${authMeResponse.status}).`,
        };
      }

      try {
        payload = JSON.parse(authMeResponse.body);
      } catch (error) {
        return {
          checked: false,
          blocked: true,
          detail: "Plant manager session returned a non-JSON auth payload.",
        };
      }
    }

    const role = payload?.user?.role || null;
    const tenantId = payload?.org?.id || payload?.user?.tenant_id || null;
    if (role !== "plant_manager") {
      return {
        checked: false,
        blocked: true,
        detail: `Expected plant_manager credentials but received role=${role || "unknown"}.`,
        role,
        tenantId,
      };
    }
    if (tenantId !== TENANT_ID) {
      return {
        checked: false,
        blocked: true,
        detail: `Plant manager belongs to tenant ${tenantId || "unknown"}, expected ${TENANT_ID}.`,
        role,
        tenantId,
      };
    }

    const linkCount = await page.getByRole("link", { name: "Waste Analysis" }).count();
    return {
      checked: true,
      blocked: false,
      wasteAccessible: linkCount > 0,
      role,
      tenantId,
      currentUrl: page.url(),
    };
  } finally {
    await browser.close();
  }
}

async function main() {
  requireEnv("CERTIFY_SUPER_ADMIN_EMAIL", SUPER_ADMIN_EMAIL);
  requireEnv("CERTIFY_SUPER_ADMIN_PASSWORD", SUPER_ADMIN_PASSWORD);
  requireEnv("CERTIFY_TENANT_ID", TENANT_ID);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1400 } });

  try {
    await login(page, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD);
    await ensureTenant(page);

    const analytics = await evaluateSharedSelector(page, "/analytics");
    const reports = await evaluateSharedSelector(page, "/reports");
    const waste = await evaluateSharedSelector(page, "/waste-analysis");
    const wasteRun = await verifyWasteRun(page);
    const plantManager = await verifyPlantManagerAccess();

    const payload = {
      uiBaseUrl: UI_BASE_URL,
      tenantId: TENANT_ID,
      analytics,
      reports,
      waste,
      wasteRun,
      plantManager,
    };

    console.log(JSON.stringify(payload, null, 2));

    const requiredChecks = [
      analytics.allMachinesVisible,
      analytics.plantsVisible,
      analytics.selectMachinesVisible,
      reports.allMachinesVisible,
      reports.plantsVisible,
      reports.selectMachinesVisible,
      waste.allMachinesVisible,
      waste.plantsVisible,
      waste.selectMachinesVisible,
      analytics.plantCards.length > 0,
      reports.plantCards.length > 0,
      waste.plantCards.length > 0,
      analytics.generatedDeviceVisible,
      reports.generatedDeviceVisible,
      waste.generatedDeviceVisible,
      analytics.duplicateDisambiguationVisible,
      reports.duplicateDisambiguationVisible,
      waste.duplicateDisambiguationVisible,
    ];
    if (REQUIRE_PLANT_MANAGER_CHECK) {
      requiredChecks.push(plantManager.checked);
      requiredChecks.push(!plantManager.blocked);
    }

    if (requiredChecks.some((value) => !value)) {
      process.exit(1);
    }
  } finally {
    await browser.close();
  }
}

module.exports = {
  isDeviceOptionDisambiguated,
  waitForScopeModeReady,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.stack || error.message : String(error));
    process.exit(1);
  });
}
