/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installJourneyHappyPathHarness } = require("./support/journeyHappyPathHarness.js");

function statCardValue(page, label) {
  return page
    .locator("div.surface-panel")
    .filter({ has: page.getByText(label, { exact: true }) })
    .locator("p.text-2xl")
    .first();
}

function fieldInput(page, label) {
  return page
    .locator("div")
    .filter({ has: page.getByText(label, { exact: true }) })
    .locator("input")
    .first();
}

function modalFieldInput(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::input[1]");
}

function modalFieldSelect(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::select[1]");
}

function labeledInput(page, label) {
  return page.locator("label", { hasText: label }).locator("xpath=following-sibling::input[1]");
}

async function gotoApp(page, path) {
  await page.goto(path, { timeout: 60000, waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
}

test("happy-path journey stays truthful from sign-in through dashboard validation", async ({ page }) => {
  await installJourneyHappyPathHarness(page);

  await gotoApp(page, "/login");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("FactoryOps#123");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/machines$/);
  await expect(page.getByRole("button", { name: "All Plants" })).toBeVisible();
  await page.getByRole("button", { name: "Plant North" }).click();
  await expect(statCardValue(page, "Total Devices")).toHaveText("0");

  await page.getByRole("button", { name: "+ Add Device" }).click();
  const modal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });
  await modal.locator('input[placeholder="e.g. Compressor Line A"]').fill("Compressor Line A");
  await modalFieldSelect(modal, "Plant").selectOption("plant-1");
  await modal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(modal, "Device ID Class").selectOption("active");
  await modal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await modal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await modal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Line 1");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();

  await expect(page.getByText("Generated Device ID")).toBeVisible();
  await expect(page.getByText("AD00000001").first()).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();

  await expect(page.locator('[data-device-id="AD00000001"]')).toContainText("Compressor Line A");
  await page.getByRole("link", { name: /Compressor Line A AD00000001/ }).click();

  await expect(page).toHaveURL(/\/machines\/AD00000001$/);
  await expect(page.getByText("No active shift window right now.").first()).toBeVisible();

  await page.getByRole("button", { name: "Parameter Configuration", exact: true }).click();
  await page
    .getByRole("heading", { name: "Power", exact: true })
    .locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]")
    .getByRole("button", { name: "Configure", exact: true })
    .click();
  await expect(page.getByText(/Configure Health:/)).toBeVisible();
  const healthModal = page.getByText(/Configure Health:/).locator("xpath=ancestor::div[contains(@class,'bg-white')][1]");
  await modalFieldInput(healthModal, "Normal Min").fill("200");
  await modalFieldInput(healthModal, "Normal Max").fill("300");
  await modalFieldInput(healthModal, "Weight (%)").fill("100");
  await page.getByLabel("Ignore zero values (exclude from scoring when machine is off)").check();
  await healthModal.getByRole("button", { name: /Save/ }).click();

  await expect(page.getByText("Weight: 100%")).toBeVisible();
  await expect(page.getByText("92%").first()).toBeVisible();

  await page.getByRole("button", { name: "+ Add Shift" }).click();
  await labeledInput(page, "Shift Name").fill("Journey Day Shift");
  await labeledInput(page, "Start Time").fill("09:00");
  await labeledInput(page, "End Time").fill("17:00");
  await labeledInput(page, "Maintenance Break (min)").fill("30");
  await page.getByRole("button", { name: "Save Shift" }).click();

  await expect(page.getByText("Journey Day Shift")).toBeVisible();
  await expect(page.getByText("09:00 - 17:00")).toBeVisible();
  await expect(page.getByText("96.5%")).toBeVisible();

  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await expect(page.getByText("Waste and loss overview is not ready yet for this machine.")).toBeVisible();

  await page.getByRole("button", { name: "Configure Rules", exact: true }).click();
  await page.getByRole("button", { name: "Add Rule" }).click();
  await page.getByLabel("Rule Name").fill("Idle longer than 45 minutes");
  await page.getByLabel("Rule Type").selectOption("continuous_idle_duration");
  await page.getByLabel("Duration (minutes)").fill("45");
  await page.getByRole("checkbox", { name: "Email" }).check();
  await page.getByLabel("Email Recipients").fill("alerts@factoryops.example");
  await page.getByRole("button", { name: "Add Email" }).click();
  await page.getByRole("button", { name: "Create Rule" }).click();

  await expect(page.getByText("Idle longer than 45 minutes")).toBeVisible();
  await expect(page.getByText("Idle State")).toBeVisible();

  await page.getByRole("main").getByRole("link", { name: "Machines", exact: true }).click();
  await page.getByRole("link", { name: /Compressor Line A AD00000001/ }).click();
  await expect(page).toHaveURL(/\/machines\/AD00000001$/);

  await expect(page.getByTitle("Machine alert history")).toContainText("1");
  await page.getByTitle("Machine alert history").click();
  await expect(page.getByText("Machine Alerts")).toBeVisible();
  await expect(page.getByText("Idle duration alert")).toBeVisible();
  await expect(page.getByText("Compressor Line A remained idle for 45 minutes.")).toBeVisible();

  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings" }).first()).toBeVisible();
  await page.getByLabel("Energy Rate (per kWh)").fill("8.5");
  await page.getByLabel("Currency").selectOption("INR");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("Tariff updated")).toBeVisible();
  await expect(page.getByText("Current tariff: ₹8.50 / kWh")).toBeVisible();

  await gotoApp(page, "/machines/AD00000001");
  await expect(page.getByText("Waste and loss overview is not ready yet for this machine.")).toBeVisible();

  await page.getByRole("link", { name: "Reports" }).click();
  await expect(page.getByRole("heading", { name: "Reports" }).first()).toBeVisible();
  await expect(page.getByText("Energy Consumption Report")).toBeVisible();
  await expect(page.getByText("Report History")).toBeVisible();

  await page.getByRole("link", { name: "Calendar" }).click();
  await expect(page.getByRole("heading", { name: "Calendar" }).first()).toBeVisible();
  await expect(page.getByText("Monthly Total Consumption")).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("button", { name: "Prev", exact: true })).toBeVisible();

  await gotoApp(page, "/machines/AD00000001");
  await page.getByRole("button", { name: "Maintenance Log", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Maintenance History" })).toBeVisible();
  await expect(page.getByText("No maintenance records yet")).toBeVisible();

  await page.getByRole("main").getByRole("link", { name: "Machines", exact: true }).click();
  await expect(page).toHaveURL(/\/machines$/);
  await expect(statCardValue(page, "Total Devices")).toHaveText("1");
  await expect(statCardValue(page, "Active Alerts")).toHaveText("1");
  await expect(statCardValue(page, "System Health")).toHaveText("92.0%");
  await expect(page.locator('[data-device-id="AD00000001"]')).toContainText("Compressor Line A");
  await expect(page.locator('[data-device-id="AD00000001"]')).toContainText("running");
});
