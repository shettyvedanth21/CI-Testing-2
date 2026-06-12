/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installPhase3Harness } = require("./support/phase3Harness.js");

test.skip(process.env.PLAYWRIGHT_LOCAL_E2E !== "1", "Requires dedicated maintenance log fixture validation.");

async function signIn(page, email, password = "FactoryOps#123") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
}

test("org admin can add, edit, and delete maintenance records from the machine page", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page, "ops@example.com");
  await page.goto("/machines/AD00000010");
  await page.getByRole("button", { name: "Maintenance Log" }).click();

  await expect(page.getByText("Maintenance History")).toBeVisible();
  await expect(page.getByText("Filter replacement")).toBeVisible();

  await page.getByRole("button", { name: "Add Maintenance" }).click();
  await page.getByLabel(/Maintenance date/i).fill("2026-05-02");
  await page.getByLabel(/Cost/i).fill("2200");
  await page.getByLabel(/Issue title/i).fill("Bearing lubrication");
  await page.getByLabel(/Notes/i).fill("Lubricated bearings and verified noise reduction.");
  await page.getByLabel(/Performed by/i).fill("Ajay");
  await page.getByLabel(/Status/i).selectOption("completed");
  await page.getByLabel(/Next due date/i).fill("2026-06-02");
  await page.getByRole("button", { name: "Add Maintenance" }).click();

  await expect(page.getByText("Maintenance record added.")).toBeVisible();
  await expect(page.getByText("Bearing lubrication")).toBeVisible();

  const addedRecord = page.locator("div").filter({ hasText: "Bearing lubrication" }).first();
  await addedRecord.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel(/Issue title/i).fill("Bearing lubrication follow-up");
  await page.getByLabel(/Cost/i).fill("2450");
  await page.getByRole("button", { name: "Save Changes" }).click();

  await expect(page.getByText("Maintenance record updated.")).toBeVisible();
  await expect(page.getByText("Bearing lubrication follow-up")).toBeVisible();

  const updatedRecord = page.locator("div").filter({ hasText: "Bearing lubrication follow-up" }).first();
  await updatedRecord.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("Delete Maintenance Record")).toBeVisible();
  await page.getByRole("button", { name: "Delete Record" }).click();

  await expect(page.getByText("Maintenance record deleted.")).toBeVisible();
  await expect(page.getByText("Bearing lubrication follow-up")).toHaveCount(0);
});

test("missing maintenance record delete keeps the UI error contract stable", async ({ page }) => {
  const harness = await installPhase3Harness(page);

  await signIn(page, "ops@example.com");
  await page.goto("/machines/AD00000010");
  await page.getByRole("button", { name: "Maintenance Log" }).click();
  harness.failNextMaintenanceDeleteAsMissing();

  const existingRecord = page.locator("div").filter({ hasText: "Filter replacement" }).first();
  await existingRecord.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("button", { name: "Delete Record" }).click();

  await expect(page.getByText("This maintenance record is no longer available. Please refresh the list.")).toBeVisible();
  await expect(page.getByText("Filter replacement")).toBeVisible();
});
