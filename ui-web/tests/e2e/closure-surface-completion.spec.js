/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installJourneyHappyPathHarness } = require("./support/journeyHappyPathHarness.js");

function modalFieldSelect(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::select[1]");
}

function modalFieldInput(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::input[1]");
}

function labeledInput(page, label) {
  return page.locator("label", { hasText: label }).locator("xpath=following-sibling::input[1]");
}

function labeledSelect(scope, label) {
  return scope.locator("label", { hasText: label }).locator("xpath=following-sibling::select[1]");
}

async function signIn(page, email = "ops@example.com", password = "FactoryOps#123") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
}

async function onboardDevice(page, name = "Closure Line") {
  await page.getByRole("button", { name: "+ Add Device" }).click();
  const modal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });
  await modal.locator('input[placeholder="e.g. Compressor Line A"]').fill(name);
  await modalFieldSelect(modal, "Plant").selectOption("plant-1");
  await modal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(modal, "Device ID Class").selectOption("active");
  await modal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await modal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await modal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Closure Bay");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();
  await expect(page.getByText("Generated Device ID")).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("link", { name: new RegExp(`${name} AD00000001`) }).click();
  await expect(page).toHaveURL(/\/machines\/AD00000001$/);
}

test("machine detail closes mqtt lifecycle, health validation/history, shift edit, and alert mutation gaps", async ({ page }) => {
  const harness = await installJourneyHappyPathHarness(page);

  await signIn(page);
  await onboardDevice(page, "Closure Line");
  await expect(page.getByRole("heading", { name: "MQTT Credential" })).toHaveCount(0);

  await page.getByRole("button", { name: "Parameter Configuration", exact: true }).click();
  const powerCard = page
    .getByRole("heading", { name: "Power", exact: true })
    .locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
  await powerCard.getByRole("button", { name: "Configure", exact: true }).click();

  let healthModal = page.getByText(/Configure Health:/).locator("xpath=ancestor::div[contains(@class,'bg-white')][1]");
  await modalFieldInput(healthModal, "Normal Min").fill("abc");
  await modalFieldInput(healthModal, "Normal Max").fill("300");
  await modalFieldInput(healthModal, "Weight (%)").fill("100");
  await healthModal.getByRole("button", { name: /^Save/ }).click();
  await expect(healthModal.getByText("Normal Min must be a finite number.")).toBeVisible();

  await modalFieldInput(healthModal, "Normal Min").fill("200");
  await healthModal.getByRole("button", { name: /^Save/ }).click();
  await expect(page.getByText("Configuration History")).toBeVisible();
  await expect(page.getByText("Created", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Last updated", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "+ Add Shift" }).click();
  await labeledInput(page, "Shift Name").fill("Editable Shift");
  await labeledInput(page, "Start Time").fill("09:00");
  await labeledInput(page, "End Time").fill("17:00");
  await labeledInput(page, "Maintenance Break (min)").fill("20");
  await page.getByRole("button", { name: "Save Shift" }).click();
  await expect(page.getByText("09:00 - 17:00")).toBeVisible();

  const shiftCard = page.getByText("Editable Shift").locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
  await shiftCard.getByRole("button", { name: "Edit" }).click();
  await labeledInput(page, "Start Time").fill("08:00");
  await labeledInput(page, "End Time").fill("16:00");
  await page.getByRole("button", { name: "Save Changes" }).click();
  await expect(page.getByText("08:00 - 16:00")).toBeVisible();

  await page.getByRole("button", { name: "Configure Rules", exact: true }).click();
  await page.getByRole("button", { name: "Add Rule" }).click();
  await page.getByLabel("Rule Name").fill("Idle longer than 45 minutes");
  await page.getByLabel("Rule Type").selectOption("continuous_idle_duration");
  await page.getByLabel("Duration (minutes)").fill("45");
  await page.getByRole("checkbox", { name: "Email" }).check();
  await page.getByLabel("Email Recipients").fill("alerts@factoryops.example");
  await page.getByRole("button", { name: "Add Email" }).click();
  await page.getByRole("button", { name: "Create Rule" }).click();

  await page.getByTitle("Machine alert history").click();
  await expect(page.getByText("Machine Alerts")).toBeVisible();
  const triggerCard = page.getByText("Idle duration alert").locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]").first();
  await triggerCard.getByRole("button", { name: "Acknowledge" }).click();
  await expect(page.getByText("Alert acknowledged.")).toBeVisible();
  await expect(page.getByText("Status: Acknowledged")).toBeVisible();

  const openTriggerCard = page.getByText("Idle duration alert").locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]").last();
  await openTriggerCard.getByRole("button", { name: "Resolve" }).click();
  await expect(page.getByText("Alert resolved.")).toBeVisible();
  await expect(page.getByText("Status: Resolved")).toBeVisible();
});

