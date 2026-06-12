/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installPhase3Harness } = require("./support/phase3Harness.js");

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

async function fillOnboardingForm(page, deviceName) {
  const modal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });
  await modal.locator('input[placeholder="e.g. Compressor Line A"]').fill(deviceName);
  await modalFieldSelect(modal, "Plant").selectOption("plant-1");
  await modal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(modal, "Device ID Class").selectOption("active");
  await modal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await modal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await modal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Bay 1");
}

test("onboarding conflict and ID allocation failures show stable user-facing contracts", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page, "ops@example.com");
  await page.getByRole("button", { name: "+ Add Device" }).click();

  await fillOnboardingForm(page, "Conflict Device");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();
  await expect(page.getByText("A device with these onboarding details already exists for this organisation.")).toBeVisible();

  await fillOnboardingForm(page, "Allocation Failure Device");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();
  await expect(page.getByText("Unable to allocate a device ID right now. Please try again.")).toBeVisible();
});

test("onboarding required fields surface truthful validation before create", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page, "ops@example.com");
  await page.getByRole("button", { name: "+ Add Device" }).click();
  const modal = page.getByRole("heading", { name: "Add device" }).locator("xpath=ancestor::div[contains(@class,'bg-[var(--surface-0)]')][1]");

  await modal.locator("form").evaluate((form) => {
    if (form instanceof HTMLFormElement) {
      form.reportValidity();
    }
  });
  await expect
    .poll(async () =>
      modal.locator('input[placeholder="e.g. Compressor Line A"]').evaluate((element) =>
        element instanceof HTMLInputElement ? element.validationMessage : "",
      ),
    )
    .not.toBe("");

  await modal.locator('input[placeholder="e.g. Compressor Line A"]').fill("Needs Plant");
  await modal.locator("form").evaluate((form) => {
    if (form instanceof HTMLFormElement) {
      form.reportValidity();
    }
  });
  await expect
    .poll(async () =>
      modal.locator("select").first().evaluate((element) =>
        element instanceof HTMLSelectElement ? element.validationMessage : "",
      ),
    )
    .not.toBe("");

  await modalFieldSelect(modal, "Plant").selectOption("plant-1");
  await modal.locator("form").evaluate((form) => {
    if (form instanceof HTMLFormElement) {
      form.reportValidity();
    }
  });
  await expect
    .poll(async () =>
      modal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').evaluate((element) =>
        element instanceof HTMLInputElement ? element.validationMessage : "",
      ),
    )
    .not.toBe("");
});
