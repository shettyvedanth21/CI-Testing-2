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

function modalFieldInput(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::input[1]");
}

function modalFieldSelect(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::select[1]");
}

function labeledInput(page, label) {
  return page.locator("label", { hasText: label }).locator("xpath=following-sibling::input[1]");
}

async function signIn(page, email = "ops@example.com", password = "FactoryOps#123") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
}

async function onboardDefaultDevice(page, deviceName = "Depth Line") {
  const modal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });
  await page.getByRole("button", { name: "+ Add Device" }).click();
  await modal.locator('input[placeholder="e.g. Compressor Line A"]').fill(deviceName);
  await modalFieldSelect(modal, "Plant").selectOption("plant-1");
  await modal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(modal, "Device ID Class").selectOption("active");
  await modal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await modal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await modal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Line 1");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();
  await expect(page.getByText("Generated Device ID")).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("link", { name: new RegExp(`${deviceName} AD00000001`) }).click();
  await expect(page).toHaveURL(/\/machines\/AD00000001$/);
}

test("machines dashboard keeps fleet summary zero-state truthful before onboarding", async ({ page }) => {
  await installJourneyHappyPathHarness(page);

  await signIn(page);
  await expect(statCardValue(page, "Total Devices")).toHaveText("0");
  await expect(statCardValue(page, "Active Alerts")).toHaveText("0");
  await expect(statCardValue(page, "System Health")).toHaveText("—");
  await expect(page.getByText("0 devices")).toBeVisible();
});

test("health configuration shows empty state and rejects inverted ranges truthfully", async ({ page }) => {
  await installJourneyHappyPathHarness(page);

  await signIn(page);
  await onboardDefaultDevice(page, "Validation Line");

  await page.getByRole("button", { name: "Parameter Configuration", exact: true }).click();
  const powerCard = page
    .getByRole("heading", { name: "Power", exact: true })
    .locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
  await expect(powerCard.getByText("Not configured")).toBeVisible();

  await powerCard.getByRole("button", { name: "Configure", exact: true }).click();

  const healthModal = page.getByText(/Configure Health:/).locator("xpath=ancestor::div[contains(@class,'bg-white')][1]");
  await modalFieldInput(healthModal, "Normal Min").fill("400");
  await modalFieldInput(healthModal, "Normal Max").fill("300");
  await modalFieldInput(healthModal, "Weight (%)").fill("100");
  await healthModal.getByRole("button", { name: /^Save/ }).click();
  await expect(healthModal.getByText("Normal Min cannot be greater than Normal Max.")).toBeVisible();

  await modalFieldInput(healthModal, "Normal Min").fill("200");
  await healthModal.getByRole("button", { name: /^Save/ }).click();

  await expect(page.getByText("Weight: 100%")).toBeVisible();
  await expect(page.getByText("92%").first()).toBeVisible();
});

test("health configuration edits reconcile immediately without a hard refresh", async ({ page }) => {
  await installJourneyHappyPathHarness(page);

  await signIn(page);
  await onboardDefaultDevice(page, "Immediate Update Line");

  await page.getByRole("button", { name: "Parameter Configuration", exact: true }).click();
  const powerCard = page
    .getByRole("heading", { name: "Power", exact: true })
    .locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");

  await powerCard.getByRole("button", { name: "Configure", exact: true }).click();
  let healthModal = page.getByText(/Configure Health:/).locator("xpath=ancestor::div[contains(@class,'bg-white')][1]");
  await modalFieldInput(healthModal, "Normal Min").fill("100");
  await modalFieldInput(healthModal, "Normal Max").fill("300");
  await modalFieldInput(healthModal, "Weight (%)").fill("25");
  await healthModal.getByRole("button", { name: /^Save/ }).click();

  const parameterConfigCard = page
    .getByRole("heading", { name: "Power", exact: true })
    .locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
  await expect(parameterConfigCard.getByText("Normal: 100 - 300")).toBeVisible();
  await expect(parameterConfigCard.getByText("Weight: 25%")).toBeVisible();

  await parameterConfigCard.getByRole("button", { name: "Edit", exact: true }).click();
  healthModal = page.getByText(/Configure Health:/).locator("xpath=ancestor::div[contains(@class,'bg-white')][1]");
  await modalFieldInput(healthModal, "Normal Min").fill("110");
  await modalFieldInput(healthModal, "Normal Max").fill("310");
  await modalFieldInput(healthModal, "Weight (%)").fill("20");
  await healthModal.getByRole("button", { name: /^Save/ }).click();

  await expect(parameterConfigCard.getByText("Normal: 110 - 310")).toBeVisible();
  await expect(parameterConfigCard.getByText("Weight: 20%")).toBeVisible();
});

