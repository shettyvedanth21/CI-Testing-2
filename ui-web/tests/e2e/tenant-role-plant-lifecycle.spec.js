/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installPhase3Harness } = require("./support/phase3Harness.js");

async function signIn(page, email, password = "FactoryOps#123") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
}

test("role changes take effect after re-login and remove elevated UI affordances", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page, "ops@example.com");
  await page.goto("/tenant/users");
  await expect(page.getByRole("heading", { name: "Team" })).toBeVisible();

  const managerRow = page.getByRole("row").filter({ hasText: "pm@example.com" }).first();
  await managerRow.getByRole("button", { name: "Edit" }).click();
  const editUserDialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Edit user" }) });
  await editUserDialog.locator("#edit-user-role").selectOption("viewer");
  await editUserDialog.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect(page.getByText("User updated successfully.")).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("Email").fill("pm@example.com");
  await page.getByLabel("Password").fill("FactoryOps#123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
  await expect(page.getByRole("button", { name: "+ Add Device" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Reports" })).toHaveCount(0);
});

test("plant create, duplicate validation, deactivate, and reactivate keep onboarding truthfulness intact", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page, "ops@example.com");
  await page.goto("/tenant/plants");
  await expect(page.getByRole("heading", { name: "Plants" })).toBeVisible();

  await page.getByRole("button", { name: "Add Plant" }).click();
  let createPlantDialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Add plant" }) });
  const plantNameField = createPlantDialog.getByLabel("Plant name");
  await plantNameField.fill("A");
  await createPlantDialog.getByRole("button", { name: "Create plant", exact: true }).click();
  await expect.poll(async () => plantNameField.evaluate((element) => (element instanceof HTMLInputElement ? element.validationMessage : ""))).not.toBe("");

  await plantNameField.fill("Aurora Plant");
  await createPlantDialog.getByLabel("Location").fill("Aurora Campus");
  await createPlantDialog.getByRole("button", { name: "Create plant", exact: true }).click();
  await expect(page.getByRole("row").filter({ hasText: "Aurora Plant" }).first()).toBeVisible();

  await page.getByRole("button", { name: "Add Plant" }).click();
  createPlantDialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Add plant" }) });
  await createPlantDialog.getByLabel("Plant name").fill("Aurora Plant");
  await createPlantDialog.getByRole("button", { name: "Create plant", exact: true }).click();
  await expect(page.getByText("Plant name already exists for this organisation.")).toBeVisible();
  await createPlantDialog.getByRole("button", { name: "Cancel", exact: true }).click();

  const auroraRow = page.getByRole("row").filter({ hasText: "Aurora Plant" }).first();
  await auroraRow.getByRole("button", { name: "Deactivate" }).click();
  await expect(auroraRow.getByText("Inactive")).toBeVisible();

  await page.goto("/machines");
  await page.getByRole("button", { name: "+ Add Device" }).click();
  await expect(page.locator("select").first().locator('option[value="plant-2"]')).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel", exact: true }).click();

  await page.goto("/tenant/plants");
  const reactivatedRow = page.getByRole("row").filter({ hasText: "Aurora Plant" }).first();
  await reactivatedRow.getByRole("button", { name: "Reactivate" }).click();
  await expect(reactivatedRow.getByText("Active")).toBeVisible();

  await page.goto("/machines");
  await page.getByRole("button", { name: "+ Add Device" }).click();
  await expect(page.locator("select").first().locator('option[value="plant-2"]')).toHaveCount(1);
});