test("settings, reports, calendar, and dashboard close the remaining truthfulness gaps", async ({ page }) => {
  const harness = await installJourneyHappyPathHarness(page);
  harness.state.plants.push({
    id: "plant-2",
    tenant_id: harness.state.tenantId,
    name: "Plant South",
    location: "Nashik",
    timezone: "Asia/Kolkata",
    is_active: true,
    created_at: new Date().toISOString(),
  });
  harness.state.me.plant_ids.push("plant-2");
  harness.state.devices.push(
    {
      device_id: "AD00000020",
      device_name: "Stopped Line",
      device_type: "compressor",
      plant_id: "plant-1",
      runtime_status: "stopped",
      operational_status: "stopped",
      load_state: "stopped",
      current_band: "unknown",
      location: "Pune Bay 2",
      first_telemetry_timestamp: new Date().toISOString(),
      last_seen_timestamp: new Date().toISOString(),
    },
    {
      device_id: "AD00000021",
      device_name: "Unknown Line",
      device_type: "compressor",
      plant_id: "plant-2",
      runtime_status: "unknown",
      operational_status: "unknown",
      load_state: "unknown",
      current_band: "unknown",
      location: "Nashik Bay 1",
      first_telemetry_timestamp: null,
      last_seen_timestamp: null,
    },
  );
  harness.setDashboardCostState("stale", ["snapshot_unavailable"]);

  await signIn(page);
  await expect(page.getByRole("button", { name: /Stopped \(1\)/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Unknown \(1\)/ })).toBeVisible();
  await expect(page.getByText("Cost is stale.")).toBeVisible();

  harness.setDashboardCostState("unavailable", ["tariff_not_configured"]);
  await page.reload();
  await expect(page.getByText("Cost is unavailable until a tariff is configured.")).toBeVisible();

  await page.goto("/settings");
  await page.getByLabel("Energy Rate (per kWh)").fill("abc");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("Rate must be a valid number.")).toBeVisible();

  await page.getByLabel("Energy Rate (per kWh)").fill("8.5");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("Tariff updated")).toBeVisible();

  await page.getByLabel("Energy Rate (per kWh)").fill("9.25");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("Tariff History")).toBeVisible();
  const olderTariffRow = page.locator("div").filter({ hasText: "₹8.50 / kWh" }).first();
  await olderTariffRow.getByRole("button", { name: "Use this version" }).click();
  await expect(page.getByText("Tariff version activated")).toBeVisible();
  await expect(page.getByText("Current tariff: ₹8.50 / kWh")).toBeVisible();

  await page.goto("/reports");
  await page.getByRole("button", { name: "Schedules" }).click();
  await page.getByRole("button", { name: "New Schedule" }).click();
  const scheduleDialog = page.locator("div").filter({ has: page.getByRole("heading", { name: "Create Schedule" }) }).last();
  await expect(scheduleDialog.getByText("All Machines · 2 devices")).toBeVisible();
  await scheduleDialog.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Schedule created successfully")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "daily" }).first()).toBeVisible();

  const scheduleRow = page.getByRole("row").filter({ hasText: "consumption" }).first();
  await scheduleRow.getByRole("button", { name: "Edit" }).click();
  const editDialog = page.locator("div").filter({ has: page.getByRole("heading", { name: "Edit Schedule" }) }).last();
  await labeledSelect(editDialog, "Frequency").selectOption("weekly");
  await editDialog.getByRole("button", { name: "Save Changes" }).click();
  await expect(page.getByText("Schedule updated successfully")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "weekly" })).toBeVisible();

  await page.goto("/calendar");
  const plantFilter = page.getByLabel("Plant");
  const allPlantsTotal = await page.getByText(/^Total Energy:/).first().innerText();
  await plantFilter.selectOption("plant-2");
  await expect(plantFilter).toHaveValue("plant-2");
  await expect.poll(async () => page.getByText(/^Total Energy:/).first().innerText()).not.toBe(allPlantsTotal);
});