test("shift configuration renders existing data and rejects overlaps truthfully", async ({ page }) => {
  await installJourneyHappyPathHarness(page);

  await signIn(page);
  await onboardDefaultDevice(page, "Shift Line");
  await page.getByRole("button", { name: "Parameter Configuration", exact: true }).click();

  await page.getByRole("button", { name: "+ Add Shift" }).click();
  await labeledInput(page, "Shift Name").fill("Morning Shift");
  await labeledInput(page, "Start Time").fill("09:00");
  await labeledInput(page, "End Time").fill("17:00");
  await labeledInput(page, "Maintenance Break (min)").fill("30");
  await page.getByRole("button", { name: "Save Shift" }).click();

  const morningShiftCard = page.getByText("Morning Shift").locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
  await expect(morningShiftCard).toBeVisible();
  await expect(morningShiftCard).toContainText("09:00 - 17:00");
  await expect(page.getByText("96.5%")).toBeVisible();

  await page.getByRole("button", { name: "+ Add Shift" }).click();
  await labeledInput(page, "Shift Name").fill("Overlap Shift");
  await labeledInput(page, "Start Time").fill("10:00");
  await labeledInput(page, "End Time").fill("16:00");
  await expect(page.getByText(/Overlaps with: Morning Shift/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Save Shift" })).toBeDisabled();
});

test("shift delete missing-record errors stay truthful and restore the prior UI", async ({ page }) => {
  await installJourneyHappyPathHarness(page);

  let failShiftDeleteOnce = true;
  await page.route("**/backend/device/api/v1/devices/AD00000001/shifts/*", async (route) => {
    if (route.request().method() !== "DELETE") {
      await route.fallback();
      return;
    }

    if (failShiftDeleteOnce) {
      failShiftDeleteOnce = false;
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ message: "SHIFT_NOT_FOUND" }),
      });
      return;
    }

    await route.fallback();
  });

  await signIn(page);
  await onboardDefaultDevice(page, "Delete Shift Line");
  await page.getByRole("button", { name: "Parameter Configuration", exact: true }).click();

  await page.getByRole("button", { name: "+ Add Shift" }).click();
  await labeledInput(page, "Shift Name").fill("Delete Guard Shift");
  await labeledInput(page, "Start Time").fill("09:00");
  await labeledInput(page, "End Time").fill("17:00");
  await page.getByRole("button", { name: "Save Shift" }).click();

  const shiftCard = page.getByText("Delete Guard Shift").locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
  await expect(shiftCard).toBeVisible();

  const seenDialogs = [];
  page.on("dialog", async (dialog) => {
    seenDialogs.push(dialog.message());
    await dialog.accept();
  });

  await shiftCard.getByRole("button", { name: "Delete" }).click();

  await expect.poll(() => seenDialogs.some((message) => message === "Delete this shift?")).toBe(true);
  await expect.poll(() => seenDialogs.some((message) => message.includes("Failed: HTTP 404"))).toBe(true);
  await expect(shiftCard).toBeVisible();
});

test("calendar month rollover keeps stale-cost fallback truthful", async ({ page }) => {
  const harness = await installJourneyHappyPathHarness(page);
  harness.state.tariff = {
    rate: 8.5,
    currency: "INR",
    updated_at: "2026-05-02T17:30:00.000Z",
  };

  await page.route("**/backend/device/api/v1/devices/calendar/monthly-energy**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("year") !== "2026" || url.searchParams.get("month") !== "6") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        year: 2026,
        month: 6,
        currency: "INR",
        generated_at: "2026-06-01T00:00:00.000Z",
        stale: true,
        warnings: ["snapshot_unavailable"],
        cost_data_state: "stale",
        cost_data_reasons: ["snapshot_unavailable"],
        cost_generated_at: "2026-05-31T23:30:00.000Z",
        summary: {
          total_energy_kwh: 0,
          total_energy_cost_inr: 0,
        },
        days: [],
        data_quality: "degraded",
      }),
    });
  });

  await signIn(page);
  await page.getByRole("link", { name: "Calendar" }).click();
  await expect(page.getByText("Cost Live")).toBeVisible();

  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText("Cost Updating", { exact: true })).toBeVisible();
  await expect(page.getByText("Waiting for fresh INR cost snapshot")).toBeVisible();
});
