/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installPhase3Harness } = require("./support/phase3Harness.js");

async function signIn(page, email = "ops@example.com", password = "FactoryOps#123") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
}

function modalFieldSelect(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::select[1]");
}

test("plant edit persists and the updated label is used during device onboarding", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page);
  await page.goto("/tenant/plants");
  await expect(page.getByRole("heading", { name: "Plants" })).toBeVisible();

  const plantRow = page.getByRole("row").filter({ hasText: "Plant One" }).first();
  await plantRow.getByRole("button", { name: "Edit" }).click();

  const dialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Edit plant" }) });
  await dialog.getByLabel("Plant name").fill("Plant One Updated");
  await dialog.getByLabel("Location").fill("Nashik");
  await dialog.getByLabel("Timezone").selectOption("America/Chicago");
  await dialog.getByRole("button", { name: "Save changes" }).click();

  const updatedRow = page.getByRole("row").filter({ hasText: "Plant One Updated" }).first();
  await expect(updatedRow).toContainText("Nashik");
  await expect(updatedRow).toContainText("America/Chicago");

  await page.goto("/machines");
  await page.getByRole("button", { name: "+ Add Device" }).click();
  const onboardModal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });
  await expect(modalFieldSelect(onboardModal, "Plant")).toContainText("Plant One Updated");

  await onboardModal.locator('input[placeholder="e.g. Compressor Line A"]').fill("Edited Plant Device");
  await modalFieldSelect(onboardModal, "Plant").selectOption("plant-1");
  await onboardModal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(onboardModal, "Device ID Class").selectOption("active");
  await onboardModal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await onboardModal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await onboardModal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Bay 4");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();

  await expect(page.getByText("Generated Device ID")).toBeVisible();
});
