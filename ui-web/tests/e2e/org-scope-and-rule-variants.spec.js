/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installPhase3Harness } = require("./support/phase3Harness.js");
const { installJourneyHappyPathHarness } = require("./support/journeyHappyPathHarness.js");

function modalFieldSelect(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::select[1]");
}

async function signIn(page, email, password = "FactoryOps#123") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
}

test("org admin pages stay tenant-scoped and plant list empty state remains truthful", async ({ page }) => {
  await installPhase3Harness(page, { includeForeignTenantFixtures: true });

  await signIn(page, "ops@example.com");

  await page.goto("/tenant/users");
  await expect(page.getByRole("heading", { name: "Team" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "pm@example.com" }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "viewer@example.com" }).first()).toBeVisible();
  await expect(page.getByText("Factory Ops").first()).toBeVisible();
  await expect(page.getByText("other-admin@example.com")).toHaveCount(0);

  await page.goto("/tenant/plants");
  await expect(page.getByRole("heading", { name: "Plants" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Plant One" }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Pune" }).first()).toBeVisible();
  await expect(page.getByText("Shadow Plant")).toHaveCount(0);
});

test("viewer role keeps navigation and machine detail read-only", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page, "viewer@example.com");
  await expect(page.getByRole("link", { name: "Rules" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Reports" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Settings" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Team" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Plants" })).toHaveCount(0);

  await page.getByRole("link", { name: /Packaging Line A AD00000010/ }).click();
  await expect(page).toHaveURL(/\/machines\/AD00000010$/);
  await expect(page.getByRole("button", { name: "Parameter Configuration", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Configure Rules", exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Maintenance Log", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add Maintenance" })).toHaveCount(0);
});

test("plant list empty state is truthful when an organisation has no plants", async ({ page }) => {
  await installPhase3Harness(page, { initialPlants: [] });

  await signIn(page, "ops@example.com");
  await page.goto("/tenant/plants");
  await expect(page.getByRole("heading", { name: "Plants" })).toBeVisible();
  await expect(page.getByText("No plants yet. Add a plant to start assigning devices and people.")).toBeVisible();
});

test("rule type variants create successfully and field-level validation stays truthful", async ({ page }) => {
  await installJourneyHappyPathHarness(page);

  await signIn(page, "ops@example.com");
  await page.getByRole("button", { name: "+ Add Device" }).click();
  const modal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });
  await modal.locator('input[placeholder="e.g. Compressor Line A"]').fill("Variants Line");
  await modalFieldSelect(modal, "Plant").selectOption("plant-1");
  await modal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(modal, "Device ID Class").selectOption("active");
  await modal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await modal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await modal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Rule Bay");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();
  await page.getByRole("button", { name: "Done" }).click();

  await page.getByRole("link", { name: /Variants Line AD00000001/ }).click();
  await expect(page).toHaveURL(/\/machines\/AD00000001$/);
  await page.getByRole("button", { name: "Configure Rules", exact: true }).click();

  await page.getByRole("button", { name: "Add Rule" }).click();
  await page.getByLabel("Rule Name").fill("Power threshold high");
  await page.getByLabel("Rule Type").selectOption("threshold");
  await page.getByLabel("Threshold Value").fill("");
  await page.getByRole("checkbox", { name: "Email" }).check();
  await page.getByLabel("Email Recipients").fill("alerts@factoryops.example");
  await page.getByRole("button", { name: "Add Email" }).click();
  await page.getByRole("button", { name: "Create Rule" }).click();
  await expect
    .poll(async () =>
      page.getByLabel("Threshold Value").evaluate((element) =>
        element instanceof HTMLInputElement ? element.validationMessage : "",
      ),
    )
    .not.toBe("");
  await page.getByLabel("Threshold Value").fill("280");
  await page.getByRole("button", { name: "Create Rule" }).click();
  await expect(page.getByText("Power threshold high")).toBeVisible();

  await page.getByRole("button", { name: "Add Rule" }).click();
  await page.getByLabel("Rule Name").fill("No running after-hours");
  await page.getByLabel("Rule Type").selectOption("time_based");
  await page.getByLabel("Restricted From (IST)").fill("");
  await page.getByLabel("Restricted To (IST)").fill("");
  await page.getByRole("checkbox", { name: "Email" }).check();
  await page.getByLabel("Email Recipients").fill("alerts@factoryops.example");
  await page.getByRole("button", { name: "Add Email" }).click();
  await page.getByRole("button", { name: "Create Rule" }).click();
  await expect
    .poll(async () =>
      page.getByLabel("Restricted From (IST)").evaluate((element) =>
        element instanceof HTMLInputElement ? element.validationMessage : "",
      ),
    )
    .not.toBe("");
  await page.getByLabel("Restricted From (IST)").fill("20:00");
  await page.getByLabel("Restricted To (IST)").fill("06:00");
  await page.getByRole("button", { name: "Create Rule" }).click();
  await expect(page.getByText("No running after-hours")).toBeVisible();

  await page.getByRole("button", { name: "Add Rule" }).click();
  await page.getByLabel("Rule Name").fill("Idle longer than 50 minutes");
  await page.getByLabel("Rule Type").selectOption("continuous_idle_duration");
  await page.getByLabel("Duration (minutes)").fill("0");
  await page.getByRole("checkbox", { name: "Email" }).check();
  await page.getByLabel("Email Recipients").fill("alerts@factoryops.example");
  await page.getByRole("button", { name: "Add Email" }).click();
  await page.getByRole("button", { name: "Create Rule" }).click();
  await expect
    .poll(async () =>
      page.getByLabel("Duration (minutes)").evaluate((element) =>
        element instanceof HTMLInputElement ? element.validationMessage : "",
      ),
    )
    .not.toBe("");
  await page.getByLabel("Duration (minutes)").fill("50");
  await page.getByRole("button", { name: "Create Rule" }).click();
  await expect(page.getByText("Idle longer than 50 minutes")).toBeVisible();

  await expect(page.getByText("Threshold", { exact: true })).toBeVisible();
  await expect(page.getByText("Time-Based", { exact: true })).toBeVisible();
  await expect(page.getByText("Continuous Idle Duration", { exact: true })).toBeVisible();
});
