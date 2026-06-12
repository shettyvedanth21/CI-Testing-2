/* eslint-disable @typescript-eslint/no-require-imports */

const { test, expect } = require("@playwright/test");
const fs = require("node:fs");

const runPreprodE2E = process.env.PLAYWRIGHT_PREPROD_E2E === "1";

test.skip(!runPreprodE2E, "Requires a configured pre-production environment.");

const context = runPreprodE2E
  ? JSON.parse(fs.readFileSync("/tmp/smoke_context.json", "utf8"))
  : {
      device_ids: {},
      plants: {},
      passwords: {},
    };
const deviceIds = context.device_ids;
const plants = context.plants;
const rolePasswords = context.passwords || {};

function resolvePassword(email) {
  if (email === context.org_admin_email) return rolePasswords.org_admin || process.env.VALIDATE_SMOKE_PASSWORD || "Validate123!";
  if (email === context.plant_manager_email) return rolePasswords.plant_manager || process.env.VALIDATE_SMOKE_PASSWORD || "Validate123!";
  if (email === context.operator_email) return rolePasswords.operator || process.env.VALIDATE_SMOKE_PASSWORD || "Validate123!";
  if (email === context.viewer_email) return rolePasswords.viewer || process.env.VALIDATE_SMOKE_PASSWORD || "Validate123!";
  return process.env.VALIDATE_SMOKE_PASSWORD || "Validate123!";
}

async function login(page, email) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(resolvePassword(email));
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/machines", { timeout: 30_000 });
}

test.describe.serial("pre-production scoped UI smoke", () => {
  test("org admin sees full org machines scope", async ({ page }) => {
    await login(page, context.org_admin_email);

    await expect(page.getByRole("link", { name: "Machines" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Rules" })).toBeVisible();
    await expect(page.getByRole("button", { name: plants.A, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: plants.B, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: plants.C, exact: true })).toBeVisible();
    await expect(page.getByText("Smoke Device A")).toBeVisible();
    await expect(page.getByText("Smoke Device B")).toBeVisible();
    await expect(page.getByText("Smoke Device C")).toBeVisible();
  });

  test("plant manager UI stays inside assigned plants", async ({ page }) => {
    await login(page, context.plant_manager_email);

    await expect(page.getByRole("link", { name: "Machines" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Rules" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Reports" })).toBeVisible();

    await expect(page.getByRole("button", { name: plants.A, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: plants.B, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: plants.C, exact: true })).toHaveCount(0);

    await expect(page.getByText("Smoke Device A")).toBeVisible();
    await expect(page.getByText("Smoke Device B")).toBeVisible();
    await expect(page.getByText("Smoke Device C")).toHaveCount(0);

    await page.getByRole("link", { name: "Rules" }).click();
    await expect(page).toHaveURL(/\/rules$/);
    await page.getByRole("button", { name: "Add Rule" }).click();
    await expect(page.getByLabel("Scope")).toContainText("All Accessible Devices");
    await expect(page.getByText("For your role, \"All Accessible Devices\" means only devices from your assigned plants.")).toBeVisible();
    await page.getByLabel("Rule Type").selectOption("continuous_idle_duration");
    await expect(page.getByLabel("Duration (minutes)")).toBeVisible();
    await expect(page.getByLabel("Restricted From (IST)")).toHaveCount(0);
    await expect(page.getByLabel("Restricted To (IST)")).toHaveCount(0);

    await page.getByRole("link", { name: "Reports" }).click();
    await expect(page).toHaveURL(/\/reports$/);
    await expect(page.getByText("Assigned plant scope")).toBeVisible();
    await expect(page.getByText("Report generation, history, and schedules are limited to devices from your assigned plants.")).toBeVisible();

    await page.getByRole("link", { name: "Energy Consumption Report" }).click();
    await expect(page).toHaveURL(/\/reports\/energy$/);
    await expect(page.getByText("Assigned plant scope")).toBeVisible();
    await expect(page.getByText("All Accessible Devices")).toBeVisible();
  });

  test("operator sees only machines and rules for assigned plant", async ({ page }) => {
    await login(page, context.operator_email);

    await expect(page.getByRole("link", { name: "Machines" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Rules" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Reports" })).toHaveCount(0);

    await expect(page.getByRole("button", { name: plants.A, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: plants.B, exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: plants.C, exact: true })).toHaveCount(0);

    await expect(page.getByText("Smoke Device A")).toBeVisible();
    await expect(page.getByText("Smoke Device B")).toHaveCount(0);
    await expect(page.getByText("Smoke Device C")).toHaveCount(0);

    await page.getByRole("link", { name: "Rules" }).click();
    await expect(page).toHaveURL(/\/rules$/);
    await page.getByRole("button", { name: "Add Rule" }).click();
    await expect(page.getByLabel("Scope")).toContainText("All Accessible Devices");
    await expect(page.getByText("For your role, \"All Accessible Devices\" means only devices from your assigned plants.")).toBeVisible();
    await page.getByLabel("Rule Type").selectOption("continuous_idle_duration");
    await expect(page.getByLabel("Duration (minutes)")).toBeVisible();
    await expect(page.getByLabel("Restricted From (IST)")).toHaveCount(0);
    await expect(page.getByLabel("Restricted To (IST)")).toHaveCount(0);
  });

  test("viewer machine detail only shows read-only tabs and no idle widget", async ({ page }) => {
    await login(page, context.viewer_email);

    await expect(page.getByRole("link", { name: "Machines" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Rules" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Reports" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: plants.A, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: plants.B, exact: true })).toHaveCount(0);

    await page.goto(`/machines/${deviceIds.A}`);
    await expect(page).toHaveURL(new RegExp(`/machines/${deviceIds.A}$`), { timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Overview" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Telemetry" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Parameter Configuration" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Configure Rules" })).toHaveCount(0);
    await expect(page.getByText("Waste & Loss Today")).toBeVisible();
    await expect(page.getByText("Idle Running Waste")).toHaveCount(0);
    await expect(page.getByText(/outside-shift energy is financially booked to Off-hours Loss/).first()).toBeVisible();
  });
});
